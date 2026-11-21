"""Employee strength analytics and assignee suggestions (spec sections 30, 31).

THE QUESTION THIS ANSWERS
-------------------------
*"Who has historically performed strongest for similar work?"* - asked about
**assigned-task outcomes**, not about surveillance. The inputs are what happened
to completed tasks: the manager's quality rating, whether the deadline was met,
how the time compared with the estimate, how much rework was needed, and - as
supporting evidence only - the task's focus and productivity scores.

THE COMPOSITE SCORE
-------------------
Each input becomes a 0-100 component, and the historical score is their
weighted mean:

    component        source                               default weight
    quality          mean manager rating / 5 * 100              0.30
    on_time          share of deadlines met * 100               0.15
    estimate         100 at or under estimate, -1 per % over    0.10
    rework           100 - 25 per average rework round          0.05
    focus            mean task focus score                      0.20
    productivity     mean task productivity score               0.20

Outcome measures carry 60% of the weight and monitoring measures 40%. That
ordering is deliberate: a manager's judgement of the work and whether it landed
on time say more about how a task went than how often someone glanced away
from a webcam.

A component with no data - no task in the category had a deadline, say - is
left out and the remaining weights are renormalised, rather than counting the
missing value as zero. Every component used is returned with the score, so the
number can always be taken apart.

THE SAFEGUARDS
--------------
* **Minimum sample.** Fewer than `MIN_TASKS_FOR_STRENGTH_CLAIM` (3) completed
  tasks in a category gives *insufficient data*, no score, and no ranking. One
  task is an anecdote.
* **Comparative phrasing only.** Every sentence this module produces passes
  `assert_comparative_language`, which forbids words such as "bad", "weak" and
  "poor". The output says one category's record is stronger than another's; it
  never says a person is bad at anything.
* **The manager decides.** `rank_candidates` orders and explains. Nothing in
  FocusGuard assigns a task on its own.

Pure functions only, so all of this is tested without a database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from focusguard_shared.business.vocabulary import assert_comparative_language
from focusguard_shared.constants import MIN_TASKS_FOR_STRENGTH_CLAIM

#: Below this many points apart, two categories are described as similar rather
#: than one being called stronger. Differences of a few points are noise.
MEANINGFUL_GAP: float = 5.0

_NAME_PLACEHOLDER = "this employee"


@dataclass(frozen=True)
class StrengthWeights:
    """Component weights. Configurable; need not sum to 1 (they are normalised)."""

    quality: float = 0.30
    on_time: float = 0.15
    estimate: float = 0.10
    rework: float = 0.05
    focus: float = 0.20
    productivity: float = 0.20

    def as_dict(self) -> dict[str, float]:
        return {
            "quality": self.quality,
            "on_time": self.on_time,
            "estimate": self.estimate,
            "rework": self.rework,
            "focus": self.focus,
            "productivity": self.productivity,
        }


DEFAULT_WEIGHTS = StrengthWeights()


@dataclass(frozen=True)
class TaskOutcome:
    """One completed task, reduced to what strength analytics uses."""

    task_id: int
    category: str
    quality_rating: float | None = None  # 1-5, entered by the manager
    on_time: bool | None = None  # None when the task had no deadline
    duration_ratio: float | None = None  # monitored / expected time
    focus_score: float | None = None  # 0-100, None when not monitored
    productivity_score: float | None = None  # 0-100, None when not classified
    rework_count: int = 0


@dataclass(frozen=True)
class CategoryPerformance:
    """One employee's record in one task category."""

    category: str
    completed_tasks: int
    sufficient_data: bool
    average_quality: float | None
    on_time_rate: float | None
    average_duration_ratio: float | None
    average_focus: float | None
    average_productivity: float | None
    average_rework: float
    #: Each 0-100 component that had data, keyed by name.
    components: dict[str, float] = field(default_factory=dict)
    #: None below the minimum sample, or when no component had data.
    historical_score: float | None = None


def category_label(category: str) -> str:
    """BACKEND -> backend, so sentences read naturally."""
    return category.replace("_", " ").lower()


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def estimate_component(ratio: float) -> float:
    """100 at or under the estimate, falling to 0 at twice the estimate.

    Finishing faster than estimated is not rewarded beyond 100: a task done in
    half the time may simply have been over-estimated, and rewarding it would
    encourage padding estimates downward.
    """
    if ratio <= 1.0:
        return 100.0
    return max(0.0, 100.0 - (ratio - 1.0) * 100.0)


def rework_component(average_rework: float) -> float:
    return max(0.0, 100.0 - 25.0 * average_rework)


