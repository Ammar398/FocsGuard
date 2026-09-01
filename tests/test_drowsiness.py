"""Drowsiness tests (spec sections 13, 61).

EYES_CLOSED is a measurement; DROWSINESS is an interpretation of it, and
interpretations need more evidence. These tests pin both the trigger and the
confidence that goes with it.
"""

from __future__ import annotations

import pytest

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.drowsiness import DrowsinessMonitor, DrowsinessObservation
from focusguard_shared.constants import VisionEventType, resolve_superseded


def _monitor(
    drowsy_seconds: float = 2.5,
    perclos_threshold: float = 0.15,
    slow_blink_count: int = 3,
) -> DrowsinessMonitor:
    return DrowsinessMonitor(
        VisionSettings(
            drowsy_duration_seconds=drowsy_seconds,
            perclos_threshold=perclos_threshold,
            drowsy_slow_blink_count=slow_blink_count,
            blink_max_duration_seconds=0.4,
            event_exit_hysteresis_seconds=0.0,
            pitch_down_threshold_degrees=20.0,
        )
    )


def _feed(
    monitor: DrowsinessMonitor,
    samples: list[tuple[float, bool]],
    *,
    camera_ok: bool = True,
    face_present: bool = True,
    head_pitch: float | None = None,
    eye_confidence: float = 0.95,
) -> list:
    events = []
    for timestamp, closed in samples:
        events.extend(
            monitor.update(
                DrowsinessObservation(
                    timestamp=timestamp,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    eyes_closed=closed,
                    eye_confidence=eye_confidence,
                    head_pitch=head_pitch,
                )
            )
        )
    return events


def _steady(start: float, end: float, closed: bool, step: float = 0.1):
    """Evenly spaced samples, matching the ~12 Hz the pipeline actually runs at."""
    samples = []
    time = start
    while time <= end + 1e-9:
        samples.append((round(time, 3), closed))
        time += step
    return samples


# --- the trigger ------------------------------------------------------------


def test_a_blink_is_never_drowsiness() -> None:
    monitor = _monitor(drowsy_seconds=2.5)

    events = _feed(monitor, [(0.0, False), (1.0, True), (1.2, False), (2.0, False)])

    assert events == []
    assert monitor.is_drowsy is False


def test_a_one_second_closure_is_not_drowsiness() -> None:
    """That is EYES_CLOSED. Drowsiness demands a much longer closure."""
    monitor = _monitor(drowsy_seconds=2.5)

    _feed(monitor, _steady(0.0, 1.0, closed=True))
    assert monitor.is_drowsy is False


def test_a_long_closure_triggers_drowsiness() -> None:
    monitor = _monitor(drowsy_seconds=2.5)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _steady(1.0, 4.5, closed=True))
    assert monitor.is_drowsy is True

    events = _feed(monitor, [(5.0, False), (5.1, False)])

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.DROWSINESS


def test_the_event_starts_when_the_eyes_closed_not_when_it_was_confirmed() -> None:
    """A 4-second closure is a 4-second event, not a 1.5-second one."""
    monitor = _monitor(drowsy_seconds=2.5)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _steady(1.0, 5.0, closed=True))
    events = _feed(monitor, [(5.0, False), (5.1, False)])

    assert events[0].start_time == pytest.approx(1.0)
    assert events[0].duration_seconds == pytest.approx(4.0)


# --- PERCLOS ----------------------------------------------------------------


def test_perclos_measures_the_share_of_time_eyes_are_closed() -> None:
    monitor = _monitor()

    _feed(monitor, _steady(0.0, 8.0, closed=False))
    _feed(monitor, _steady(8.1, 10.0, closed=True))

    perclos = monitor.perclos(now=10.0)
    assert perclos == pytest.approx(0.19, abs=0.06)


def test_perclos_is_zero_for_permanently_open_eyes() -> None:
    monitor = _monitor()
    _feed(monitor, _steady(0.0, 10.0, closed=False))
    assert monitor.perclos(now=10.0) == 0.0


def test_perclos_ignores_gaps_when_monitoring_was_suspended() -> None:
    """Camera downtime must not be recorded as time spent with eyes closed."""
    monitor = _monitor()

    _feed(monitor, _steady(0.0, 2.0, closed=False))
    # A 30-second hole, then monitoring resumes.
    _feed(monitor, _steady(32.0, 34.0, closed=False))

    assert monitor.perclos(now=34.0) == 0.0


def test_many_short_closures_raise_perclos() -> None:
    """The pattern a single-closure test misses: someone fighting sleep.

    Half-second closures never reach the 2.5 s threshold individually, yet they
    add up to a large share of the window.
    """
    monitor = _monitor()

    for index in range(20):
        base = index * 1.0
        _feed(monitor, _steady(base, base + 0.4, closed=True))
        _feed(monitor, _steady(base + 0.5, base + 0.9, closed=False))

    assert monitor.perclos(now=20.0) > 0.15


