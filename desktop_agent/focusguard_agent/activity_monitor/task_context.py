"""Task-aware productivity classification (spec sections 22, 23, 28).

THE PROBLEM THIS SOLVES
-----------------------
*"Do not classify YouTube as automatically unproductive. If task = 'Watch Python
Tutorial', YouTube can be productive. If task = 'Implement Authentication' and
the employee spends 45 minutes watching entertainment, it may be distracting.
Productivity must therefore be TASK CONTEXT AWARE."*

Nothing before this module knows what the user is supposed to be doing. The
classifier can say *Figma is DESIGN*; only a task can say whether design work is
what should be happening right now.

PRECEDENCE
----------
    1. explicit task rule      "figma.com is productive for this task"
    2. task category affinity  a BACKEND task makes DEVELOPMENT tools productive
    3. user or org override    "I count Spotify as neutral"
    4. category default        ENTERTAINMENT is distracting
    5. unclassified            neutral

Category affinity (2) exists so a manager creating a task does not have to
enumerate every tool a developer might open. Saying the task is BACKEND is enough
to make an IDE, a terminal, and documentation productive.

WHAT IS DELIBERATELY NOT INFERRED
---------------------------------
A `CONTEXT_DEPENDENT` site - YouTube, Google, an AI chat - resolves to **NEUTRAL**
unless a task rule names it. FocusGuard does not try to guess whether a YouTube
page is a lecture or entertainment: the domain cannot tell us, and the page title
is not collected by default. Guessing would produce exactly the confident-but-
wrong verdict section 61 forbids.

That is a real limitation, and the honest response to it is the recommendation
engine (Phase 15): *"18 minutes went to youtube.com during this session - if that
was entertainment, consider adding it to your distraction list"*. The user
decides; the system does not pretend to know.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from focusguard_agent.activity_monitor.classifier import (
    ActivityClassifier,
    ClassifiedActivity,
)
from focusguard_shared.constants import (
    ActivityCategory,
    Classification,
    RuleSource,
    TaskCategory,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("activity.task_context")

#: Which activity categories a task of each kind makes productive, so a task does
#: not have to list every tool by name (spec section 28).
TASK_CATEGORY_AFFINITY: dict[TaskCategory, frozenset[ActivityCategory]] = {
    TaskCategory.FRONTEND: frozenset(
        {ActivityCategory.DEVELOPMENT, ActivityCategory.DESIGN, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.BACKEND: frozenset(
        {ActivityCategory.DEVELOPMENT, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.DATABASE: frozenset(
        {ActivityCategory.DEVELOPMENT, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.DEVOPS: frozenset(
        {ActivityCategory.DEVELOPMENT, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.TESTING: frozenset(
        {ActivityCategory.DEVELOPMENT, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.DESIGN: frozenset(
        {ActivityCategory.DESIGN, ActivityCategory.DOCUMENTATION}
    ),
    TaskCategory.DOCUMENTATION: frozenset(
        {ActivityCategory.DOCUMENTATION, ActivityCategory.PRODUCTIVITY}
    ),
    TaskCategory.RESEARCH: frozenset(
        {
            ActivityCategory.DOCUMENTATION,
            ActivityCategory.EDUCATION,
            ActivityCategory.PRODUCTIVITY,
        }
    ),
    TaskCategory.STUDY: frozenset(
        {
            ActivityCategory.EDUCATION,
            ActivityCategory.DOCUMENTATION,
            ActivityCategory.PRODUCTIVITY,
        }
    ),
    TaskCategory.MARKETING: frozenset(
        {ActivityCategory.DESIGN, ActivityCategory.PRODUCTIVITY}
    ),
    TaskCategory.SUPPORT: frozenset(
        {ActivityCategory.COMMUNICATION, ActivityCategory.DOCUMENTATION}
    ),
}


@dataclass
class TaskContext:
    """What the user is currently supposed to be doing (spec section 28)."""

    name: str
    category: TaskCategory = TaskCategory.OTHER

    #: Explicit rules. Matched case-insensitively against application names and
    #: domains alike, so a task can name either.
    productive: set[str] = field(default_factory=set)
    neutral: set[str] = field(default_factory=set)
    distracting: set[str] = field(default_factory=set)

    expected_minutes: int | None = None

    #: The backend task id when this context came from an assigned task
    #: (Milestone E). None for a student's self-described task.
    task_id: int | None = None

    def __post_init__(self) -> None:
        self.productive = {item.lower() for item in self.productive}
        self.neutral = {item.lower() for item in self.neutral}
        self.distracting = {item.lower() for item in self.distracting}

    def rule_for(self, *subjects: str | None) -> Classification | None:
        """Return an explicit verdict for any of these subjects, if one exists.

        Distracting is checked first: if a task both lists a site as productive
        and marks it distracting, the restrictive reading is the safer one to
        honour, and it is more likely to be the deliberate entry.
        """
        candidates = [subject.lower() for subject in subjects if subject]

        for subject in candidates:
            if subject in self.distracting:
                return Classification.DISTRACTING
        for subject in candidates:
            if subject in self.productive:
                return Classification.PRODUCTIVE
        for subject in candidates:
            if subject in self.neutral:
                return Classification.NEUTRAL
        return None


def task_context_from_assignment(assignment: dict) -> TaskContext:
    """Build a context from a task as `GET /agent/tasks` returns it.

    The manager's rules become the explicit task rules, and the task category
    supplies the affinity defaults - so a BACKEND task makes an IDE productive
    even if the manager never listed one.
    """
    try:
        category = TaskCategory(str(assignment.get("category", "OTHER")).upper())
    except ValueError:
        category = TaskCategory.OTHER

    return TaskContext(
        name=str(assignment["title"]),
        category=category,
        productive=set(assignment.get("productive_rules") or []),
        neutral=set(assignment.get("neutral_rules") or []),
        distracting=set(assignment.get("distracting_rules") or []),
        expected_minutes=assignment.get("expected_minutes"),
        task_id=int(assignment["id"]),
    )


class TaskContextEngine:
    """Applies task context on top of the base classification."""

    def __init__(
        self,
        classifier: ActivityClassifier | None = None,
        task: TaskContext | None = None,
    ) -> None:
        self._classifier = classifier or ActivityClassifier()
        self._task = task

    @property
    def task(self) -> TaskContext | None:
        return self._task

    def set_task(self, task: TaskContext | None) -> None:
        """Start or clear the active task."""
        self._task = task
        if task:
            logger.info("Task context set: %r (%s)", task.name, task.category)
        else:
            logger.info("Task context cleared")

    def classify(
        self,
        application_name: str,
        domain: str | None = None,
    ) -> ClassifiedActivity:
        """Classify an activity, applying task context where one is active."""
        base = self._classifier.classify(application_name, domain=domain)

        if self._task is None:
            return base

        # 1. An explicit rule on the task beats everything.
        explicit = self._task.rule_for(domain, application_name)
        if explicit is not None:
            return ClassifiedActivity(
                application_name=base.application_name,
                category=base.category,
                classification=explicit,
                rule_source=RuleSource.TASK_RULE,
                domain=domain,
            )

        # 2. Category affinity: a BACKEND task makes development tools productive
        # without the task having to list every one of them.
        affinity = TASK_CATEGORY_AFFINITY.get(self._task.category, frozenset())
        if base.category in affinity:
            return ClassifiedActivity(
                application_name=base.application_name,
                category=base.category,
                classification=Classification.PRODUCTIVE,
                rule_source=RuleSource.TASK_RULE,
                domain=domain,
            )

        # 3-5. No task opinion, so the base verdict stands. Note that a
        # CONTEXT_DEPENDENT site stays NEUTRAL here rather than being guessed at.
        return base

    def explain(self, activity: ClassifiedActivity) -> str:
        """Why this verdict, in words a user can check."""
        subject = activity.domain or activity.application_name

        if activity.rule_source is RuleSource.TASK_RULE and self._task is not None:
            if self._task.rule_for(activity.domain, activity.application_name) is not None:
                return f"{subject} is listed on the task {self._task.name!r}"
            return (
                f"{subject} is {activity.category}, which counts as work for a "
                f"{self._task.category} task"
            )

        return activity.explain()
