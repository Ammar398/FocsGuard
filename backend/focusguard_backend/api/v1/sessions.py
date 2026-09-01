"""Session read endpoints (spec section 36).

    GET /api/v1/sessions            list
    GET /api/v1/sessions/{id}       one session with its full timeline

Every route resolves a `Scope` and passes it to the repository, which cannot
build a query without one. That is how section 51 isolation is kept: not by
remembering a filter, but by a function signature that will not compile without
it.

Phase 13 replaces the temporary user resolution below with a JWT dependency. The
routes do not change - they already ask for a scope rather than assuming one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import SessionDetail, SessionSummary
from focusguard_backend.database.session import get_db
from focusguard_backend.repositories.telemetry import Scope, TelemetryRepository

router = APIRouter(prefix="/sessions", tags=["sessions"])


def current_scope(
    user_id: int = Query(..., description="whose sessions (replaced by auth in Phase 13)"),
) -> Scope:
    """Resolve what the caller may see.

    Temporary: taking the user from a query parameter is obviously not
    authentication, and Phase 13 swaps this dependency for a token. It exists now
    so the routes are already written against a scope rather than being retrofitted
    later - which is when isolation bugs get introduced.
    """
    return Scope.for_user(user_id)


@router.get("", response_model=list[SessionSummary])
def list_sessions(
    scope: Scope = Depends(current_scope),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[SessionSummary]:
    """List sessions, newest first."""
    sessions = TelemetryRepository(db).list_sessions(scope, limit=limit, offset=offset)
    return [SessionSummary.model_validate(session) for session in sessions]


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(
    session_id: int,
    scope: Scope = Depends(current_scope),
    db: Session = Depends(get_db),
) -> SessionDetail:
    """One session with its events and score timeline."""
    repository = TelemetryRepository(db)
    session = repository.get_session(session_id, scope)

    if session is None:
        # 404 rather than 403 for a session outside the caller's scope: telling a
        # stranger that a session exists but is not theirs leaks that it exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        )

    detail = SessionDetail.model_validate(session)
    detail.vision_events = repository.vision_events(session_id, scope)  # type: ignore[assignment]
    detail.activity_events = repository.activity_events(session_id, scope)  # type: ignore[assignment]
    detail.focus_scores = repository.scores(session_id, scope)  # type: ignore[assignment]
    return detail
