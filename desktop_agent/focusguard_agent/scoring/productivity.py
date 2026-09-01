"""Digital productivity scoring (spec sections 23, 59).

THE SECOND AXIS
---------------
Visual attention answers *were they looking at the screen*. This answers *was
what they were doing worth doing*. They are deliberately independent, because the
interesting cases are the ones where they disagree:

| Situation | Attention | Productivity |
|---|---|---|
| Staring intently at Netflix | high | low |
| Reading docs across two monitors | lower | high |

Collapsing them into one number early would hide exactly the behaviour the
project exists to describe (spec section 59).

    DPS = 100 * (productive_seconds + 0.5 * neutral_seconds) / total_seconds

WHY NEUTRAL COUNTS AS HALF
--------------------------
Slack and email are real work. Scoring them as zero would make the number unfair
and, worse, would push users toward gaming it - avoiding communication to protect
a score is a bad outcome for a tool meant to help. Scoring them as fully
productive would make the metric meaningless. Half is the honest compromise, and
it is configurable.

WHAT COUNTS AS PRODUCTIVE IS NOT DECIDED HERE
---------------------------------------------
This module only accumulates durations against a verdict it is given. The verdict
comes from the task-context engine in Phase 10, because it depends entirely on
what the user is meant to be doing: YouTube during "Watch Python Lecture" is
productive, and during "Implement Authentication" it is not (spec section 22).

Until Phase 9 supplies foreground-application data there is no input at all, and
`score()` returns None rather than a made-up number.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from focusguard_agent.config import ScoringSettings
from focusguard_shared.constants import Classification
from focusguard_shared.utilities import get_logger

logger = get_logger("scoring.productivity")

#: Samples further apart than this are not counted. Without it, a gap while the
#: agent was paused would be attributed to whatever was in front of it.
_MAX_SAMPLE_GAP_SECONDS = 5.0


@dataclass
class ProductivityTotals:
    """Time spent in each classification."""

    productive_seconds: float = 0.0
    neutral_seconds: float = 0.0
    distracting_seconds: float = 0.0

    @property
    def total_seconds(self) -> float:
        return self.productive_seconds + self.neutral_seconds + self.distracting_seconds

    def ratio(self, classification: Classification) -> float:
        total = self.total_seconds
        if total <= 0:
            return 0.0
        return {
            Classification.PRODUCTIVE: self.productive_seconds,
            Classification.NEUTRAL: self.neutral_seconds,
            Classification.DISTRACTING: self.distracting_seconds,
        }[classification] / total


class ProductivityTracker:
    """Accumulates classified activity time and reports a 0-100 score."""

    def __init__(self, settings: ScoringSettings | None = None) -> None:
        self._settings = settings or ScoringSettings()
        self._totals = ProductivityTotals()
        self._window: deque[tuple[float, Classification, float]] = deque()
        self._last: tuple[float, Classification] | None = None

    @property
    def totals(self) -> ProductivityTotals:
        """Session totals, for the report."""
        return self._totals

    @property
    def has_data(self) -> bool:
        return self._totals.total_seconds > 0

    def update(self, now: float, classification: Classification | None) -> None:
        """Record what the user was doing since the previous call.

        Args:
            classification: the verdict for the active application, or None when
                nothing is known - idle, or activity monitoring unavailable.
        """
        if classification is None:
            self._last = None
            return

        if self._last is not None:
            previous_time, previous_classification = self._last
            gap = now - previous_time
            if 0 < gap <= _MAX_SAMPLE_GAP_SECONDS:
                self._add(previous_classification, gap)
                self._window.append((now, previous_classification, gap))

        self._last = (now, classification)
        self._trim(now)

    def _add(self, classification: Classification, seconds: float) -> None:
        if classification is Classification.PRODUCTIVE:
            self._totals.productive_seconds += seconds
        elif classification is Classification.NEUTRAL:
            self._totals.neutral_seconds += seconds
        else:
            self._totals.distracting_seconds += seconds

    def _trim(self, now: float) -> None:
        cutoff = now - self._settings.rolling_window_seconds
        while self._window and self._window[0][0] < cutoff:
            self._window.popleft()

    def score(self, rolling: bool = True) -> float | None:
        """Return the 0-100 productivity score, or None when there is no data.

        Args:
            rolling: score the recent window rather than the whole session, so
                the live dashboard reflects what is happening now.
        """
        if rolling:
            totals = ProductivityTotals()
            for _, classification, seconds in self._window:
                if classification is Classification.PRODUCTIVE:
                    totals.productive_seconds += seconds
                elif classification is Classification.NEUTRAL:
                    totals.neutral_seconds += seconds
                else:
                    totals.distracting_seconds += seconds
        else:
            totals = self._totals

        if totals.total_seconds <= 0:
            # No data is not the same as bad productivity. Returning None lets the
            # scoring engine fall back to attention alone rather than inventing a
            # number the user would see and believe.
            return None

        credited = (
            totals.productive_seconds
            + self._settings.neutral_activity_credit * totals.neutral_seconds
        )
        return 100.0 * credited / totals.total_seconds

    def reset(self) -> None:
        self._totals = ProductivityTotals()
        self._window.clear()
        self._last = None
