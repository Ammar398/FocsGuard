"""Recommendations, and the section 8 language guarantee.

The Phase 15 acceptance criterion has two halves:

* **every recommendation displays the data that triggered it** - enforced by the
  dataclass, which refuses to be constructed without evidence;
* **no medical or diagnostic vocabulary can appear in output** - proven here by
  running every rule across the whole input space, not by spot-checking a few
  strings. "Cannot appear" is a claim about all inputs, so the test says so.
"""

from __future__ import annotations

import random
from datetime import date

import pytest

from focusguard_shared.recommendations import (
    ALL_RULES,
    MIN_SESSIONS_FOR_ADVICE,
    ClinicalLanguageError,
    Evidence,
    FocusStatistics,
    Priority,
    Recommendation,
    RecommendationKind,
    SessionFacts,
    contains_clinical_language,
    find_clinical_language,
    generate_recommendations,
)

DAY = date(2026, 9, 1)


def _session(
    session_id: int = 1,
    *,
    hour: int = 14,
    minutes: float = 60.0,
    score: float = 75.0,
    focused: float | None = None,
    events: dict[str, tuple[float, int]] | None = None,
    activity: dict[str, float] | None = None,
    distracting_by_label: dict[str, float] | None = None,
    classifications: dict[str, float] | None = None,
    longest_block: float = 900.0,
) -> SessionFacts:
    duration = minutes * 60
    events = events or {}
    return SessionFacts(
        session_id=session_id,
        started_at=DAY,
        start_hour=hour,
        duration_seconds=duration,
        focused_seconds=duration * 0.7 if focused is None else focused,
        distracted_seconds=duration * 0.2,
        average_focus_score=score,
        event_seconds={name: value[0] for name, value in events.items()},
        event_counts={name: value[1] for name, value in events.items()},
        activity_seconds=activity or {},
        distracting_seconds_by_label=distracting_by_label or {},
        classification_seconds=classifications or {},
        longest_focus_block_seconds=longest_block,
    )


def _stats(*sessions: SessionFacts) -> FocusStatistics:
    return FocusStatistics(sessions=list(sessions))


# --- the vocabulary guard itself --------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "This may indicate ADHD.",
        "You might have attention deficit hyperactivity disorder.",
        "These are symptoms of a focus disorder.",
        "Consider speaking to a psychiatrist about medication.",
        "Your diagnosis suggests a treatment plan.",
        "You are easily distracted.",
        "You cannot focus for long.",
        "The data indicates you have a condition.",
    ],
)
def test_clinical_language_is_detected(text: str) -> None:
    assert contains_clinical_language(text), f"should have been caught: {text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "Your phone appeared in view 14 times.",
        "Sessions before 10pm scored higher than later ones.",
        "Slow, long blinks were detected in several sessions.",
        "Most off-task time went to youtube.com.",
        "Try putting your phone out of reach.",
        "This stretch went well.",
        # Substring traps: none of these contain a forbidden whole word.
        "The address of the page was not recorded.",
        "Adding a break often helps.",
        "An accurate measurement of focused time.",
        "Your score was added to the weekly total.",
    ],
)
def test_ordinary_language_is_not_flagged(text: str) -> None:
    found = find_clinical_language(text)
    assert found == [], f"false alarm on {text!r}: {found}"


def test_a_recommendation_that_diagnoses_cannot_be_constructed() -> None:
    """The guard runs at construction, so a bad rule fails in the test suite."""
    with pytest.raises(ClinicalLanguageError, match="section 8"):
        Recommendation(
            rule_id="bad",
            kind=RecommendationKind.REST,
            priority=Priority.LOW,
            title="You may have ADHD",
            detail="Consider a diagnosis.",
            evidence=[Evidence("Sessions", "5")],
        )


def test_evidence_cannot_be_omitted() -> None:
    """Section 9: advice without its reasoning is just an instruction."""
    with pytest.raises(ValueError, match="[Ss]ection 9"):
        Recommendation(
            rule_id="no_evidence",
            kind=RecommendationKind.BREAKS,
            priority=Priority.LOW,
            title="Take more breaks",
            detail="Because I said so.",
            evidence=[],
        )


