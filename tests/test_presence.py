"""Face presence tests (spec sections 11, 61).

The behaviour under test that matters most: a camera failure must never be
recorded as the user being absent.
"""

from __future__ import annotations

import pytest

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.presence import FacePresenceMonitor, PresenceObservation
from focusguard_shared.constants import VisionEventType


def _monitor(absent_grace: float = 2.0, multiple_grace: float = 2.0) -> FacePresenceMonitor:
    settings = VisionSettings(
        face_absent_grace_seconds=absent_grace,
        multiple_faces_grace_seconds=multiple_grace,
        event_exit_hysteresis_seconds=0.5,
    )
    return FacePresenceMonitor(settings)


def _feed(
    monitor: FacePresenceMonitor,
    samples: list[tuple[float, int]],
    camera_ok: bool = True,
) -> list:
    events = []
    for timestamp, face_count in samples:
        events.extend(
            monitor.update(
                PresenceObservation(
                    timestamp=timestamp, camera_ok=camera_ok, face_count=face_count
                )
            )
        )
    return events


# --- face absence -----------------------------------------------------------


def test_brief_absence_is_ignored() -> None:
    """Spec section 11: under two seconds must not create an event."""
    monitor = _monitor(absent_grace=2.0)

    events = _feed(monitor, [(0.0, 1), (1.0, 0), (1.5, 0), (2.0, 1), (3.0, 1)])

    assert events == []
    assert monitor.face_absent is False


def test_sustained_absence_creates_one_event() -> None:
    monitor = _monitor(absent_grace=2.0)

    _feed(monitor, [(0.0, 1), (1.0, 0), (3.5, 0)])
    assert monitor.face_absent is True

    events = _feed(monitor, [(6.0, 1), (6.6, 1)])

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.FACE_ABSENT
    assert events[0].start_time == pytest.approx(1.0)
    assert events[0].duration_seconds == pytest.approx(5.0)


# --- multiple faces ---------------------------------------------------------


def test_multiple_faces_raises_its_own_event() -> None:
    monitor = _monitor(multiple_grace=2.0)

    _feed(monitor, [(0.0, 1), (1.0, 2), (3.5, 2)])
    assert monitor.multiple_faces is True

    events = _feed(monitor, [(5.0, 1), (5.6, 1)])

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.MULTIPLE_FACES


def test_two_faces_is_not_also_an_absence() -> None:
    monitor = _monitor()
    _feed(monitor, [(0.0, 2), (3.0, 2)])

    assert monitor.multiple_faces is True
    assert monitor.face_absent is False


# --- camera failure: the central guarantee ----------------------------------


def test_camera_failure_never_produces_a_face_absent_event() -> None:
    """A locked phone or dropped Wi-Fi is a technical fault, not a distraction.

    This is the regression guard for the whole design. A detector handed no
    frames finds no face; without this rule, every camera dropout would cost the
    user focus points.
    """
    monitor = _monitor(absent_grace=2.0)

    _feed(monitor, [(0.0, 1), (1.0, 1)])

    # Camera dies for a long time - far longer than the absence grace period.
    events = _feed(monitor, [(2.0, 0), (10.0, 0), (30.0, 0)], camera_ok=False)

    assert events == []
    assert monitor.suspended is True
    assert monitor.face_absent is False


def test_monitoring_resumes_after_the_camera_recovers() -> None:
    monitor = _monitor(absent_grace=2.0)

    _feed(monitor, [(0.0, 1)])
    _feed(monitor, [(1.0, 0), (20.0, 0)], camera_ok=False)
    assert monitor.suspended is True

    _feed(monitor, [(21.0, 1), (22.0, 1)])
    assert monitor.suspended is False
    assert monitor.face_absent is False


def test_absence_after_recovery_is_timed_from_recovery_not_the_outage() -> None:
    """The outage must not be back-charged to the user once the camera returns."""
    monitor = _monitor(absent_grace=2.0)

    _feed(monitor, [(0.0, 1)])
    _feed(monitor, [(1.0, 0), (50.0, 0)], camera_ok=False)

    # Camera is back and genuinely nobody is there.
    _feed(monitor, [(51.0, 0), (54.0, 0)])
    events = _feed(monitor, [(55.0, 1), (55.6, 1)])

    assert len(events) == 1
    event = events[0]
    assert event.start_time == pytest.approx(51.0), "must not start during the outage"
    assert event.duration_seconds == pytest.approx(4.0)


def test_a_confirmed_event_survives_a_later_camera_failure() -> None:
    """Evidence gathered before the outage is sound and must still be reported."""
    monitor = _monitor(absent_grace=2.0)

    _feed(monitor, [(0.0, 1), (1.0, 0), (5.0, 0)])
    assert monitor.face_absent is True

    events = _feed(monitor, [(6.0, 0)], camera_ok=False)

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.FACE_ABSENT
    assert events[0].end_time == pytest.approx(5.0), "closed at the last good frame"


def test_face_present_requires_a_working_camera() -> None:
    assert PresenceObservation(timestamp=0.0, camera_ok=False, face_count=0).face_present is False
    assert PresenceObservation(timestamp=0.0, camera_ok=False, face_count=1).face_present is False
    assert PresenceObservation(timestamp=0.0, camera_ok=True, face_count=0).face_present is False
    assert PresenceObservation(timestamp=0.0, camera_ok=True, face_count=1).face_present is True


# --- session end ------------------------------------------------------------


def test_flush_closes_an_absence_in_progress() -> None:
    monitor = _monitor(absent_grace=2.0)
    _feed(monitor, [(0.0, 1), (1.0, 0), (5.0, 0)])

    events = monitor.flush(8.0)

    assert len(events) == 1
    assert events[0].duration_seconds == pytest.approx(7.0)
