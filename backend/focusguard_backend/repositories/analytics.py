"""Aggregation for reports and recommendations (spec sections 42, 43, 51).

SCOPED LIKE EVERYTHING ELSE
---------------------------
Every read here takes a `Scope` the signature requires, exactly as
`repositories/telemetry.py` does. An analytics query is the easiest place to
leak across tenants - it aggregates by definition, so a missing filter produces a
plausible-looking number rather than an error. That is precisely why the filter
cannot be forgotten here.

AGGREGATED IN SQL, NOT IN PYTHON
--------------------------------
A month of sessions is tens of thousands of event rows. Loading them to sum in a
loop would be slow and would grow worse with use, so the grouping happens in the
database and only the totals come back.

The exception is `SessionFacts`, which is built per session - there are dozens of
those, not thousands, and the rules need them separately to tell a habit from one
bad afternoon.

TIME ZONES
----------
Everything is stored in UTC. A report about "today" or "sessions after 10pm" is
meaningless in UTC for a user in Karachi, so the caller passes an offset and the
grouping happens in local time. Doing it in Python rather than in SQL keeps the
query dialect-neutral between SQLite and PostgreSQL, which was a deliberate
constraint from the start.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from focusguard_backend.models.base import as_utc
from focusguard_backend.models.telemetry import (
    ActivityEvent,
    FocusSession,
    VisionEvent,
)
from focusguard_backend.repositories.telemetry import Scope, _apply
from focusguard_shared.recommendations import FocusStatistics, SessionFacts


@dataclass(frozen=True)
class DayTotals:
    """One day, as the report renders it."""

    day: date
    sessions: int = 0
    monitored_seconds: float = 0.0
    focused_seconds: float = 0.0
    distracted_seconds: float = 0.0
    average_focus_score: float = 0.0

    @property
    def focused_share(self) -> float:
        return (
            self.focused_seconds / self.monitored_seconds
            if self.monitored_seconds > 0
            else 0.0
        )


def _local(moment: datetime, offset_minutes: int) -> datetime:
    """A stored UTC timestamp, in the user's local time."""
    aware = as_utc(moment)
    assert aware is not None
    return aware + timedelta(minutes=offset_minutes)


