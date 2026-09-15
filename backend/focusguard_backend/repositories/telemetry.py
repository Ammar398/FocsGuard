"""Telemetry data access, with tenant isolation enforced structurally.

WHY A REPOSITORY LAYER AT ALL
-----------------------------
Section 51 requires that a manager from one organisation can never reach
another's data. If that filter lives in service functions, it is one forgotten
`.where()` away from a breach, and nothing fails loudly when it is missing - the
query just returns more than it should.

So every read here takes a **`scope`** argument that is required by the function
signature. Forgetting it is a `TypeError` at import time, not a data leak in
production. `tests/test_backend_isolation.py` proves the boundary holds.

INSERTS ARE IDEMPOTENT
----------------------
`ingest_*` methods skip any event whose `event_uuid` already exists. That is the
server half of the section 37 retry design: an agent that resends after an
ambiguous timeout adds nothing, and a user's distraction totals are not inflated
by a bad network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.models.telemetry import (
    ActivityEvent,
    FocusScore,
    FocusSession,
    VisionEvent,
)


@dataclass(frozen=True)
class Scope:
    """Who is asking, and therefore what they may see.

    A student is scoped to their own `user_id`. A manager is scoped to their
    `organization_id`. One of the two is always set - a scope with neither would
    match everything, so the constructor refuses to build one.
    """

    user_id: int | None = None
    organization_id: int | None = None

    def __post_init__(self) -> None:
        if self.user_id is None and self.organization_id is None:
            raise ValueError(
                "A Scope must be limited to a user or an organisation. "
                "An unscoped query would cross tenant boundaries."
            )

    @classmethod
    def for_user(cls, user_id: int) -> Scope:
        return cls(user_id=user_id)

    @classmethod
    def for_organization(cls, organization_id: int) -> Scope:
        return cls(organization_id=organization_id)


def _apply(statement, model, scope: Scope):
    """Constrain a query to the caller's scope.

    Both filters apply when both are set, so a manager viewing one employee sees
    that employee's rows *and only within their own organisation*.
    """
    if scope.organization_id is not None:
        statement = statement.where(model.organization_id == scope.organization_id)
    if scope.user_id is not None:
        statement = statement.where(model.user_id == scope.user_id)
    return statement


class TelemetryRepository:
    """All reads and writes for sessions, events and scores."""

    def __init__(self, session: Session) -> None:
        self._db = session

    # -- sessions ----------------------------------------------------------

    def create_session(
        self,
        session_uuid: str,
        user_id: int,
        name: str,
        started_at: datetime,
        device_id: int | None = None,
        organization_id: int | None = None,
        task_name: str | None = None,
        task_category: str | None = None,
        agent_version: str | None = None,
    ) -> FocusSession:
        """Create a session, or return the existing one with the same uuid.

        Idempotent for the same reason events are: an agent may retry.
        """
        existing = self._db.scalar(
            select(FocusSession).where(FocusSession.session_uuid == session_uuid)
        )
        if existing is not None:
            return existing

        record = FocusSession(
            session_uuid=session_uuid,
            user_id=user_id,
            device_id=device_id,
            organization_id=organization_id,
            name=name,
            task_name=task_name,
            task_category=task_category,
            started_at=started_at,
            agent_version=agent_version,
        )
        self._db.add(record)
        self._db.flush()
        return record

    def get_session(self, session_id: int, scope: Scope) -> FocusSession | None:
        statement = _apply(
            select(FocusSession).where(FocusSession.id == session_id),
            FocusSession,
            scope,
        )
        return self._db.scalar(statement)

    def get_session_by_uuid(self, session_uuid: str, scope: Scope) -> FocusSession | None:
        statement = _apply(
            select(FocusSession).where(FocusSession.session_uuid == session_uuid),
            FocusSession,
            scope,
        )
        return self._db.scalar(statement)

    def list_sessions(
        self,
        scope: Scope,
        limit: int = 50,
        offset: int = 0,
        since: datetime | None = None,
    ) -> list[FocusSession]:
        statement = _apply(select(FocusSession), FocusSession, scope)
        if since is not None:
            statement = statement.where(FocusSession.started_at >= since)

        statement = (
            statement.order_by(FocusSession.started_at.desc()).limit(limit).offset(offset)
        )
        return list(self._db.scalars(statement))

    def complete_session(
        self,
        session: FocusSession,
        ended_at: datetime,
        duration_seconds: float = 0.0,
        focused_seconds: float = 0.0,
        distracted_seconds: float = 0.0,
        average_focus_score: float = 0.0,
        longest_focus_block_seconds: float = 0.0,
    ) -> None:
        session.ended_at = ended_at
        session.duration_seconds = duration_seconds
        session.focused_seconds = focused_seconds
        session.distracted_seconds = distracted_seconds
        session.average_focus_score = average_focus_score
        session.longest_focus_block_seconds = longest_focus_block_seconds
        self._db.flush()

    # -- ingest (spec section 37) ------------------------------------------

    def _existing_uuids(self, model, uuids: list[str]) -> set[str]:
        if not uuids:
            return set()
        return set(
            self._db.scalars(select(model.event_uuid).where(model.event_uuid.in_(uuids)))
        )

    def ingest_vision_events(
        self, session: FocusSession, events: list[dict]
    ) -> tuple[int, int]:
        """Store new events. Returns (accepted, duplicates_skipped)."""
        if not events:
            return 0, 0

        known = self._existing_uuids(
            VisionEvent, [event["event_uuid"] for event in events]
        )
        accepted = 0

        for event in events:
            if event["event_uuid"] in known:
                continue
            self._db.add(
                VisionEvent(
                    event_uuid=event["event_uuid"],
                    session_id=session.id,
                    user_id=session.user_id,
                    organization_id=session.organization_id,
                    event_type=event["event_type"],
                    start_time=event["start_time"],
                    end_time=event["end_time"],
                    duration_seconds=event["duration_seconds"],
                    confidence=event["confidence"],
                    event_metadata=event.get("metadata"),
                )
            )
            # Guards against duplicates *within* one batch, which the database
            # would otherwise only catch at flush time, failing the whole batch.
            known.add(event["event_uuid"])
            accepted += 1

        self._db.flush()
        return accepted, len(events) - accepted

    def ingest_activity_events(
        self, session: FocusSession, events: list[dict]
    ) -> tuple[int, int]:
        if not events:
            return 0, 0

        known = self._existing_uuids(
            ActivityEvent, [event["event_uuid"] for event in events]
        )
        accepted = 0

        for event in events:
            if event["event_uuid"] in known:
                continue
            self._db.add(
                ActivityEvent(
                    event_uuid=event["event_uuid"],
                    session_id=session.id,
                    user_id=session.user_id,
                    organization_id=session.organization_id,
                    process_name=event.get("process_name"),
                    application_name=event["application_name"],
                    window_title=event.get("window_title"),
                    domain=event.get("domain"),
                    path_prefix=event.get("path_prefix"),
                    category=event["category"],
                    classification=event["classification"],
                    rule_source=event["rule_source"],
                    start_time=event["start_time"],
                    end_time=event["end_time"],
                    duration_seconds=event["duration_seconds"],
                )
            )
            known.add(event["event_uuid"])
            accepted += 1

        self._db.flush()
        return accepted, len(events) - accepted

    def ingest_scores(self, session: FocusSession, scores: list[dict]) -> int:
        """Store score samples.

        Scores carry no uuid: they are a sampled series rather than discrete
        events, and re-uploading a duplicate sample distorts an average rather
        than inventing an event. The agent marks them synced once accepted.
        """
        for score in scores:
            self._db.add(
                FocusScore(
                    session_id=session.id,
                    user_id=session.user_id,
                    organization_id=session.organization_id,
                    recorded_at=score["recorded_at"],
                    visual_attention_score=score["visual_attention_score"],
                    digital_productivity_score=score.get("digital_productivity_score"),
                    combined_focus_score=score["combined_focus_score"],
                    active_events=score.get("active_events"),
                )
            )
        self._db.flush()
        return len(scores)

    # -- reads -------------------------------------------------------------

    def vision_events(self, session_id: int, scope: Scope) -> list[VisionEvent]:
        statement = _apply(
            select(VisionEvent).where(VisionEvent.session_id == session_id),
            VisionEvent,
            scope,
        ).order_by(VisionEvent.start_time)
        return list(self._db.scalars(statement))

    def activity_events(self, session_id: int, scope: Scope) -> list[ActivityEvent]:
        statement = _apply(
            select(ActivityEvent).where(ActivityEvent.session_id == session_id),
            ActivityEvent,
            scope,
        ).order_by(ActivityEvent.start_time)
        return list(self._db.scalars(statement))

    def scores(self, session_id: int, scope: Scope) -> list[FocusScore]:
        statement = _apply(
            select(FocusScore).where(FocusScore.session_id == session_id),
            FocusScore,
            scope,
        ).order_by(FocusScore.recorded_at)
        return list(self._db.scalars(statement))