# --- the acceptance criterion, across the whole input space -----------------


def test_no_rule_can_produce_clinical_language() -> None:
    """The section 8 guarantee, proven over randomised inputs rather than samples.

    Two thousand randomised statistics, every rule run against each. Any rule
    that could ever diagnose, pathologise or label a person fails here rather
    than in front of a student.
    """
    random.seed(815)
    event_types = [
        "PHONE_VISIBLE",
        "FACE_ABSENT",
        "DROWSINESS",
        "EYES_CLOSED",
        "GAZE_AWAY",
        "HEAD_TURNED",
    ]
    labels = ["youtube.com", "Visual Studio Code", "reddit.com", "Notion", "x.com"]

    checked = 0
    for case in range(2000):
        sessions = []
        for index in range(random.randint(0, 8)):
            minutes = random.uniform(1, 240)
            sessions.append(
                _session(
                    session_id=index,
                    hour=random.randint(0, 23),
                    minutes=minutes,
                    score=random.uniform(0, 100),
                    focused=random.uniform(0, minutes * 60),
                    events={
                        name: (random.uniform(0, minutes * 60), random.randint(0, 60))
                        for name in random.sample(event_types, random.randint(0, 4))
                    },
                    activity={
                        name: random.uniform(0, minutes * 60)
                        for name in random.sample(labels, random.randint(0, 4))
                    },
                    distracting_by_label={
                        name: random.uniform(0, minutes * 60)
                        for name in random.sample(labels, random.randint(0, 3))
                    },
                    classifications={
                        name: random.uniform(0, minutes * 60)
                        for name in ("PRODUCTIVE", "NEUTRAL", "DISTRACTING")
                    },
                    longest_block=random.uniform(0, minutes * 60),
                )
            )

        stats = _stats(*sessions)

        # Every rule directly, not just the capped output of the generator -
        # otherwise a rule that never survives the priority cut goes unchecked.
        for rule in ALL_RULES:
            recommendation = rule(stats)
            if recommendation is None:
                continue
            checked += 1

            for text in (recommendation.title, recommendation.detail):
                found = find_clinical_language(text)
                assert found == [], (
                    f"case {case}, rule {recommendation.rule_id}: {found} in {text!r}"
                )
            for item in recommendation.evidence:
                assert not contains_clinical_language(item.label)
                assert not contains_clinical_language(item.value)

    assert checked > 500, f"only {checked} recommendations exercised; widen the fuzz"


def test_every_rule_fires_at_least_once_under_fuzzing() -> None:
    """A rule that never fires is a rule the language test never checked."""
    random.seed(24)
    fired: set[str] = set()

    for _ in range(3000):
        count = random.randint(0, 8)
        minutes = random.uniform(1, 200)
        sessions = [
            _session(
                session_id=index,
                hour=random.choice([9, 11, 14, 16, 22, 23, 1]),
                minutes=minutes,
                score=random.uniform(30, 100),
                events={
                    "PHONE_VISIBLE": (random.uniform(0, 600), random.randint(0, 40)),
                    "FACE_ABSENT": (random.uniform(0, minutes * 40), random.randint(0, 10)),
                    "DROWSINESS": (random.uniform(0, minutes * 20), random.randint(0, 8)),
                },
                activity={"youtube.com": random.uniform(0, minutes * 40)},
                distracting_by_label={"youtube.com": random.uniform(0, minutes * 40)},
                classifications={"DISTRACTING": random.uniform(0, minutes * 40)},
                longest_block=random.uniform(0, 1200),
            )
            for index in range(count)
        ]
        for rule in ALL_RULES:
            result = rule(_stats(*sessions))
            if result is not None:
                fired.add(result.rule_id)

    expected = {
        "not_enough_data",
        "phone_in_reach",
        "frequent_interruptions",
        "signs_of_tiredness",
        "dominant_distraction",
        "sessions_without_breaks",
        "better_time_of_day",
        "going_well",
    }
    assert fired == expected, f"never fired: {sorted(expected - fired)}"


# --- refusing to guess ------------------------------------------------------


