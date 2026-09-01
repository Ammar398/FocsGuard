"""Agent ingest endpoints (spec sections 33, 36, 37).

    POST /api/v1/agent/events      upload a batch
    POST /api/v1/agent/heartbeat   report liveness

WHAT ARRIVES HERE
-----------------
Structured events and aggregate scores. Never frames, never screenshots, never
input text (section 33). The schema has nowhere to put them, so a compromised
agent could not upload one even deliberately.

RETRIES ARE EXPECTED, NOT EXCEPTIONAL
-------------------------------------
An agent that loses its connection mid-upload does not know whether the batch
landed. Section 37 says it should simply send again, so every event carries a
client-generated uuid and the repository skips any it already has.

The response reports duplicates separately from accepted rather than hiding
them: a healthy agent recovering from a dropped connection will see a non-zero
count, and that is success.

AUTHENTICATION
--------------
Phase 12 identifies the device by its uuid and nothing more. Phase 13 adds the
device credential that must accompany it - the dependency below is the single
place that changes, which is why it exists now rather than being inlined.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import (
    EventBatchUpload,
    HeartbeatUpload,
    IngestResult,
)
from focusguard_backend.database.session import get_db
from focusguard_backend.models.identity import Device
from focusguard_backend.models.telemetry import DeviceHeartbeat
from focusguard_backend.repositories.telemetry import Scope, TelemetryRepository
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])


def resolve_device(device_uuid: str, db: Session) -> Device:
    """Find the device sending this data.

    Phase 13 replaces this with a credential check. Keeping it in one function
    means that change touches one place, and every endpoint below inherits it.
    """
    device = db.scalar(select(Device).where(Device.device_uuid == device_uuid))

    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown device. Enrol it before uploading.",
        )
    if not device.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This device has been deactivated.",
        )
    if device.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device is not linked to a user.",
        )
    return device


@router.post("/events", response_model=IngestResult)
def ingest_events(batch: EventBatchUpload, db: Session = Depends(get_db)) -> IngestResult:
    """Accept a batch of events from an agent."""
    device = resolve_device(batch.device_uuid, db)
    repository = TelemetryRepository(db)

    # The batch carries its own session, so an agent that ran offline can upload
    # a complete finished session in one request with no prior round trip.
    session = repository.create_session(
        session_uuid=batch.session.session_uuid,
        user_id=device.user_id,
        name=batch.session.name,
        started_at=batch.session.started_at,
        device_id=device.id,
        organization_id=device.organization_id,
        task_name=batch.session.task_name,
        task_category=batch.session.task_category,
        agent_version=batch.agent_version,
    )

    vision_accepted, vision_duplicates = repository.ingest_vision_events(
        session, [event.model_dump() for event in batch.vision_events]
    )
    activity_accepted, activity_duplicates = repository.ingest_activity_events(
        session, [event.model_dump() for event in batch.activity_events]
    )
    scores_accepted = repository.ingest_scores(
        session, [score.model_dump() for score in batch.focus_scores]
    )

    if batch.session.ended_at is not None:
        repository.complete_session(
            session,
            ended_at=batch.session.ended_at,
            duration_seconds=batch.session.duration_seconds,
            focused_seconds=batch.session.focused_seconds,
            distracted_seconds=batch.session.distracted_seconds,
            average_focus_score=batch.session.average_focus_score,
        )

    device.last_seen_at = datetime.now(UTC)
    device.agent_version = batch.agent_version
    db.commit()

    total = vision_accepted + activity_accepted + scores_accepted
    if total or vision_duplicates or activity_duplicates:
        logger.info(
            "Device %s: %d accepted, %d duplicates skipped",
            batch.device_uuid,
            total,
            vision_duplicates + activity_duplicates,
        )

    return IngestResult(
        session_id=session.id,
        accepted={
            "vision_events": vision_accepted,
            "activity_events": activity_accepted,
            "focus_scores": scores_accepted,
        },
        duplicates={
            "vision_events": vision_duplicates,
            "activity_events": activity_duplicates,
        },
    )


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(payload: HeartbeatUpload, db: Session = Depends(get_db)) -> None:
    """Report that a device is alive (spec section 36).

    Cheap and frequent, so the manager board can show who is working without
    holding a WebSocket open to every machine.
    """
    device = resolve_device(payload.device_uuid, db)
    now = datetime.now(UTC)

    session_id = None
    if payload.session_uuid:
        found = TelemetryRepository(db).get_session_by_uuid(
            payload.session_uuid, scope=Scope.for_user(device.user_id)
        )
        session_id = found.id if found else None

    db.add(
        DeviceHeartbeat(
            device_id=device.id,
            reported_at=now,
            session_id=session_id,
            pending_events=payload.pending_events,
        )
    )
    device.last_seen_at = now
    if payload.agent_version:
        device.agent_version = payload.agent_version

    db.commit()