def category_performance(
    category: str,
    outcomes: Sequence[TaskOutcome],
    weights: StrengthWeights = DEFAULT_WEIGHTS,
    min_tasks: int = MIN_TASKS_FOR_STRENGTH_CLAIM,
) -> CategoryPerformance:
    """Summarise one category from its completed tasks."""
    relevant = [outcome for outcome in outcomes if outcome.category == category]
    count = len(relevant)

    quality = _mean(o.quality_rating for o in relevant)
    deadlines = [o.on_time for o in relevant if o.on_time is not None]
    on_time = sum(deadlines) / len(deadlines) if deadlines else None
    ratio = _mean(o.duration_ratio for o in relevant)
    focus = _mean(o.focus_score for o in relevant)
    productivity = _mean(o.productivity_score for o in relevant)
    rework = sum(o.rework_count for o in relevant) / count if count else 0.0

    components: dict[str, float] = {}
    if quality is not None:
        components["quality"] = quality / 5.0 * 100.0
    if on_time is not None:
        components["on_time"] = on_time * 100.0
    if ratio is not None:
        components["estimate"] = estimate_component(ratio)
    if count:
        components["rework"] = rework_component(rework)
    if focus is not None:
        components["focus"] = focus
    if productivity is not None:
        components["productivity"] = productivity

    sufficient = count >= min_tasks
    score = None
    if sufficient and components:
        weight_map = weights.as_dict()
        total_weight = sum(weight_map[name] for name in components)
        if total_weight > 0:
            score = (
                sum(value * weight_map[name] for name, value in components.items())
                / total_weight
            )

    return CategoryPerformance(
        category=category,
        completed_tasks=count,
        sufficient_data=sufficient,
        average_quality=quality,
        on_time_rate=on_time,
        average_duration_ratio=ratio,
        average_focus=focus,
        average_productivity=productivity,
        average_rework=rework,
        components={name: round(value, 1) for name, value in components.items()},
        historical_score=score,
    )


def employee_profile(
    outcomes: Sequence[TaskOutcome],
    weights: StrengthWeights = DEFAULT_WEIGHTS,
    min_tasks: int = MIN_TASKS_FOR_STRENGTH_CLAIM,
) -> list[CategoryPerformance]:
    """Every category the employee has completed work in.

    Scored categories first, strongest record first; then categories with too
    little history, most-completed first.
    """
    categories = sorted({outcome.category for outcome in outcomes})
    profile = [category_performance(c, outcomes, weights, min_tasks) for c in categories]

    scored = sorted(
        (p for p in profile if p.historical_score is not None),
        key=lambda p: (-(p.historical_score or 0.0), -p.completed_tasks, p.category),
    )
    unscored = sorted(
        (p for p in profile if p.historical_score is None),
        key=lambda p: (-p.completed_tasks, p.category),
    )
    return scored + unscored


def strength_statements(
    name: str,
    profile: Sequence[CategoryPerformance],
    min_tasks: int = MIN_TASKS_FOR_STRENGTH_CLAIM,
) -> list[str]:
    """Plain-language observations about an employee's task history.

    Comparative, attributed to the data, and checked against the judgement
    vocabulary before being returned.
    """
    # Generated against a placeholder and checked before the real name goes in.
    # A name is user input; checking it would mean an employee called "Bad" (it
    # is a surname) crashes their own profile page.
    real_name, name = name, _NAME_PLACEHOLDER
    scored = [p for p in profile if p.historical_score is not None]
    statements: list[str] = []

    if not scored:
        statements.append(
            f"There is not yet enough completed-task history to compare {name}'s "
            f"performance across task categories. At least {min_tasks} completed "
            "tasks in a category are needed."
        )
    elif len(scored) == 1:
        only = scored[0]
        statements.append(
            f"Historical task data for {name} covers {category_label(only.category)} "
            f"assignments: a historical score of {only.historical_score:.0f} across "
            f"{only.completed_tasks} completed tasks."
        )
    else:
        best, runner_up = scored[0], scored[1]
        gap = (best.historical_score or 0.0) - (runner_up.historical_score or 0.0)
        if gap >= MEANINGFUL_GAP:
            statements.append(
                f"Historical task data suggests {name} has performed more strongly on "
                f"{category_label(best.category)} assignments "
                f"({best.historical_score:.0f} across {best.completed_tasks} tasks) than on "
                f"{category_label(runner_up.category)} assignments "
                f"({runner_up.historical_score:.0f} across {runner_up.completed_tasks} tasks)."
            )
        else:
            statements.append(
                f"Historical task data shows {name} performing at a similar level on "
                f"{category_label(best.category)} ({best.historical_score:.0f}) and "
                f"{category_label(runner_up.category)} ({runner_up.historical_score:.0f}) "
                "assignments."
            )

    thin = [p for p in profile if not p.sufficient_data]
    if thin:
        listed = ", ".join(
            f"{category_label(p.category)} ({p.completed_tasks})" for p in thin
        )
        statements.append(
            f"Too few completed tasks to draw conclusions for: {listed}."
        )

    assert_comparative_language(*statements)
    return [statement.replace(_NAME_PLACEHOLDER, real_name) for statement in statements]