def test_too_few_sessions_says_so_rather_than_advising() -> None:
    """Section 61's restraint applies to advice: one day is not a pattern."""
    stats = _stats(
        _session(1, events={"PHONE_VISIBLE": (600.0, 30)}),
        _session(2, events={"PHONE_VISIBLE": (600.0, 30)}),
    )

    results = generate_recommendations(stats)

    assert len(results) == 1
    assert results[0].rule_id == "not_enough_data"
    assert "pattern" in results[0].detail


def test_the_not_enough_data_notice_disappears_once_there_is_enough() -> None:
    stats = _stats(*[_session(index) for index in range(MIN_SESSIONS_FOR_ADVICE)])

    ids = {item.rule_id for item in generate_recommendations(stats)}

    assert "not_enough_data" not in ids


# --- individual rules -------------------------------------------------------


def test_a_phone_habit_is_reported_with_its_rate() -> None:
    stats = _stats(
        *[
            _session(index, minutes=60, events={"PHONE_VISIBLE": (300.0, 8)})
            for index in range(4)
        ]
    )

    result = next(r for r in generate_recommendations(stats) if r.rule_id == "phone_in_reach")

    assert "out of reach" in result.title
    values = {item.label: item.value for item in result.evidence}
    assert values["Times the phone was visible"] == "32"
    assert values["Sessions affected"] == "4 of 4"


def test_one_bad_session_is_not_called_a_phone_habit() -> None:
    """Concentrated in a single session is an afternoon, not a habit."""
    stats = _stats(
        _session(1, minutes=60, events={"PHONE_VISIBLE": (900.0, 40)}),
        _session(2, minutes=60),
        _session(3, minutes=60),
        _session(4, minutes=60),
        _session(5, minutes=60),
    )

    ids = {r.rule_id for r in generate_recommendations(stats)}

    assert "phone_in_reach" not in ids


def test_the_dominant_distraction_is_named() -> None:
    """"You were distracted" is not actionable; naming the site is."""
    stats = _stats(
        *[
            _session(
                index,
                minutes=60,
                activity={"youtube.com": 1800.0, "Visual Studio Code": 900.0},
                distracting_by_label={"youtube.com": 1800.0},
                classifications={"DISTRACTING": 1800.0},
            )
            for index in range(3)
        ]
    )

    result = next(
        r for r in generate_recommendations(stats) if r.rule_id == "dominant_distraction"
    )

    assert "youtube.com" in result.title
    assert "change how it is classified" in result.detail, "the user can disagree"


def test_a_time_of_day_claim_needs_both_groups() -> None:
    """Comparing one late session with one early one is noise dressed as insight."""
    only_one_late = _stats(
        _session(1, hour=9, score=85),
        _session(2, hour=10, score=85),
        _session(3, hour=23, score=40),
    )

    ids = {r.rule_id for r in generate_recommendations(only_one_late)}

    assert "better_time_of_day" not in ids


def test_a_time_of_day_claim_is_made_when_the_gap_is_real() -> None:
    stats = _stats(
        _session(1, hour=9, score=86),
        _session(2, hour=11, score=84),
        _session(3, hour=23, score=52),
        _session(4, hour=23, score=48),
    )

    result = next(
        r for r in generate_recommendations(stats) if r.rule_id == "better_time_of_day"
    )

    values = {item.label: item.value for item in result.evidence}
    assert values["Average before 10pm"] == "85 / 100"
    assert values["Average after 10pm"] == "50 / 100"
    assert values["Difference"] == "35 points"


def test_a_small_time_of_day_difference_is_not_reported() -> None:
    stats = _stats(
        _session(1, hour=9, score=80),
        _session(2, hour=11, score=79),
        _session(3, hour=23, score=77),
        _session(4, hour=23, score=76),
    )

    ids = {r.rule_id for r in generate_recommendations(stats)}

    assert "better_time_of_day" not in ids


def test_good_weeks_are_acknowledged() -> None:
    """A tool that only ever reports problems is one people stop opening."""
    stats = _stats(*[_session(index, score=88) for index in range(4)])

    ids = {r.rule_id for r in generate_recommendations(stats)}

    assert "going_well" in ids


