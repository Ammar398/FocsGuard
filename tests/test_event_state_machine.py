"""Event state machine tests (spec sections 11, 14, 16, 17).

Every behavioural event in FocusGuard is produced by this one class, so these
tests cover the grace-period, hysteresis and timing rules for the whole system.
No camera, no clock, no database - just booleans and timestamps.
"""

from __future__ import annotations

import pytest

from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import EventState, VisionEventType


def _machine(grace: float = 3.0, hysteresis: float = 0.5, minimum: float = 0.0):
    return EventStateMachine(
        event_type=VisionEventType.GAZE_AWAY,
        grace_seconds=grace,
        exit_hysteresis_seconds=hysteresis,
        min_duration_seconds=minimum,
    )


# --- the grace period -------------------------------------------------------


def test_brief_condition_emits_nothing() -> None:
    """A one-second glance away must not cost anything (spec section 14)."""
    machine = _machine(grace=3.0)

    assert machine.update(True, 0.0) is None
    assert machine.update(True, 0.5) is None
    assert machine.state is EventState.CANDIDATE

    assert machine.update(False, 1.0) is None
    assert machine.state is EventState.IDLE
    assert machine.is_active is False


def test_sustained_condition_becomes_active_after_the_grace_period() -> None:
    machine = _machine(grace=3.0)

    machine.update(True, 0.0)
    machine.update(True, 2.9)
    assert machine.state is EventState.CANDIDATE, "still inside the grace period"

    machine.update(True, 3.0)
    assert machine.state is EventState.ACTIVE
    assert machine.is_active is True


def test_event_starts_when_the_condition_did_not_when_it_was_confirmed() -> None:
    """A 6s look-away with a 3s grace is a 6s event, not a 3s one.

    The grace period decides whether to believe the signal; it does not change
    when the behaviour began.
    """
    machine = _machine(grace=3.0, hysteresis=0.5)

    machine.update(True, 10.0)
    machine.update(True, 13.0)  # ACTIVE
    machine.update(True, 16.0)
    machine.update(False, 16.0)
    event = machine.update(False, 16.6)

    assert event is not None
    assert event.start_time == pytest.approx(10.0)
    assert event.end_time == pytest.approx(16.0)
    assert event.duration_seconds == pytest.approx(6.0)


# --- hysteresis on exit -----------------------------------------------------


def test_flicker_does_not_split_one_event_in_two() -> None:
    """Spec section 16: a continuous phone appearance produces exactly ONE event."""
    machine = _machine(grace=1.0, hysteresis=2.0)

    machine.update(True, 0.0)
    machine.update(True, 1.0)  # ACTIVE
    assert machine.is_active

    # Detector drops out for a moment, then recovers inside the hysteresis window.
    assert machine.update(False, 5.0) is None
    assert machine.update(True, 5.3) is None
    assert machine.is_active, "a momentary dropout must not close the event"

    machine.update(False, 10.0)
    event = machine.update(False, 12.1)

    assert event is not None
    assert event.duration_seconds == pytest.approx(10.0)


def test_event_ends_when_the_condition_stopped_not_when_hysteresis_expired() -> None:
    """Otherwise every event would be inflated by the hysteresis duration."""
    machine = _machine(grace=1.0, hysteresis=2.0)

    machine.update(True, 0.0)
    machine.update(True, 1.0)
    machine.update(False, 8.0)
    event = machine.update(False, 10.5)

    assert event is not None
    assert event.end_time == pytest.approx(8.0)


# --- confidence -------------------------------------------------------------


def test_confidence_is_the_mean_not_the_peak() -> None:
    """A single confident frame must not make a whole event look certain."""
    machine = _machine(grace=0.0, hysteresis=0.0)

    machine.update(True, 0.0, confidence=0.9)
    machine.update(True, 1.0, confidence=0.5)
    machine.update(True, 2.0, confidence=0.4)
    machine.update(False, 3.0)
    event = machine.update(False, 3.1)

    assert event is not None
    assert event.confidence == pytest.approx(0.6, abs=1e-6)


# --- minimum duration -------------------------------------------------------


def test_events_below_the_minimum_duration_are_discarded() -> None:
    """Used to separate a blink from an EYES_CLOSED event (spec section 12)."""
    machine = _machine(grace=0.0, hysteresis=0.0, minimum=1.0)

    machine.update(True, 0.0)
    machine.update(False, 0.3)
    assert machine.update(False, 0.4) is None, "0.3s is a blink, not an event"

    machine.update(True, 10.0)
    machine.update(True, 12.0)
    machine.update(False, 12.0)
    event = machine.update(False, 12.1)
    assert event is not None
    assert event.duration_seconds == pytest.approx(2.0)


# --- flush and reset --------------------------------------------------------


def test_flush_closes_an_open_event() -> None:
    """Ending a session mid-distraction must not silently discard it."""
    machine = _machine(grace=1.0)
    machine.update(True, 0.0)
    machine.update(True, 2.0)

    event = machine.flush(5.0)
    assert event is not None
    assert event.duration_seconds == pytest.approx(5.0)
    assert machine.state is EventState.IDLE


def test_flush_emits_nothing_for_an_unconfirmed_candidate() -> None:
    machine = _machine(grace=3.0)
    machine.update(True, 0.0)
    assert machine.flush(1.0) is None


def test_reset_discards_without_emitting() -> None:
    machine = _machine(grace=1.0)
    machine.update(True, 0.0)
    machine.update(True, 2.0)
    assert machine.is_active

    machine.reset()
    assert machine.state is EventState.IDLE
    assert machine.is_active is False


# --- reporting --------------------------------------------------------------


def test_current_duration_tracks_an_active_event() -> None:
    machine = _machine(grace=1.0)
    machine.update(True, 100.0)
    machine.update(True, 101.0)
    assert machine.current_duration(105.0) == pytest.approx(5.0)


def test_current_duration_is_zero_when_idle() -> None:
    assert _machine().current_duration(50.0) == 0.0


def test_behaviour_event_duration_is_never_negative() -> None:
    event = BehaviorEvent(
        event_type=VisionEventType.FACE_ABSENT,
        start_time=10.0,
        end_time=9.0,
        confidence=1.0,
    )
    assert event.duration_seconds == 0.0


def test_repeated_cycles_produce_separate_events() -> None:
    machine = _machine(grace=1.0, hysteresis=0.5)
    emitted = []

    for offset in (0.0, 100.0):
        machine.update(True, offset)
        machine.update(True, offset + 2.0)
        machine.update(False, offset + 5.0)
        event = machine.update(False, offset + 5.6)
        if event is not None:
            emitted.append(event)

    assert len(emitted) == 2
    assert all(e.duration_seconds == pytest.approx(5.0) for e in emitted)
