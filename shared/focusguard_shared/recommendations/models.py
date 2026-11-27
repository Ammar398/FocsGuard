"""What a recommendation is, and what the rules are allowed to look at.

EVERY RECOMMENDATION CARRIES ITS EVIDENCE
-----------------------------------------
Section 9 requires that the system explains *why*. Not as a sentence the rule
author wrote, which is just more prose - as the **actual numbers that triggered
it**, in a structure the interface must render.

That is why `Recommendation.evidence` is a required list rather than an optional
string. A rule cannot be written without stating what fired it, and the interface
cannot show advice with the reasoning hidden behind a tooltip.

It also makes disagreement possible. "You spent 51 minutes on youtube.com across
4 sessions" is checkable. A student who knows those were lecture recordings can
say so, and reclassify the domain. Advice without its evidence is just an
instruction.

CONFIDENCE, AND REFUSING TO GUESS
---------------------------------
Every rule declares how many sessions it needs. Below that it returns nothing
rather than something weak. Section 61 forbids claiming a user "is definitely
distracted" on one weak signal, and the same restraint applies to advice: one bad
Tuesday is not a pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from focusguard_shared.recommendations.vocabulary import assert_no_clinical_language


class RecommendationKind(StrEnum):
    """What a suggestion is about. Used for grouping and for icons."""

    ENVIRONMENT = "ENVIRONMENT"
    SCHEDULE = "SCHEDULE"
    BREAKS = "BREAKS"
    DIGITAL_HABITS = "DIGITAL_HABITS"
    REST = "REST"
    ENCOURAGEMENT = "ENCOURAGEMENT"


class Priority(StrEnum):
    """How prominently to show it. Never an urgency claim about the person."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class Evidence:
    """One measurement that contributed to a recommendation.

    `value` is pre-formatted text rather than a number, because the unit varies -
    "51 minutes", "4 of 6 sessions", "18 times". Keeping it a string means the
    interface never has to guess how to render a bare float.
    """

    label: str
    value: str


@dataclass(frozen=True)
class Recommendation:
    """One suggestion, with the data that produced it.

    Text is checked at construction: a rule that tries to diagnose fails in the
    test suite rather than in front of a student.
    """

    rule_id: str
    kind: RecommendationKind
    priority: Priority

    #: A short imperative or observation. Shown as the heading.
    title: str

    #: One or two sentences. Says what was seen and what might help.
    detail: str

    #: The numbers that fired the rule. Never empty - see the module docstring.
    evidence: list[Evidence] = field(default_factory=list)

    #: Concrete things to try, most useful first. Short, practical, optional.
    tips: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.evidence:
            raise ValueError(
                f"Rule {self.rule_id!r} produced a recommendation with no evidence. "
                "Section 9 requires every suggestion to show what triggered it."
            )
        assert_no_clinical_language(self.title, self.detail)
        for item in self.evidence:
            assert_no_clinical_language(item.label, item.value)
        for tip in self.tips:
            assert_no_clinical_language(tip)


@dataclass(frozen=True)
class SessionFacts:
    """One session, reduced to what the rules need.

    Deliberately not the ORM model or the API schema. A rule that could reach a
    database would be a rule that is hard to test and easy to make slow; this
    keeps every rule a pure function over plain data.
    """

    session_id: int
    started_at: date
    #: Hour of day the session began, 0-23. Local time, because advice about
    #: "working late" is meaningless in UTC.
    start_hour: int

    duration_seconds: float
    focused_seconds: float
    distracted_seconds: float
    average_focus_score: float

    #: Seconds per attention event type, e.g. {"PHONE_VISIBLE": 80.0}.
    event_seconds: dict[str, float] = field(default_factory=dict)
    #: Count per attention event type.
    event_counts: dict[str, int] = field(default_factory=dict)

    #: Seconds per application or domain label, all classifications together.
    activity_seconds: dict[str, float] = field(default_factory=dict)

    #: Seconds per label that were classified DISTRACTING specifically.
    #:
    #: Separate from `activity_seconds` because the two answer different
    #: questions, and conflating them produces exactly the wrong answer: the
    #: editor someone works in all day dominates total time in every session,
    #: so attributing distraction by total time names the tool they were
    #: working in. Attribution has to follow the classification.
    distracting_seconds_by_label: dict[str, float] = field(default_factory=dict)

    #: Seconds per classification: PRODUCTIVE / NEUTRAL / DISTRACTING.
    classification_seconds: dict[str, float] = field(default_factory=dict)

    #: The longest unbroken stretch with no attention event.
    longest_focus_block_seconds: float = 0.0


@dataclass
class FocusStatistics:
    """Everything the rules may look at, across a period.

    Built once by the caller from whatever store it has - the backend's database
    or the agent's local SQLite - so the rules run identically in both.
    """

    sessions: list[SessionFacts] = field(default_factory=list)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def total_seconds(self) -> float:
        return sum(session.duration_seconds for session in self.sessions)

    @property
    def focused_seconds(self) -> float:
        return sum(session.focused_seconds for session in self.sessions)

    @property
    def distracted_seconds(self) -> float:
        return sum(session.distracted_seconds for session in self.sessions)

    @property
    def average_focus_score(self) -> float:
        """Weighted by session length.

        A flat mean would let a two-minute session count as much as a two-hour
        one, which is how a single short bad session drags a good week down.
        """
        if not self.sessions:
            return 0.0
        weight = sum(session.duration_seconds for session in self.sessions)
        if weight <= 0:
            return sum(s.average_focus_score for s in self.sessions) / len(self.sessions)
        return (
            sum(s.average_focus_score * s.duration_seconds for s in self.sessions) / weight
        )

    def event_seconds(self, event_type: str) -> float:
        return sum(s.event_seconds.get(event_type, 0.0) for s in self.sessions)

    def event_count(self, event_type: str) -> int:
        return sum(s.event_counts.get(event_type, 0) for s in self.sessions)

    def sessions_with_event(self, event_type: str) -> int:
        """How many sessions saw this at all.

        More honest than a total for deciding whether something is a pattern: one
        session with forty phone appearances is a bad afternoon, four sessions
        with five each is a habit.
        """
        return sum(1 for s in self.sessions if s.event_counts.get(event_type, 0) > 0)

    def activity_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for session in self.sessions:
            for label, seconds in session.activity_seconds.items():
                totals[label] = totals.get(label, 0.0) + seconds
        return totals

    def classification_totals(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for session in self.sessions:
            for label, seconds in session.classification_seconds.items():
                totals[label] = totals.get(label, 0.0) + seconds
        return totals