# --- the shape of the output ------------------------------------------------


def test_every_recommendation_carries_evidence() -> None:
    """The section 9 acceptance criterion, over every rule that can fire."""
    random.seed(9)
    seen = 0

    for _ in range(400):
        sessions = [
            _session(
                index,
                hour=random.choice([9, 14, 23]),
                minutes=random.uniform(10, 180),
                score=random.uniform(20, 100),
                events={
                    "PHONE_VISIBLE": (random.uniform(0, 900), random.randint(0, 30)),
                    "FACE_ABSENT": (random.uniform(0, 1800), random.randint(0, 12)),
                    "DROWSINESS": (random.uniform(0, 900), random.randint(0, 6)),
                },
                activity={"youtube.com": random.uniform(0, 3600)},
                distracting_by_label={"youtube.com": random.uniform(0, 3600)},
                classifications={"DISTRACTING": random.uniform(0, 3600)},
                longest_block=random.uniform(0, 900),
            )
            for index in range(random.randint(0, 6))
        ]
        for recommendation in generate_recommendations(_stats(*sessions), limit=10):
            seen += 1
            assert recommendation.evidence, recommendation.rule_id
            assert all(item.label and item.value for item in recommendation.evidence)

    assert seen > 100


def test_the_list_is_capped_and_ordered_by_priority() -> None:
    """Eight suggestions at once is a list nobody acts on."""
    stats = _stats(
        *[
            _session(
                index,
                minutes=90,
                score=40,
                events={
                    "PHONE_VISIBLE": (900.0, 20),
                    "FACE_ABSENT": (1500.0, 9),
                    "DROWSINESS": (700.0, 5),
                },
                activity={"youtube.com": 2400.0},
                distracting_by_label={"youtube.com": 2400.0},
                classifications={"DISTRACTING": 2400.0},
                longest_block=60.0,
            )
            for index in range(5)
        ]
    )

    results = generate_recommendations(stats, limit=3)

    assert len(results) == 3
    order = [Priority.HIGH, Priority.MEDIUM, Priority.LOW]
    positions = [order.index(item.priority) for item in results]
    assert positions == sorted(positions)


def test_the_weighted_average_is_not_dragged_by_a_short_session() -> None:
    """A two-minute session must not count as much as a two-hour one."""
    stats = _stats(
        _session(1, minutes=120, score=85),
        _session(2, minutes=2, score=10),
    )

    assert stats.average_focus_score > 80, "a flat mean would give 47.5"


def test_the_tool_you_work_in_is_not_named_as_the_distraction() -> None:
    """A regression caught by running this against real data.

    The first version attributed distraction to whichever label had the most
    time in sessions that contained *any* distracting time. An editor open all
    day dominates total time in every session, so the rule confidently named
    Visual Studio Code as the thing pulling someone off task, while the actual
    distracting span was a fraction of the size.

    Attribution has to follow the classification, not the clock.
    """
    stats = _stats(
        *[
            _session(
                index,
                minutes=120,
                # The editor dominates by total time, five to one.
                activity={"Visual Studio Code": 6000.0, "youtube.com": 1200.0},
                # But only YouTube was classified as distracting.
                distracting_by_label={"youtube.com": 1200.0},
                classifications={"PRODUCTIVE": 6000.0, "DISTRACTING": 1200.0},
            )
            for index in range(4)
        ]
    )

    result = next(
        r for r in generate_recommendations(stats, limit=8)
        if r.rule_id == "dominant_distraction"
    )

    assert "youtube.com" in result.title
    assert "Visual Studio Code" not in result.title
    assert "Visual Studio Code" not in str(result.evidence)


def test_nothing_classified_distracting_produces_no_claim() -> None:
    """A productive week must not have a distraction named anyway."""
    stats = _stats(
        *[
            _session(
                index,
                minutes=90,
                activity={"Visual Studio Code": 5400.0},
                classifications={"PRODUCTIVE": 5400.0},
            )
            for index in range(4)
        ]
    )

    ids = {r.rule_id for r in generate_recommendations(stats, limit=8)}

    assert "dominant_distraction" not in ids