# --- assignee suggestions (spec section 31) ---------------------------------


@dataclass(frozen=True)
class CandidateInput:
    user_id: int
    name: str
    outcomes: Sequence[TaskOutcome]
    open_tasks: int = 0


@dataclass(frozen=True)
class Candidate:
    user_id: int
    name: str
    #: 1-based. None for candidates with insufficient history, who are not ranked.
    rank: int | None
    performance: CategoryPerformance
    open_tasks: int
    reasons: list[str]


@dataclass(frozen=True)
class CandidateRanking:
    category: str
    ranked: list[Candidate]
    insufficient_data: list[Candidate]
    notice: str = (
        "Suggestions are based only on completed tasks in this category. "
        "They are evidence for your decision, not a decision."
    )


def _reasons(performance: CategoryPerformance, open_tasks: int) -> list[str]:
    label = category_label(performance.category)
    reasons: list[str] = []

    if performance.historical_score is not None:
        reasons.append(
            f"{label.capitalize()} historical score {performance.historical_score:.0f} "
            f"across {performance.completed_tasks} completed tasks"
        )
    else:
        reasons.append(
            f"{performance.completed_tasks} completed {label} task(s) - fewer than "
            f"{MIN_TASKS_FOR_STRENGTH_CLAIM} needed for a score"
        )

    if performance.average_quality is not None:
        reasons.append(f"Average quality rating {performance.average_quality:.1f}/5")
    if performance.on_time_rate is not None:
        reasons.append(f"{performance.on_time_rate * 100:.0f}% of deadlines met")
    if performance.average_duration_ratio is not None:
        reasons.append(
            f"Monitored time averaged {performance.average_duration_ratio * 100:.0f}% of estimates"
        )
    if performance.average_focus is not None:
        reasons.append(f"Average task focus {performance.average_focus:.0f}")
    if performance.average_productivity is not None:
        reasons.append(f"Average task productivity {performance.average_productivity:.0f}")
    reasons.append(f"Currently {open_tasks} open task(s)")

    assert_comparative_language(*reasons)
    return reasons


def rank_candidates(
    category: str,
    people: Iterable[CandidateInput],
    weights: StrengthWeights = DEFAULT_WEIGHTS,
    min_tasks: int = MIN_TASKS_FOR_STRENGTH_CLAIM,
) -> CandidateRanking:
    """Order employees by their historical record in one category.

    Ties go to more completed tasks (a better-supported number), then to the
    lighter current workload.
    """
    scored: list[tuple[CandidateInput, CategoryPerformance]] = []
    unscored: list[tuple[CandidateInput, CategoryPerformance]] = []

    for person in people:
        performance = category_performance(category, person.outcomes, weights, min_tasks)
        (scored if performance.historical_score is not None else unscored).append(
            (person, performance)
        )

    scored.sort(
        key=lambda pair: (
            -(pair[1].historical_score or 0.0),
            -pair[1].completed_tasks,
            pair[0].open_tasks,
            pair[0].name,
        )
    )
    unscored.sort(key=lambda pair: (-pair[1].completed_tasks, pair[0].open_tasks, pair[0].name))

    ranked = [
        Candidate(
            user_id=person.user_id,
            name=person.name,
            rank=index + 1,
            performance=performance,
            open_tasks=person.open_tasks,
            reasons=_reasons(performance, person.open_tasks),
        )
        for index, (person, performance) in enumerate(scored)
    ]
    insufficient = [
        Candidate(
            user_id=person.user_id,
            name=person.name,
            rank=None,
            performance=performance,
            open_tasks=person.open_tasks,
            reasons=_reasons(performance, person.open_tasks),
        )
        for person, performance in unscored
    ]
    return CandidateRanking(category=category, ranked=ranked, insufficient_data=insufficient)


def group_by_user(
    rows: Iterable[tuple[int, TaskOutcome]],
) -> dict[int, list[TaskOutcome]]:
    """Helper for callers that load outcomes for many employees at once."""
    grouped: dict[int, list[TaskOutcome]] = defaultdict(list)
    for user_id, outcome in rows:
        grouped[user_id].append(outcome)
    return dict(grouped)
