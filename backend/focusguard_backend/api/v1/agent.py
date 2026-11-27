"""Agent ingest endpoints (spec sections 33, 36, 37).

    POST /api/v1/agent/events      upload a batch
    POST /api/v1/agent/heartbeat   report liveness
    GET  /api/v1/agent/tasks       the open tasks assigned to this device's user

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

A device credential grants write access to its own events, and exactly one
read: **the open tasks assigned to its own user** (Phase 19), because the agent
needs a task's productive / distracting rules to classify activity while that
task is being worked on. It still cannot read a session, a score, or anything
about any colleague. A stolen agent secret should let an attacker post nonsense
telemetry and see one person's to-do list, not browse an organisation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.business_schemas import AgentTaskOut
from focusguard_backend.api.v1.schemas import (
    EventBatchUpload,
    HeartbeatUpload,
    IngestResult,
)
from focusguard_backend.auth.dependencies import get_current_device
from focusguard_backend.database.session import get_db
from focusguard_backend.models.base import as_utc
from focusguard_backend.models.business import Project, Task
from focusguard_backend.models.identity import Device
from focusguard_backend.models.telemetry import DeviceHeartbeat
from focusguard_backend.repositories.telemetry import Scope, TelemetryRepository
from focusguard_shared.constants import OPEN_TASK_STATUSES, TaskStatus
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


def resolve_task(db: Session, device: Device, task_id: int | None) -> Task | None:
    """The task a session claims to belong to, if this device may claim it.

    It must be in the device's organisation and assigned to the device's user.
    Anything else is stored without a task rather than refused: refusing would
    block the agent's whole upload queue on one bad field, and losing the
    monitoring data is worse than losing the link.
    """
    if task_id is None or device.organization_id is None:
        return None
    task = db.scalar(
        select(Task).where(
            Task.id == task_id,
            Task.organization_id == device.organization_id,
            Task.assignee_id == device.user_id,
        )
    )
    if task is None:
        logger.warning(
            "Device %s claimed task %s, which is not assigned to its user; "
            "storing the session without a task",
            device.id,
            task_id,
        )
    return task


@router.post("/events", response_model=IngestResult)
def ingest_events(
    batch: EventBatchUpload,
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> IngestResult:
    """Accept a batch of events from an agent."""
    check_uuid_matches(batch.device_uuid, device)
    repository = TelemetryRepository(db)
    task = resolve_task(db, device, batch.session.task_id)

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
        task_id=task.id if task else None,
    )

    # Starting work on a task moves it to IN_PROGRESS. Completion is never set
    # here - that is the manager's judgement, not the agent's (section 28).
    if task is not None and task.status == TaskStatus.TODO.value:
        task.status = TaskStatus.IN_PROGRESS.value
        task.started_at = task.started_at or batch.session.started_at

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


@router.get("/tasks", response_model=list[AgentTaskOut])
def assigned_tasks(
    device: Device = Depends(get_current_device),
    db: Session = Depends(get_db),
) -> list[AgentTaskOut]:
    """The open tasks assigned to this device's user, and nothing else.

    A student's device belongs to no organisation and always gets an empty list.
    """
    if device.organization_id is None:
        return []

    tasks = db.scalars(
        select(Task)
        .where(
            Task.organization_id == device.organization_id,
            Task.assignee_id == device.user_id,
            Task.status.in_([str(s) for s in OPEN_TASK_STATUSES]),
        )
        .order_by(Task.deadline.is_(None), Task.deadline, Task.id)
    )
    projects = {
        project.id: project.name
        for project in db.scalars(
            select(Project).where(Project.organization_id == device.organization_id)
        )
    }
    db.commit()  # persists last_seen_at, set by get_current_device

    return [
        AgentTaskOut(
            id=task.id,
            title=task.title,
            category=task.category,
            status=task.status,
            project_name=projects.get(task.project_id) if task.project_id else None,
            expected_minutes=task.expected_minutes,
            deadline=as_utc(task.deadline),
            productive_rules=list(task.productive_rules or []),
            neutral_rules=list(task.neutral_rules or []),
            distracting_rules=list(task.distracting_rules or []),
            schedule_days=list(task.schedule_days) if task.schedule_days else None,
            schedule_time=task.schedule_time,
            schedule_minutes=task.schedule_minutes,
        )
        for task in tasks
    ]
