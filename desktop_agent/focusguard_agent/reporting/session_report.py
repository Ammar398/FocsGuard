"""Rebuild a session report from stored data (spec sections 42, 43).

WHY THIS EXISTS SEPARATELY FROM THE AGENT
-----------------------------------------
The agent prints a summary when it shuts down cleanly. That is not enough:

* a hard kill, a power cut or a crash loses the printout even though every event
  was already written to the database;
* a user wants yesterday's numbers without re-running anything;
* Phase 15's daily and weekly reports, and the backend's analytics, need the same
  figures computed the same way.

So the report is derived from **stored rows**, not from live objects. There is one
implementation of "what does this session say", and the agent's live summary and
a report printed a week later cannot disagree.

TIME IS RECONSTRUCTED FROM SAMPLE GAPS
--------------------------------------
Focused and distracted seconds come from the score timeline, which is written at
roughly 1 Hz. Each sample is credited with the gap to the next one.

Gaps longer than the tolerance are **not** counted. Those are pauses: the camera
dropped, the machine slept, the agent was suspended. Counting them would
attribute time to a user who was not being measured - the same rule the scoring
engine, PERCLOS and the activity aggregator all follow.

The tolerance is **derived from the configured sample interval**, not fixed. A
hardcoded five seconds silently assumes 1 Hz sampling: raise
`scoring.score_sample_interval_seconds` above it and every gap looks like a
pause, so every report shows zero focused and zero distracted time beside a
perfectly ordinary-looking average. A wrong number that looks right is worse
than a crash, so the two settings are tied together here instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from focusguard_agent.local_store.database import LocalStore
from focusguard_shared.constants import Classification
from focusguard_shared.utilities import get_logger

logger = get_logger("reporting.session")

#: A gap of more than this many sample periods is a pause rather than jitter.
#: Two and a half periods tolerates one dropped sample and a late one; three
#: consecutive misses is a real interruption.
_GAP_TOLERANCE_PERIODS = 2.5

#: Floor for the tolerance, so a very fast sample rate does not make the report
#: hypersensitive to ordinary scheduling jitter on a busy four-core machine.
_MIN_SAMPLE_GAP_SECONDS = 5.0


def default_sample_gap_seconds() -> float:
    """The pause threshold implied by the configured sampling rate."""
    from focusguard_agent.config import get_settings

    interval = get_settings().scoring.score_sample_interval_seconds
    return max(_MIN_SAMPLE_GAP_SECONDS, interval * _GAP_TOLERANCE_PERIODS)


@dataclass
class EventTotal:
    """How much of a session one kind of event accounted for."""

    event_type: str
    seconds: float = 0.0
    count: int = 0
    mean_confidence: float = 0.0
    longest_seconds: float = 0.0


@dataclass
class ApplicationTotal:
    """Time in one application or on one domain."""

    name: str
    seconds: float = 0.0
    classification: str = "NEUTRAL"
    category: str = "OTHER"
    is_domain: bool = False


@dataclass
class SessionReport:
    """Everything a session says, derived entirely from stored rows."""

    session_id: int
    name: str
    task_name: str | None = None
    task_category: str | None = None

    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: float = 0.0
    completed: bool = True

    #: Time covered by score samples. Less than duration when monitoring paused.
    scored_seconds: float = 0.0
    focused_seconds: float = 0.0
    distracted_seconds: float = 0.0
    longest_focus_block_seconds: float = 0.0

    average_focus_score: float = 0.0
    lowest_focus_score: float = 0.0
    highest_focus_score: float = 0.0
    average_visual_attention: float = 0.0
    average_productivity: float | None = None

    events: list[EventTotal] = field(default_factory=list)
    applications: list[ApplicationTotal] = field(default_factory=list)
    classification_seconds: dict[str, float] = field(default_factory=dict)

    @property
    def focused_ratio(self) -> float:
        if self.scored_seconds <= 0:
            return 0.0
        return self.focused_seconds / self.scored_seconds

    @property
    def unscored_seconds(self) -> float:
        """Session time not covered by scoring - camera down, agent paused."""
        return max(0.0, self.duration_seconds - self.scored_seconds)

    @property
    def phone_appearances(self) -> int:
        return next(
            (event.count for event in self.events if event.event_type == "PHONE_VISIBLE"), 0
        )

    def event(self, event_type: str) -> EventTotal | None:
        return next((e for e in self.events if e.event_type == event_type), None)

    def top_applications(self, limit: int = 5) -> list[ApplicationTotal]:
        return self.applications[:limit]

    def most_distracting(self) -> ApplicationTotal | None:
        distracting = [
            app for app in self.applications
            if app.classification == Classification.DISTRACTING.value
        ]
        return distracting[0] if distracting else None


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_session_report(
    store: LocalStore,
    session_id: int,
    max_sample_gap_seconds: float | None = None,
) -> SessionReport | None:
    """Compute a full report for one session, or None if it does not exist.

    `max_sample_gap_seconds` defaults to the value implied by the configured
    sample interval. It is a parameter so tests can state it outright rather
    than depending on global settings.
    """
    row = store.get_session(session_id)
    if row is None:
        return None

    started = _parse(row["started_at"])
    ended = _parse(row["ended_at"])

    report = SessionReport(
        session_id=session_id,
        name=row["name"],
        task_name=row["task_name"],
        task_category=row["task_category"],
        started_at=started,
        ended_at=ended,
        completed=ended is not None,
    )

    scores = store.scores(session_id)
    if started is not None:
        # An unfinished session is measured to its last sample rather than to
        # now, so a crashed session does not appear to have run for days.
        last = _parse(scores[-1]["recorded_at"]) if scores else None
        finish = ended or last or started
        report.duration_seconds = max(0.0, (finish - started).total_seconds())

    _summarise_scores(
        report, scores, max_sample_gap_seconds or default_sample_gap_seconds()
    )
    _summarise_events(report, store.vision_events(session_id))
    _summarise_activity(report, store.activity_events(session_id))
    return report


def _summarise_scores(report: SessionReport, scores: list, max_gap_seconds: float) -> None:
    if not scores:
        return

    combined = [row["combined_focus_score"] for row in scores]
    report.average_focus_score = sum(combined) / len(combined)
    report.lowest_focus_score = min(combined)
    report.highest_focus_score = max(combined)

    visual = [row["visual_attention_score"] for row in scores]
    report.average_visual_attention = sum(visual) / len(visual)

    productivity = [
        row["digital_productivity_score"]
        for row in scores
        if row["digital_productivity_score"] is not None
    ]
    if productivity:
        report.average_productivity = sum(productivity) / len(productivity)

    # Credit each sample with the gap to the next, skipping pauses.
    block_start: datetime | None = None
    for current, following in zip(scores, scores[1:], strict=False):
        moment = _parse(current["recorded_at"])
        next_moment = _parse(following["recorded_at"])
        if moment is None or next_moment is None:
            continue

        gap = (next_moment - moment).total_seconds()
        if not 0 < gap <= max_gap_seconds:
            # A pause. Close any focus block rather than letting it span the gap.
            block_start = None
            continue

        report.scored_seconds += gap

        raw = current["active_events"]
        active = json.loads(raw) if raw else []

        if active:
            report.distracted_seconds += gap
            block_start = None
        else:
            report.focused_seconds += gap
            if block_start is None:
                block_start = moment
            report.longest_focus_block_seconds = max(
                report.longest_focus_block_seconds,
                (next_moment - block_start).total_seconds(),
            )


def _summarise_events(report: SessionReport, events: list) -> None:
    totals: dict[str, EventTotal] = {}
    confidence_sums: dict[str, float] = {}

    for row in events:
        event_type = row["event_type"]
        total = totals.setdefault(event_type, EventTotal(event_type=event_type))
        duration = row["duration_seconds"]

        total.seconds += duration
        total.count += 1
        total.longest_seconds = max(total.longest_seconds, duration)
        confidence_sums[event_type] = confidence_sums.get(event_type, 0.0) + row["confidence"]

    for event_type, total in totals.items():
        total.mean_confidence = confidence_sums[event_type] / total.count

    report.events = sorted(totals.values(), key=lambda item: -item.seconds)


def _summarise_activity(report: SessionReport, activity: list) -> None:
    totals: dict[str, ApplicationTotal] = {}

    for row in activity:
        # A domain is the more informative label when one is present: "github.com"
        # says far more than "Google Chrome".
        domain = row["domain"]
        name = domain or row["application_name"]

        total = totals.setdefault(
            name,
            ApplicationTotal(
                name=name,
                classification=row["classification"],
                category=row["category"],
                is_domain=bool(domain),
            ),
        )
        total.seconds += row["duration_seconds"]

        report.classification_seconds[row["classification"]] = (
            report.classification_seconds.get(row["classification"], 0.0)
            + row["duration_seconds"]
        )

    report.applications = sorted(totals.values(), key=lambda item: -item.seconds)
