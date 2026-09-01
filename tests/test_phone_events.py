"""Phone event tests (spec section 16).

The requirement under test: *a continuous phone appearance should create ONE
event*. Detection at 3 Hz over 30 seconds produces ~90 positive frames, and
object detection flickers, so both over-counting and splitting have to be
prevented.
"""

from __future__ import annotations

import pytest

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.phone import PhoneMonitor, PhoneObservation
from focusguard_shared.constants import VisionEventType


def _monitor(grace: float = 1.5, merge_gap: float = 2.0) -> PhoneMonitor:
    return PhoneMonitor(
        VisionSettings(phone_grace_seconds=grace, phone_merge_gap_seconds=merge_gap)
    )


def _feed(
    monitor: PhoneMonitor,
    samples: list[tuple[float, bool]],
    *,
    camera_ok: bool = True,
    confidence: float = 0.8,
) -> list:
    events = []
    for timestamp, detected in samples:
        events.extend(
            monitor.update(
                PhoneObservation(
                    timestamp=timestamp,
                    camera_ok=camera_ok,
                    detected=detected,
                    confidence=confidence,
                )
            )
        )
    return events


def _stream(start: float, end: float, detected: bool, hz: float = 3.0):
    """Samples at the real detection rate, ~3 Hz."""
    step = 1.0 / hz
    samples = []
    time = start
    while time <= end + 1e-9:
        samples.append((round(time, 3), detected))
        time += step
    return samples


# --- one event per appearance ----------------------------------------------


def test_thirty_seconds_of_phone_produces_exactly_one_event() -> None:
    """The core requirement. At 3 Hz that is ~90 positive frames."""
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 31.0, detected=True))
    events = _feed(monitor, _stream(31.5, 34.0, detected=False))

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.PHONE_VISIBLE
    assert events[0].duration_seconds == pytest.approx(30.0, abs=0.5)
    assert monitor.event_count == 1


def test_detection_flicker_does_not_split_one_appearance() -> None:
    """A hand across the phone, or a reflection, drops detection for a moment."""
    monitor = _monitor(merge_gap=2.0)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 10.0, detected=True))
    _feed(monitor, [(10.4, False), (10.8, False)])  # 0.8s dropout
    _feed(monitor, _stream(11.0, 20.0, detected=True))
    events = _feed(monitor, _stream(20.5, 23.0, detected=False))

    assert len(events) == 1, "one appearance, not two"
    assert events[0].duration_seconds == pytest.approx(19.5, abs=1.0)


def test_a_genuine_second_appearance_is_a_separate_event() -> None:
    monitor = _monitor(merge_gap=2.0)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 6.0, detected=True))
    _feed(monitor, _stream(6.5, 20.0, detected=False))  # long gap
    _feed(monitor, _stream(21.0, 26.0, detected=True))
    _feed(monitor, _stream(26.5, 30.0, detected=False))

    assert monitor.event_count == 2


def test_a_glimpsed_phone_is_not_an_event() -> None:
    """Reaching past the desk brings a phone into frame for a moment."""
    monitor = _monitor(grace=1.5)

    events = _feed(monitor, [(0.0, False), (1.0, True), (1.6, False), (4.0, False)])

    assert events == []
    assert monitor.event_count == 0


# --- reported figures -------------------------------------------------------


def test_total_phone_time_accumulates_across_appearances() -> None:
    """Feeds the "phone: 2 min" figure on the dashboard."""
    monitor = _monitor()

    for index in range(3):
        base = index * 40.0
        _feed(monitor, [(base, False)])
        _feed(monitor, _stream(base + 1.0, base + 11.0, detected=True))
        _feed(monitor, _stream(base + 11.5, base + 15.0, detected=False))

    assert monitor.event_count == 3
    assert monitor.total_seconds == pytest.approx(30.0, abs=2.0)


def test_current_duration_is_reported_while_the_phone_is_visible() -> None:
    monitor = _monitor(grace=1.5)

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 5.0, detected=True))

    assert monitor.phone_visible is True
    assert monitor.current_duration(10.0) == pytest.approx(9.0, abs=0.5)


def test_confidence_is_the_mean_across_the_appearance() -> None:
    """A single confident frame must not make a whole event look certain."""
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 5.0, detected=True), confidence=0.95)
    _feed(monitor, _stream(5.5, 9.0, detected=True), confidence=0.55)
    events = _feed(monitor, _stream(9.5, 12.0, detected=False))

    assert len(events) == 1
    assert 0.6 < events[0].confidence < 0.9


# --- camera failure ---------------------------------------------------------


def test_a_camera_failure_closes_an_open_appearance() -> None:
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 10.0, detected=True))
    assert monitor.phone_visible is True

    events = _feed(monitor, [(11.0, False)], camera_ok=False)

    assert len(events) == 1
    assert monitor.suspended is True


def test_no_phone_events_are_invented_while_the_camera_is_down() -> None:
    monitor = _monitor()
    events = _feed(monitor, _stream(0.0, 30.0, detected=True), camera_ok=False)

    assert events == []
    assert monitor.event_count == 0


def test_phone_detection_does_not_require_a_visible_face() -> None:
    """A user holding a phone below the desk may be out of frame themselves.

    Unlike the eye and gaze monitors, this one only needs a working camera.
    """
    observation = PhoneObservation(timestamp=0.0, camera_ok=True, detected=True)
    assert observation.usable is True


# --- session end ------------------------------------------------------------


def test_flush_closes_an_appearance_in_progress() -> None:
    monitor = _monitor()

    _feed(monitor, [(0.0, False)])
    _feed(monitor, _stream(1.0, 10.0, detected=True))
    events = monitor.flush(12.0)

    assert len(events) == 1
    assert events[0].duration_seconds == pytest.approx(11.0, abs=0.5)