class AnalyticsRepository:
    """Rollups and statistics, always within a scope."""

    def __init__(self, session: Session) -> None:
        self._db = session

    # -- the window --------------------------------------------------------

    def _sessions_in_window(
        self, scope: Scope, since: datetime, until: datetime
    ) -> list[FocusSession]:
        statement = _apply(select(FocusSession), FocusSession, scope).where(
            FocusSession.started_at >= since,
            FocusSession.started_at < until,
        )
        return list(self._db.scalars(statement.order_by(FocusSession.started_at)))

    # -- daily and weekly rollups (spec section 42) ------------------------

    def daily_totals(
        self,
        scope: Scope,
        since: datetime,
        until: datetime,
        offset_minutes: int = 0,
    ) -> list[DayTotals]:
        """Per-day totals, oldest first, with empty days included.

        Empty days are present deliberately. A chart that silently skips them
        draws a flat line across a week off and makes it look like continuous
        work.
        """
        sessions = self._sessions_in_window(scope, since, until)

        grouped: dict[date, list[FocusSession]] = defaultdict(list)
        for session in sessions:
            grouped[_local(session.started_at, offset_minutes).date()].append(session)

        results: list[DayTotals] = []
        cursor = _local(since, offset_minutes).date()
        last = _local(until, offset_minutes).date()

        while cursor < last:
            day_sessions = grouped.get(cursor, [])
            monitored = sum(s.duration_seconds for s in day_sessions)

            results.append(
                DayTotals(
                    day=cursor,
                    sessions=len(day_sessions),
                    monitored_seconds=monitored,
                    focused_seconds=sum(s.focused_seconds for s in day_sessions),
                    distracted_seconds=sum(s.distracted_seconds for s in day_sessions),
                    # Weighted by length, so a two-minute session does not count
                    # as much as a two-hour one.
                    average_focus_score=(
                        sum(s.average_focus_score * s.duration_seconds for s in day_sessions)
                        / monitored
                        if monitored > 0
                        else 0.0
                    ),
                )
            )
            cursor += timedelta(days=1)

        return results

    # -- what the rules need ----------------------------------------------

    def statistics(
        self,
        scope: Scope,
        since: datetime,
        until: datetime,
        offset_minutes: int = 0,
    ) -> FocusStatistics:
        """Build the input the recommendation rules run on."""
        sessions = self._sessions_in_window(scope, since, until)
        if not sessions:
            return FocusStatistics()

        session_ids = [session.id for session in sessions]
        events = self._event_totals(session_ids, scope)
        activity, classifications, distracting = self._activity_totals(session_ids, scope)

        return FocusStatistics(
            sessions=[
                SessionFacts(
                    session_id=session.id,
                    started_at=_local(session.started_at, offset_minutes).date(),
                    start_hour=_local(session.started_at, offset_minutes).hour,
                    duration_seconds=session.duration_seconds,
                    focused_seconds=session.focused_seconds,
                    distracted_seconds=session.distracted_seconds,
                    average_focus_score=session.average_focus_score,
                    event_seconds=events.get(session.id, ({}, {}))[0],
                    event_counts=events.get(session.id, ({}, {}))[1],
                    activity_seconds=activity.get(session.id, {}),
                    distracting_seconds_by_label=distracting.get(session.id, {}),
                    classification_seconds=classifications.get(session.id, {}),
                    longest_focus_block_seconds=session.longest_focus_block_seconds,
                )
                for session in sessions
            ]
        )

    def _event_totals(
        self, session_ids: list[int], scope: Scope
    ) -> dict[int, tuple[dict[str, float], dict[str, int]]]:
        """Seconds and counts per event type, per session. Grouped in SQL."""
        statement = _apply(
            select(
                VisionEvent.session_id,
                VisionEvent.event_type,
                func.sum(VisionEvent.duration_seconds),
                func.count(VisionEvent.id),
            ).where(VisionEvent.session_id.in_(session_ids)),
            VisionEvent,
            scope,
        ).group_by(VisionEvent.session_id, VisionEvent.event_type)

        totals: dict[int, tuple[dict[str, float], dict[str, int]]] = defaultdict(
            lambda: ({}, {})
        )
        for session_id, event_type, seconds, count in self._db.execute(statement):
            seconds_map, counts_map = totals[session_id]
            seconds_map[event_type] = float(seconds or 0.0)
            counts_map[event_type] = int(count or 0)
        return dict(totals)

    def _activity_totals(
        self, session_ids: list[int], scope: Scope
    ) -> tuple[
        dict[int, dict[str, float]],
        dict[int, dict[str, float]],
        dict[int, dict[str, float]],
    ]:
        """Per session: seconds by label, by classification, and distracting by label.

        The label is the domain when there is one, otherwise the application
        name - the same rule the portal uses, so "youtube.com" is one row rather
        than being buried inside "Google Chrome".
        """
        statement = _apply(
            select(
                ActivityEvent.session_id,
                ActivityEvent.domain,
                ActivityEvent.application_name,
                ActivityEvent.classification,
                func.sum(ActivityEvent.duration_seconds),
            ).where(ActivityEvent.session_id.in_(session_ids)),
            ActivityEvent,
            scope,
        ).group_by(
            ActivityEvent.session_id,
            ActivityEvent.domain,
            ActivityEvent.application_name,
            ActivityEvent.classification,
        )

        activity: dict[int, dict[str, float]] = defaultdict(dict)
        classifications: dict[int, dict[str, float]] = defaultdict(dict)
        distracting: dict[int, dict[str, float]] = defaultdict(dict)

        for session_id, domain, application, classification, seconds in self._db.execute(
            statement
        ):
            label = domain or application
            value = float(seconds or 0.0)
            activity[session_id][label] = activity[session_id].get(label, 0.0) + value
            classifications[session_id][classification] = (
                classifications[session_id].get(classification, 0.0) + value
            )

            if classification == "DISTRACTING":
                distracting[session_id][label] = (
                    distracting[session_id].get(label, 0.0) + value
                )

        return dict(activity), dict(classifications), dict(distracting)

    # -- the export (spec section 43) --------------------------------------

    def export_rows(
        self, scope: Scope, since: datetime, until: datetime
    ) -> list[dict[str, object]]:
        """One row per session, for CSV export.

        Sessions rather than raw events, deliberately. A student exporting their
        own data wants something they can open in a spreadsheet and reason about,
        not forty thousand rows. It is also the level that survives the section 45
        retention prune, so an export of an old month is not silently empty.
        """
        return [
            {
                "session_id": session.id,
                "name": session.name,
                "task_name": session.task_name or "",
                "task_category": session.task_category or "",
                "started_at": (as_utc(session.started_at) or session.started_at).isoformat(),
                "ended_at": (
                    (as_utc(session.ended_at) or session.ended_at).isoformat()
                    if session.ended_at
                    else ""
                ),
                "duration_seconds": round(session.duration_seconds, 1),
                "focused_seconds": round(session.focused_seconds, 1),
                "distracted_seconds": round(session.distracted_seconds, 1),
                "average_focus_score": round(session.average_focus_score, 1),
                "longest_focus_block_seconds": round(
                    session.longest_focus_block_seconds, 1
                ),
            }
            for session in self._sessions_in_window(scope, since, until)
        ]


def window_for_days(days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The last `days` days, ending at the next UTC midnight.

    Ending at midnight rather than at "now" means today's partial day is included
    whole, so a report opened at 9am and again at 5pm covers the same span and the
    chart does not appear to change shape during the day.
    """
    moment = now or datetime.now(UTC)
    end = datetime(moment.year, moment.month, moment.day, tzinfo=UTC) + timedelta(days=1)
    return end - timedelta(days=days), end
