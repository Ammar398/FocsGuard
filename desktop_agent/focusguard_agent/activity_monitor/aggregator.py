"""Turning foreground samples into usage spans (spec sections 19, 21, 45).

WHY SPANS AND NOT SAMPLES
-------------------------
Polling once a second for an eight-hour day is 28,800 rows, almost all of them
identical to the one before. What the reports actually need is *"Visual Studio
Code, 3h 10m"* - so consecutive samples of the same application are collapsed
into one span with a start, an end and a duration. Section 45 is explicit that
storing millions of redundant rows is not acceptable on this machine.

WHY SHORT SPANS ARE DISCARDED
-----------------------------
Alt-tabbing through six windows to find one produces six spans of under a second.
None of them is usage, and keeping them would bury the real entries in noise and
make "most used application" meaningless. Spans under `min_span_seconds`
(default 3 s) are dropped - the time is still counted as monitored, it simply is
not attributed to an application.

IDLE TIME IS NOT USAGE
----------------------
If the user walks away with VS Code in front, that is not three hours of
development. Once idle passes the threshold the current span is closed, and no
new span opens until input resumes. The same rule as everywhere else in this
project: time we cannot attribute honestly is attributed to nobody.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from focusguard_agent.activity_monitor.classifier import ClassifiedActivity
from focusguard_shared.constants import ActivityCategory, Classification, RuleSource
from focusguard_shared.utilities import get_logger

logger = get_logger("activity.aggregator")


@dataclass(frozen=True)
class ActivitySpan:
    """One continuous period in a single application."""

    application_name: str
    process_name: str
    category: ActivityCategory
    classification: Classification
    rule_source: RuleSource

    start_time: float
    end_time: float

    window_title: str | None = None
    domain: str | None = None

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def __str__(self) -> str:
        label = self.domain or self.application_name
        return f"{label} for {self.duration_seconds:.0f}s ({self.classification})"


@dataclass
class ActivityTotals:
    """Per-application and per-category totals for the report."""

    by_application: dict[str, float] = field(default_factory=dict)
    by_category: dict[ActivityCategory, float] = field(default_factory=dict)
    by_classification: dict[Classification, float] = field(default_factory=dict)
    idle_seconds: float = 0.0
    discarded_spans: int = 0

    @property
    def total_seconds(self) -> float:
        return sum(self.by_application.values())

    def most_used(self, limit: int = 5) -> list[tuple[str, float]]:
        """Top applications by time - the "most-used application" figure."""
        return sorted(self.by_application.items(), key=lambda item: -item[1])[:limit]

    def seconds_for(self, classification: Classification) -> float:
        return self.by_classification.get(classification, 0.0)


class ActivityAggregator:
    """Collapses a stream of foreground samples into usage spans."""

    def __init__(self, min_span_seconds: float = 3.0) -> None:
        self._min_span = min_span_seconds
        self._totals = ActivityTotals()

        self._current: ClassifiedActivity | None = None
        self._current_process = ""
        self._current_title: str | None = None
        self._started_at: float | None = None
        self._last_sample: float | None = None
        self._was_idle = False

    # -- state -------------------------------------------------------------

    @property
    def totals(self) -> ActivityTotals:
        return self._totals

    @property
    def current(self) -> ClassifiedActivity | None:
        """What is in front right now, or None while idle."""
        return self._current

    def current_duration(self, now: float) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, now - self._started_at)

    # -- driving -----------------------------------------------------------

    def update(
        self,
        now: float,
        activity: ClassifiedActivity | None,
        process_name: str = "",
        window_title: str | None = None,
        idle: bool = False,
    ) -> ActivitySpan | None:
        """Record one sample. Returns a completed span when the application changes.

        Args:
            activity: the classified foreground application, or None when there
                is nothing to attribute (idle, lock screen, window switch).
            idle: True when the user has been inactive past the threshold.
        """
        if idle or activity is None:
            # Only count the gap BETWEEN two idle samples. Measuring from the
            # previous sample would charge the active time before the user
            # stopped as idle - the first idle sample would silently swallow
            # however long they were genuinely working.
            if idle and self._was_idle and self._last_sample is not None:
                self._totals.idle_seconds += max(0.0, now - self._last_sample)

            self._was_idle = idle
            self._last_sample = now
            return self._close(now)

        self._was_idle = False
        self._last_sample = now

        same = (
            self._current is not None
            and self._current.application_name == activity.application_name
            and self._current.domain == activity.domain
        )
        if same:
            return None

        completed = self._close(now)

        self._current = activity
        self._current_process = process_name
        self._current_title = window_title
        self._started_at = now
        return completed

    def _close(self, now: float) -> ActivitySpan | None:
        """End the current span, discarding it if it was too short to matter."""
        if self._current is None or self._started_at is None:
            self._current = None
            self._started_at = None
            return None

        span = ActivitySpan(
            application_name=self._current.application_name,
            process_name=self._current_process,
            category=self._current.category,
            classification=self._current.classification,
            rule_source=self._current.rule_source,
            start_time=self._started_at,
            end_time=now,
            window_title=self._current_title,
            domain=self._current.domain,
        )

        self._current = None
        self._started_at = None
        self._current_title = None

        if span.duration_seconds < self._min_span:
            # Alt-tabbing through windows. Not usage.
            self._totals.discarded_spans += 1
            return None

        self._accumulate(span)
        return span

    def _accumulate(self, span: ActivitySpan) -> None:
        key = span.domain or span.application_name
        seconds = span.duration_seconds

        self._totals.by_application[key] = (
            self._totals.by_application.get(key, 0.0) + seconds
        )
        self._totals.by_category[span.category] = (
            self._totals.by_category.get(span.category, 0.0) + seconds
        )
        self._totals.by_classification[span.classification] = (
            self._totals.by_classification.get(span.classification, 0.0) + seconds
        )

    def flush(self, now: float) -> ActivitySpan | None:
        """Close the open span, e.g. at session end."""
        return self._close(now)
