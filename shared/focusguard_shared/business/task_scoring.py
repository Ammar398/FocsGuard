"""Task focus and task productivity scores (spec section 29).

    Task Focus Score        = duration-weighted mean of the sessions' combined focus
    Task Productivity Score = 100 * (productive + 0.5 * neutral) / classified time

WHY THESE TWO, AND WHY SEPARATE
-------------------------------
Section 59 again: attention and productivity are different questions. A task can
be done with steady attention in the wrong tools, or in the right tools with a
lot of looking away to a second monitor. Reporting one blended number would hide
which of those happened, so a task carries both.

The productivity formula is the agent's DPS formula (`scoring/productivity.py`)
applied to the whole task, so a task's number and the live number the employee
saw while working are the same measurement at two time scales.

WHY WEIGHTED BY DURATION
------------------------
A task worked in one three-hour session and one five-minute session should be
described mostly by the three hours. A flat mean of session scores would let the
five minutes count as much.

NONE IS NOT ZERO
----------------
A task with no monitored sessions - done on paper, or on a machine without the
agent - has no focus score. Reporting 0 would read as "worked with no focus at
all", which is a false and damaging statement about a person. So it is `None`,
and the UI says "not measured".

Pure functions only: no database, no clock. Everything here is tested directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

#: Same compromise as the agent's DPS: communication is real work, but not the
#: task itself. See `desktop_agent/.../scoring/productivity.py`.
NEUTRAL_WEIGHT: float = 0.5


@dataclass(frozen=True)
class TaskSessionFacts:
    """What one monitored session contributed to a task."""

    duration_seconds: float
    average_focus_score: float
    focused_seconds: float = 0.0
    productive_seconds: float = 0.0
    neutral_seconds: float = 0.0
    distracting_seconds: float = 0.0


@dataclass(frozen=True)
class TaskScores:
    """A task's measured outcome, with the reasoning attached."""

    session_count: int
    monitored_seconds: float
    focused_seconds: float
    productive_seconds: float
    neutral_seconds: float
    distracting_seconds: float

    #: None when nothing was monitored. See the module docstring.
    task_focus_score: float | None
    task_productivity_score: float | None

    #: Monitored time / expected time. None without an estimate or without data.
    duration_ratio: float | None

    explanation: list[str] = field(default_factory=list)


def productivity_score(
    productive_seconds: float,
    neutral_seconds: float,
    distracting_seconds: float,
    neutral_weight: float = NEUTRAL_WEIGHT,
) -> float | None:
    """The DPS formula over arbitrary totals. None when nothing was classified."""
    total = productive_seconds + neutral_seconds + distracting_seconds
    if total <= 0:
        return None
    return 100.0 * (productive_seconds + neutral_weight * neutral_seconds) / total


def score_task(
    sessions: Iterable[TaskSessionFacts],
    expected_minutes: int | None = None,
    neutral_weight: float = NEUTRAL_WEIGHT,
) -> TaskScores:
    """Combine a task's sessions into its two scores."""
    facts = [session for session in sessions if session.duration_seconds > 0]

    monitored = sum(s.duration_seconds for s in facts)
    focused = sum(s.focused_seconds for s in facts)
    productive = sum(s.productive_seconds for s in facts)
    neutral = sum(s.neutral_seconds for s in facts)
    distracting = sum(s.distracting_seconds for s in facts)

    focus = (
        sum(s.average_focus_score * s.duration_seconds for s in facts) / monitored
        if monitored > 0
        else None
    )
    productivity = productivity_score(productive, neutral, distracting, neutral_weight)

    ratio = None
    if expected_minutes and expected_minutes > 0 and monitored > 0:
        ratio = (monitored / 60.0) / expected_minutes

    explanation: list[str] = []
    if not facts:
        explanation.append("No monitored sessions were recorded for this task.")
    else:
        explanation.append(
            f"{len(facts)} monitored session(s), {monitored / 60:.0f} minutes in total."
        )
        if focus is not None:
            explanation.append(
                f"Task focus {focus:.0f}: the average combined focus score, "
                "weighted by how long each session lasted."
            )
        if productivity is None:
            explanation.append("No application or website activity was classified.")
        else:
            explanation.append(
                f"Task productivity {productivity:.0f}: {productive / 60:.0f} min productive, "
                f"{neutral / 60:.0f} min neutral (counted at half), "
                f"{distracting / 60:.0f} min distracting, under this task's rules."
            )
        if ratio is not None:
            explanation.append(
                f"Monitored time was {ratio * 100:.0f}% of the {expected_minutes}-minute estimate."
            )

    return TaskScores(
        session_count=len(facts),
        monitored_seconds=monitored,
        focused_seconds=focused,
        productive_seconds=productive,
        neutral_seconds=neutral,
        distracting_seconds=distracting,
        task_focus_score=focus,
        task_productivity_score=productivity,
        duration_ratio=ratio,
        explanation=explanation,
    )
