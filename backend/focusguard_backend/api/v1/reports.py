"""Reports, recommendations and export (spec sections 42, 43).

    GET /api/v1/reports/summary          daily totals over a window
    GET /api/v1/reports/recommendations  rule output, with evidence
    GET /api/v1/reports/export.csv       the caller's own sessions

SCOPE COMES FROM THE TOKEN
--------------------------
Every route depends on `current_scope`, so a student sees their own data and a
manager sees their organisation's. There is no parameter naming whose report to
build, which is what keeps section 51 intact on routes that aggregate - the place
a missing filter produces a plausible number rather than an error.

THE EXPORT IS A RIGHT, NOT A FEATURE
------------------------------------
Section 43 asks for export. It matters more than a convenience: a person being
measured should be able to take the measurements with them, check them, and keep
them if they stop using the tool. So it is the caller's own data, in a format
anything can open, with no server-side record of having exported.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import (
    DayTotalsOut,
    RecommendationOut,
    ReportSummary,
)
from focusguard_backend.auth.dependencies import current_scope
from focusguard_backend.database.session import get_db
from focusguard_backend.repositories.analytics import AnalyticsRepository, window_for_days
from focusguard_backend.repositories.telemetry import Scope
from focusguard_shared.recommendations import generate_recommendations

router = APIRouter(prefix="/reports", tags=["reports"])

#: Bounds on the window. Thirty days is the raw-event retention period (section
#: 45), and a year is the most a summary query should ever be asked to span.
MIN_DAYS = 1
MAX_DAYS = 366

#: Minutes east of UTC. The caller's browser knows this; the server cannot guess
#: it, and guessing would put "sessions after 10pm" in the wrong day.
MIN_OFFSET = -12 * 60
MAX_OFFSET = 14 * 60


def _window(days: int) -> tuple[datetime, datetime]:
    return window_for_days(days)


@router.get("/summary", response_model=ReportSummary)
def summary(
    days: int = Query(7, ge=MIN_DAYS, le=MAX_DAYS, description="window length"),
    offset_minutes: int = Query(
        0, ge=MIN_OFFSET, le=MAX_OFFSET, description="minutes east of UTC"
    ),
    scope: Scope = Depends(current_scope),
    db: Session = Depends(get_db),
) -> ReportSummary:
    """Daily totals over a window (spec section 42).

    `days=1` is today, `days=7` a week, `days=30` a month. One endpoint rather
    than separate daily and weekly routes: they differ only in the window, and
    two endpoints would be two places to fix a bug.
    """
    since, until = _window(days)
    repository = AnalyticsRepository(db)
    totals = repository.daily_totals(scope, since, until, offset_minutes)

    monitored = sum(day.monitored_seconds for day in totals)
    focused = sum(day.focused_seconds for day in totals)

    return ReportSummary(
        since=since,
        until=until,
        days=days,
        sessions=sum(day.sessions for day in totals),
        monitored_seconds=monitored,
        focused_seconds=focused,
        distracted_seconds=sum(day.distracted_seconds for day in totals),
        # Weighted by monitored time. A flat mean over days would let one
        # ten-minute Sunday count as much as a full Monday.
        average_focus_score=(
            sum(day.average_focus_score * day.monitored_seconds for day in totals) / monitored
            if monitored > 0
            else 0.0
        ),
        daily=[
            DayTotalsOut(
                day=day.day,
                sessions=day.sessions,
                monitored_seconds=day.monitored_seconds,
                focused_seconds=day.focused_seconds,
                distracted_seconds=day.distracted_seconds,
                average_focus_score=day.average_focus_score,
            )
            for day in totals
        ],
    )


@router.get("/recommendations", response_model=list[RecommendationOut])
def recommendations(
    days: int = Query(7, ge=MIN_DAYS, le=MAX_DAYS),
    offset_minutes: int = Query(0, ge=MIN_OFFSET, le=MAX_OFFSET),
    limit: int = Query(4, ge=1, le=8),
    scope: Scope = Depends(current_scope),
    db: Session = Depends(get_db),
) -> list[RecommendationOut]:
    """Rule-based suggestions, each with the data that triggered it (section 9).

    The rules live in `focusguard_shared.recommendations` as pure functions, so
    the same logic can run against the agent's local store with no backend at
    all, and the two cannot disagree.
    """
    since, until = _window(days)
    stats = AnalyticsRepository(db).statistics(scope, since, until, offset_minutes)

    return [
        RecommendationOut(
            rule_id=item.rule_id,
            kind=str(item.kind),
            priority=str(item.priority),
            title=item.title,
            detail=item.detail,
            evidence=[{"label": e.label, "value": e.value} for e in item.evidence],
        )
        for item in generate_recommendations(stats, limit=limit)
    ]


@router.get("/export.csv")
def export_csv(
    days: int = Query(30, ge=MIN_DAYS, le=MAX_DAYS),
    scope: Scope = Depends(current_scope),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """The caller's sessions as CSV (spec section 43)."""
    since, until = _window(days)
    rows = AnalyticsRepository(db).export_rows(scope, since, until)

    columns = [
        "session_id",
        "name",
        "task_name",
        "task_category",
        "started_at",
        "ended_at",
        "duration_seconds",
        "focused_seconds",
        "distracted_seconds",
        "average_focus_score",
        "longest_focus_block_seconds",
    ]

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        # Encoded with a BOM so Excel on Windows reads it as UTF-8. Without it,
        # a session named with any non-ASCII character opens as mojibake, which
        # is the single most common complaint about CSV exports on this platform.
        iter([buffer.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="focusguard-{stamp}.csv"'
        },
    )
