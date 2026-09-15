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

AUTHENTICATION (Phase 13)
-------------------------
Every route here requires a **device credential**, issued at enrolment and sent
in `X-Device-Secret`. The uuid in the payload is no longer identity - it is
checked against the authenticated device and a mismatch is refused, so a device
cannot upload events in another device's name.

A device credential grants exactly one power: write its own events. It can read
nothing. A stolen agent secret should let an attacker post nonsense telemetry,
not browse an organisation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import (
    EventBatchUpload,
    HeartbeatUpload,
    IngestResult,
)
from focusguard_backend.auth.dependencies import get_current_device
from focusguard_backend.database.session import get_db
from focusguard_backend.models.identity import Device
from focusguard_backend.models.telemetry import DeviceHeartbeat
from focusguard_backend.repositories.telemetry import Scope, TelemetryRepository
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])


def check_uuid_matches(claimed_uuid: str, device: Device) -> None:
    """Refuse a payload that claims to be from a different device.

    The credential already decided who is calling. This only catches a payload
    whose uuid disagrees with it - a misconfigured agent, or an attempt to file
    events under someone else's machine. Either way it is a refusal, not a
    silent correction: quietly reassigning the events would hide the problem and
    corrupt the record.
    """
    if claimed_uuid != device.device_uuid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The payload device_uuid does not match the authenticated device.",
        )


@router.post("/events", response_model=IngestResult)
def ingest_events(
    batch: EventBatchUpload,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> IngestResult:
    """Accept a batch of events from an agent."""
    check_uuid_matches(batch.device_uuid, device)
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
            longest_focus_block_seconds=batch.session.longest_focus_block_seconds,
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
def heartbeat(
    payload: HeartbeatUpload,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> None:
    """Report that a device is alive (spec section 36).

    Cheap and frequent, so the manager board can show who is working without
    holding a WebSocket open to every machine.
    """
    check_uuid_matches(payload.device_uuid, device)
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
