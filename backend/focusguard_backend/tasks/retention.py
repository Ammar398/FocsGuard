"""Server-side retention (spec section 45).

    python -m focusguard_backend.tasks.retention              # 30 days, the default
    python -m focusguard_backend.tasks.retention --days 60
    python -m focusguard_backend.tasks.retention --dry-run

WHAT IS KEPT AND WHAT GOES
--------------------------
Raw rows - attention events, activity spans, ~1 Hz score samples - are the bulk
of the database and are only needed for a session's detailed timeline. After the
retention window they are deleted. What stays, indefinitely:

* every session with its totals (duration, focused, distracted, average focus,
  longest focus block) - already stored on the session row;
* the session's productive / neutral / distracting seconds, **written onto the
  session row first**, so a task finished months ago keeps its productivity
  score and the employee keeps their strength history;
* tasks, ratings, consents and accounts.

Only sessions that have *ended* before the cut-off are touched. A session still
running, or still uploading, is never pruned under it.

Idempotent: a session is marked with `raw_events_pruned_at`, and a second run
finds nothing left to do.

Run it from Task Scheduler once a day; it takes well under a second on a
demonstration-sized database.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from focusguard_backend.models.telemetry import (
    ActivityEvent,
    FocusScore,
    FocusSession,
    VisionEvent,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.tasks.retention")

DEFAULT_RETENTION_DAYS = 30


@dataclass
class PruneResult:
    sessions: int = 0
    vision_events: int = 0
    activity_events: int = 0
    focus_scores: int = 0


def prune_raw_events(
    db: Session,
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> PruneResult:
    """Roll up, then delete, raw rows of sessions that ended before the cut-off."""
    if older_than_days < 1:
        raise ValueError("Retention must be at least one day.")

    cutoff = (now or datetime.now(UTC)) - timedelta(days=older_than_days)
    sessions = list(
        db.scalars(
            select(FocusSession).where(
                FocusSession.ended_at.is_not(None),
                FocusSession.ended_at < cutoff,
                FocusSession.raw_events_pruned_at.is_(None),
            )
        )
    )
    result = PruneResult(sessions=len(sessions))
    if not sessions:
        return result

    ids = [session.id for session in sessions]

    totals: dict[int, dict[str, float]] = {}
    for session_id, classification, seconds in db.execute(
        select(
            ActivityEvent.session_id,
            ActivityEvent.classification,
            func.sum(ActivityEvent.duration_seconds),
        )
        .where(ActivityEvent.session_id.in_(ids))
        .group_by(ActivityEvent.session_id, ActivityEvent.classification)
    ):
        totals.setdefault(session_id, {})[classification] = float(seconds or 0.0)

    def count(model) -> int:
        return int(
            db.scalar(select(func.count()).select_from(model).where(model.session_id.in_(ids)))
            or 0
        )

    result.vision_events = count(VisionEvent)
    result.activity_events = count(ActivityEvent)
    result.focus_scores = count(FocusScore)

    if dry_run:
        return result

    stamp = datetime.now(UTC)
    for session in sessions:
        split = totals.get(session.id, {})
        session.productive_seconds = split.get("PRODUCTIVE", 0.0)
        session.neutral_seconds = split.get("NEUTRAL", 0.0)
        session.distracting_seconds = split.get("DISTRACTING", 0.0)
        session.raw_events_pruned_at = stamp

    # The roll-up is flushed before anything is deleted, so a failure part-way
    # leaves totals with their raw rows rather than raw rows with no totals.
    db.flush()
    for model in (VisionEvent, ActivityEvent, FocusScore):
        db.execute(delete(model).where(model.session_id.in_(ids)))
    db.commit()

    logger.info(
        "Retention: pruned raw rows of %d session(s) older than %d days "
        "(%d events, %d activity spans, %d score samples)",
        result.sessions,
        older_than_days,
        result.vision_events,
        result.activity_events,
        result.focus_scores,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    from focusguard_backend.database.session import create_all, get_session_factory
    from focusguard_shared.utilities import configure_logging

    parser = argparse.ArgumentParser(description="Prune raw telemetry past the retention window")
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)
    parser.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    args = parser.parse_args(argv)

    configure_logging(level="INFO")
    create_all()
    with get_session_factory()() as db:
        result = prune_raw_events(db, args.days, dry_run=args.dry_run)

    verb = "Would prune" if args.dry_run else "Pruned"
    print(
        f"{verb} {result.sessions} session(s): {result.vision_events} attention events, "
        f"{result.activity_events} activity spans, {result.focus_scores} score samples. "
        "Session totals and task history are kept."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
