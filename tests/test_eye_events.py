"""Blink and eye-closure event tests (spec sections 12, 13).

The distinction under test: a blink costs nothing, a sustained closure is an
event. Getting it wrong either way is bad - penalise blinks and a user loses
points for being alive; ignore duration and someone asleep scores as focused.
"""

from __future__ import annotations

import pytest

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.eyes import EyeMonitor, EyeObservation
from focusguard_shared.constants import VisionEventType


def _monitor(grace: float = 1.0, blink_max: float = 0.4) -> EyeMonitor:
    return EyeMonitor(
        VisionSettings(
            eyes_closed_grace_seconds=grace,
            blink_max_duration_seconds=blink_max,
            event_exit_hysteresis_seconds=0.0,
        )
    )


def _feed(
    monitor: EyeMonitor,
    samples: list[tuple[float, bool]],
    *,
    camera_ok: bool = True,
    face_present: bool = True,
    valid: bool = True,
    confidence: float = 0.95,
) -> list:
    events = []
    for timestamp, closed in samples:
        events.extend(
            monitor.update(
                EyeObservation(
                    timestamp=timestamp,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    eyes_closed=closed,
                    ear_average=0.10 if closed else 0.30,
                    confidence=confidence,
                    valid=valid,
                )
            )
        )
    return events


# --- blinks cost nothing ----------------------------------------------------


def test_a_normal_blink_produces_no_event() -> None:
    """Everybody blinks 15-20 times a minute; it must never cost focus."""
    monitor = _monitor(grace=1.0, blink_max=0.4)

    events = _feed(monitor, [(0.0, False), (1.0, True), (1.2, False), (2.0, False)])

    assert events == []
    assert monitor.eyes_closed is False


def test_blinks_are_counted() -> None:
    monitor = _monitor()

    for index in range(5):
        base = index * 3.0
        _feed(monitor, [(base, False), (base + 0.5, True), (base + 0.7, False)])

    assert monitor.blink_count == 5


def test_blink_rate_is_reported_per_minute() -> None:
    """Phase 5 reads this rate rather than measuring drowsiness separately."""
    monitor = _monitor()

    for index in range(10):
        base = index * 3.0
        _feed(monitor, [(base, False), (base + 0.5, True), (base + 0.7, False)])

    rate = monitor.blink_rate_per_minute(now=30.0)
    assert rate > 0
    assert rate == pytest.approx(22.0, abs=8.0)


def test_blink_rate_is_zero_before_any_blinks() -> None:
    assert _monitor().blink_rate_per_minute(now=100.0) == 0.0


def test_a_single_blink_does_not_extrapolate_to_sixty_per_minute() -> None:
    """A one-second window would otherwise report an absurd rate."""
    monitor = _monitor()
    _feed(monitor, [(0.0, False), (0.1, True), (0.3, False)])
    assert monitor.blink_rate_per_minute(now=0.4) == 0.0


# --- sustained closure is an event -----------------------------------------


def test_a_long_closure_produces_an_eyes_closed_event() -> None:
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False), (1.0, True), (2.5, True)])
    assert monitor.eyes_closed is True

    events = _feed(monitor, [(3.0, False), (3.1, False)])

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.EYES_CLOSED
    assert events[0].duration_seconds == pytest.approx(2.0)


def test_a_long_closure_is_not_counted_as_a_blink() -> None:
    """Otherwise resting your eyes would look like rapid blinking."""
    monitor = _monitor(grace=1.0, blink_max=0.4)

    _feed(monitor, [(0.0, False), (1.0, True), (4.0, True), (4.1, False)])

    assert monitor.blink_count == 0


def test_event_confidence_carries_through_from_the_detector() -> None:
    """Disagreement between EAR and blendshapes must reach the scoring engine."""
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False)], confidence=0.5)
    _feed(monitor, [(1.0, True), (3.0, True)], confidence=0.5)
    events = _feed(monitor, [(3.5, False), (3.6, False)], confidence=0.5)

    assert len(events) == 1
    assert events[0].confidence == pytest.approx(0.5)


# --- unusable data attributes nothing ---------------------------------------


def test_a_missing_face_suspends_rather_than_reporting_closed_eyes() -> None:
    """No face means no information about the eyes - not evidence they are shut."""
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False)])
    events = _feed(monitor, [(1.0, False), (10.0, False)], face_present=False)

    assert events == []
    assert monitor.eyes_closed is False
    assert monitor.suspended is True


def test_a_camera_failure_suspends_eye_monitoring() -> None:
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False)])
    events = _feed(monitor, [(1.0, False), (20.0, False)], camera_ok=False)

    assert events == []
    assert monitor.suspended is True


def test_unmeasurable_eyes_suspend_monitoring() -> None:
    """Sitting too far back makes EAR meaningless; it is not a closed eye."""
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False)])
    events = _feed(monitor, [(1.0, False), (10.0, False)], valid=False)

    assert events == []
    assert monitor.suspended is True


def test_monitoring_resumes_after_the_face_returns() -> None:
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, [(1.0, False)], face_present=False)
    assert monitor.suspended is True

    _feed(monitor, [(5.0, False)])
    assert monitor.suspended is False


def test_a_confirmed_closure_is_emitted_when_the_face_disappears() -> None:
    """Evidence gathered while the face was visible is still sound."""
    monitor = _monitor(grace=1.0)

    _feed(monitor, [(0.0, False), (1.0, True), (3.0, True)])
    assert monitor.eyes_closed is True

    events = _feed(monitor, [(3.5, False)], face_present=False)

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.EYES_CLOSED


# --- session end ------------------------------------------------------------


def test_flush_closes_a_closure_in_progress() -> None:
    monitor = _monitor(grace=1.0)
    _feed(monitor, [(0.0, False), (1.0, True), (3.0, True)])

    events = monitor.flush(5.0)

    assert len(events) == 1
    assert events[0].duration_seconds == pytest.approx(4.0)