# --- corroboration raises confidence ---------------------------------------


def test_an_uncorroborated_closure_is_reported_with_lower_confidence() -> None:
    """One long closure is decent evidence, but not proof of drowsiness."""
    monitor = _monitor()

    _feed(monitor, _steady(0.0, 1.0, closed=False, step=0.5))
    _feed(monitor, _steady(1.1, 4.5, closed=True))
    events = _feed(monitor, [(5.0, False), (5.1, False)])

    assert len(events) == 1
    assert 0.6 < events[0].confidence < 0.85


def test_corroborating_signals_raise_confidence() -> None:
    """The same closure, but with head down and elevated PERCLOS."""
    plain = _monitor()
    _feed(plain, _steady(0.0, 1.0, closed=False, step=0.5))
    _feed(plain, _steady(1.1, 4.5, closed=True))
    plain_events = _feed(plain, [(5.0, False), (5.1, False)])

    corroborated = _monitor()
    _feed(corroborated, _steady(0.0, 1.0, closed=False, step=0.5), head_pitch=-30.0)
    _feed(corroborated, _steady(1.1, 4.5, closed=True), head_pitch=-30.0)
    rich_events = _feed(corroborated, [(5.0, False), (5.1, False)], head_pitch=-30.0)

    assert rich_events[0].confidence > plain_events[0].confidence


def test_a_poor_eye_reading_lowers_drowsiness_confidence() -> None:
    """Uncertainty in the measurement must propagate to the interpretation."""
    monitor = _monitor()

    _feed(monitor, [(0.0, False)], eye_confidence=0.5)
    _feed(monitor, _steady(1.0, 4.5, closed=True), eye_confidence=0.5)
    events = _feed(monitor, [(5.0, False), (5.1, False)], eye_confidence=0.5)

    assert events[0].confidence < 0.5


def test_indicators_are_recorded_for_explanation() -> None:
    """A user must never be told they looked drowsy without being shown why."""
    monitor = _monitor()

    _feed(monitor, _steady(0.0, 5.0, closed=True), head_pitch=-35.0)

    indicators = monitor.indicators
    assert indicators.head_down is True
    assert any("head down" in reason for reason in indicators.corroborating)


def test_head_up_is_not_counted_as_corroboration() -> None:
    monitor = _monitor()
    _feed(monitor, _steady(0.0, 5.0, closed=True), head_pitch=0.0)
    assert monitor.indicators.head_down is False


# --- slow closures ----------------------------------------------------------


def test_slow_closures_are_counted_between_blink_and_drowsy_thresholds() -> None:
    """Sluggish blinks fall between the blink counter and the closure detector."""
    monitor = _monitor()

    for index in range(4):
        base = index * 5.0
        _feed(monitor, [(base, False)])
        _feed(monitor, _steady(base + 0.5, base + 1.7, closed=True))
        _feed(monitor, [(base + 1.8, False)])

    assert monitor.slow_closure_count(now=20.0) == 4


def test_a_fast_blink_is_not_a_slow_closure() -> None:
    monitor = _monitor()
    _feed(monitor, [(0.0, False), (1.0, True), (1.2, False)])
    assert monitor.slow_closure_count(now=2.0) == 0


# --- unusable data ----------------------------------------------------------


def test_a_camera_failure_never_produces_drowsiness() -> None:
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    events = _feed(monitor, _steady(1.0, 40.0, closed=False, step=1.0), camera_ok=False)

    assert events == []
    assert monitor.is_drowsy is False
    assert monitor.suspended is True


def test_a_missing_face_suspends_drowsiness_monitoring() -> None:
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    events = _feed(monitor, _steady(1.0, 30.0, closed=False, step=1.0), face_present=False)

    assert events == []
    assert monitor.suspended is True


# --- no double counting -----------------------------------------------------


def test_drowsiness_supersedes_eyes_closed_for_scoring() -> None:
    """One closure must not be charged twice.

    DROWSINESS always overlaps EYES_CLOSED because the same closure produced
    both. Summing the weights (40 + 10) would exceed what either is meant to cost.
    """
    active = {VisionEventType.DROWSINESS, VisionEventType.EYES_CLOSED}
    resolved = resolve_superseded(active)

    assert resolved == {VisionEventType.DROWSINESS}


def test_unrelated_events_are_not_suppressed() -> None:
    active = {
        VisionEventType.DROWSINESS,
        VisionEventType.EYES_CLOSED,
        VisionEventType.PHONE_VISIBLE,
    }
    assert resolve_superseded(active) == {
        VisionEventType.DROWSINESS,
        VisionEventType.PHONE_VISIBLE,
    }


def test_eyes_closed_alone_is_kept() -> None:
    active = {VisionEventType.EYES_CLOSED}
    assert resolve_superseded(active) == {VisionEventType.EYES_CLOSED}
