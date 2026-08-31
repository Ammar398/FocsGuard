"""Event contract tests (spec sections 17, 33, 37)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from focusguard_shared.constants import VisionEventType
from focusguard_shared.schemas import EventBatch, VisionEvent, VisionFrameObservation

NOW = datetime(2026, 8, 31, 19, 0, 0, tzinfo=UTC)


def _event(**overrides: object) -> VisionEvent:
    payload: dict[str, object] = {
        "session_id": "s-1",
        "event_type": VisionEventType.PHONE_VISIBLE,
        "start_time": NOW,
        "end_time": NOW + timedelta(seconds=30),
        "duration_seconds": 30.0,
        "confidence": 0.86,
    }
    payload.update(overrides)
    return VisionEvent(**payload)  # type: ignore[arg-type]


def test_every_event_gets_a_unique_uuid() -> None:
    """Client-generated UUIDs are what make retried uploads idempotent."""
    assert _event().event_uuid != _event().event_uuid


def test_end_time_cannot_precede_start_time() -> None:
    with pytest.raises(ValidationError):
        _event(end_time=NOW - timedelta(seconds=5))


def test_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValidationError):
        _event(confidence=1.4)
    with pytest.raises(ValidationError):
        _event(confidence=-0.1)


def test_duration_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        _event(duration_seconds=-1.0)


def test_unknown_fields_are_rejected() -> None:
    """An agent sending a field the server does not understand must fail loudly."""
    with pytest.raises(ValidationError):
        _event(screenshot_path="C:/secret.png")


def test_camera_failure_is_not_face_absence() -> None:
    """The scoring-correctness guarantee from CURRENT_STATE.md section 2.4.

    A dead camera reports zero faces. Reading that as an absent user would
    penalise someone whose phone simply locked.
    """
    observation = VisionFrameObservation(timestamp=1.0, camera_ok=False, face_count=0)
    assert observation.face_present is False

    present = VisionFrameObservation(timestamp=1.0, camera_ok=True, face_count=1)
    assert present.face_present is True

    # Zero faces with a healthy camera is genuine absence.
    absent = VisionFrameObservation(timestamp=1.0, camera_ok=True, face_count=0)
    assert absent.face_present is False


def test_batch_counts_all_record_types() -> None:
    batch = EventBatch(
        device_id="d-1",
        session_id="s-1",
        agent_version="0.1.0",
        vision_events=[_event(), _event()],
    )
    assert batch.total_records == 2


def test_empty_batch_is_valid() -> None:
    """A heartbeat batch with nothing to report must still be accepted."""
    batch = EventBatch(device_id="d-1", session_id="s-1", agent_version="0.1.0")
    assert batch.total_records == 0
