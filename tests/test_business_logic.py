"""Task scoring, strength analytics and assignee ranking (spec sections 29-31).

Pure-function tests: no database, no clock. The two properties that matter most
for a defence are proven by exhaustive-ish fuzzing rather than examples:

* below the minimum sample, nobody is ever scored or ranked;
* no generated sentence about an employee ever contains judgemental language.
"""

from __future__ import annotations

import random

import pytest

from focusguard_shared.business import (
    CandidateInput,
    JudgementalLanguageError,
    TaskOutcome,
    TaskSessionFacts,
    assert_comparative_language,
    category_performance,
    employee_profile,
    find_judgemental_language,
    productivity_score,
    rank_candidates,
    score_task,
    strength_statements,
)
from focusguard_shared.business.strengths import estimate_component, rework_component
from focusguard_shared.constants import MIN_TASKS_FOR_STRENGTH_CLAIM, TaskCategory

# --- task scoring -----------------------------------------------------------


def test_a_task_with_no_sessions_has_no_scores_not_zero() -> None:
    """Zero would claim 'worked with no focus'. None says 'not measured'."""
    scores = score_task([], expected_minutes=60)

    assert scores.task_focus_score is None
    assert scores.task_productivity_score is None
    assert scores.duration_ratio is None
    assert "No monitored sessions" in scores.explanation[0]


def test_task_focus_is_weighted_by_session_length() -> None:
    scores = score_task(
        [
            TaskSessionFacts(duration_seconds=3 * 3600, average_focus_score=90),
            TaskSessionFacts(duration_seconds=300, average_focus_score=10),
        ]
    )
    # A flat mean would say 50. Three hours at 90 should dominate five minutes.
    assert scores.task_focus_score == pytest.approx(
        (90 * 3 * 3600 + 10 * 300) / (3 * 3600 + 300)
    )
    assert scores.task_focus_score > 85


def test_task_productivity_matches_the_agents_dps_formula() -> None:
    scores = score_task(
        [
            TaskSessionFacts(
                duration_seconds=3600,
                average_focus_score=80,
                productive_seconds=2400,
                neutral_seconds=600,
                distracting_seconds=600,
            )
        ]
    )
    assert scores.task_productivity_score == pytest.approx(100 * (2400 + 300) / 3600)
    assert productivity_score(2400, 600, 600) == pytest.approx(75.0)


def test_unclassified_activity_gives_no_productivity_score() -> None:
    assert productivity_score(0, 0, 0) is None


def test_duration_ratio_compares_monitored_time_to_the_estimate() -> None:
    scores = score_task(
        [TaskSessionFacts(duration_seconds=90 * 60, average_focus_score=70)],
        expected_minutes=60,
    )
    assert scores.duration_ratio == pytest.approx(1.5)
    assert any("150%" in line for line in scores.explanation)


def test_zero_length_sessions_are_ignored() -> None:
    scores = score_task([TaskSessionFacts(duration_seconds=0, average_focus_score=100)])
    assert scores.session_count == 0
    assert scores.task_focus_score is None


# --- components -------------------------------------------------------------


def test_finishing_under_estimate_is_not_rewarded_beyond_100() -> None:
    assert estimate_component(0.4) == 100.0
    assert estimate_component(1.0) == 100.0
    assert estimate_component(1.5) == pytest.approx(50.0)
    assert estimate_component(3.0) == 0.0


def test_rework_reduces_its_component_and_floors_at_zero() -> None:
    assert rework_component(0) == 100.0
    assert rework_component(1) == 75.0
    assert rework_component(10) == 0.0


# --- category performance ---------------------------------------------------


def _outcome(task_id: int, category: str = "BACKEND", **kwargs) -> TaskOutcome:
    defaults = {
        "quality_rating": 4.0,
        "on_time": True,
        "duration_ratio": 1.0,
        "focus_score": 80.0,
        "productivity_score": 75.0,
        "rework_count": 0,
    }
    defaults.update(kwargs)
    return TaskOutcome(task_id=task_id, category=category, **defaults)


def test_below_the_minimum_sample_there_is_no_score() -> None:
    outcomes = [_outcome(i) for i in range(MIN_TASKS_FOR_STRENGTH_CLAIM - 1)]

    performance = category_performance("BACKEND", outcomes)

    assert performance.sufficient_data is False
    assert performance.historical_score is None
    # The averages are still reported, so the manager sees the raw history.
    assert performance.average_quality == pytest.approx(4.0)


def test_at_the_minimum_sample_a_score_appears() -> None:
    outcomes = [_outcome(i) for i in range(MIN_TASKS_FOR_STRENGTH_CLAIM)]
    performance = category_performance("BACKEND", outcomes)

    assert performance.sufficient_data is True
    assert performance.historical_score is not None
    assert set(performance.components) == {
        "quality", "on_time", "estimate", "rework", "focus", "productivity",
    }


def test_missing_components_are_left_out_not_counted_as_zero() -> None:
    """No task had a deadline: on_time must not count as 0% on time."""
    outcomes = [
        _outcome(i, on_time=None, focus_score=None, productivity_score=None, quality_rating=5.0)
        for i in range(3)
    ]
    performance = category_performance("BACKEND", outcomes)

    assert "on_time" not in performance.components
    assert "focus" not in performance.components
    # quality 100, estimate 100, rework 100 -> 100, not dragged down by absences.
    assert performance.historical_score == pytest.approx(100.0)


def test_quality_outweighs_focus() -> None:
    """Outcome measures carry more weight than monitoring measures (section 30)."""
    careful = [_outcome(i, quality_rating=5.0, focus_score=60.0) for i in range(3)]
    watched = [_outcome(i, quality_rating=3.0, focus_score=100.0) for i in range(3)]

    assert (
        category_performance("BACKEND", careful).historical_score
        > category_performance("BACKEND", watched).historical_score
    )


def test_categories_are_never_mixed() -> None:
    outcomes = [_outcome(i, "BACKEND", quality_rating=5.0) for i in range(3)] + [
        _outcome(10 + i, "DESIGN", quality_rating=2.0) for i in range(3)
    ]
    assert category_performance("BACKEND", outcomes).average_quality == 5.0
    assert category_performance("DESIGN", outcomes).average_quality == 2.0


def test_profile_lists_scored_categories_first_strongest_first() -> None:
    outcomes = (
        [_outcome(i, "DESIGN", quality_rating=3.0) for i in range(3)]
        + [_outcome(10 + i, "BACKEND", quality_rating=5.0) for i in range(4)]
        + [_outcome(20, "TESTING")]
    )
    profile = employee_profile(outcomes)

    assert [p.category for p in profile] == ["BACKEND", "DESIGN", "TESTING"]
    assert profile[-1].historical_score is None


# --- statements -------------------------------------------------------------


def test_statements_compare_history_rather_than_judging() -> None:
    outcomes = [_outcome(i, "FRONTEND", quality_rating=4.8) for i in range(4)] + [
        _outcome(10 + i, "BACKEND", quality_rating=3.2, on_time=False) for i in range(3)
    ]
    statements = strength_statements("Employee A", employee_profile(outcomes))

    assert statements[0].startswith("Historical task data suggests Employee A")
    assert "more strongly on frontend assignments" in statements[0]
    for text in statements:
        assert find_judgemental_language(text) == []


def test_close_scores_are_described_as_similar() -> None:
    outcomes = [_outcome(i, "FRONTEND") for i in range(3)] + [
        _outcome(10 + i, "BACKEND") for i in range(3)
    ]
    statements = strength_statements("Sam", employee_profile(outcomes))
    assert "similar level" in statements[0]


def test_no_history_says_so() -> None:
    statements = strength_statements("New Hire", employee_profile([_outcome(1)]))
    assert "not yet enough completed-task history" in statements[0]
    assert "backend (1)" in statements[-1]


def test_a_name_containing_a_judgement_word_does_not_crash_the_profile() -> None:
    """Names are user input. 'Bad' is a real surname."""
    statements = strength_statements("Ahmed Bad", employee_profile([_outcome(1)]))
    assert "Ahmed Bad" in statements[0]


def test_the_vocabulary_check_catches_judgements() -> None:
    with pytest.raises(JudgementalLanguageError):
        assert_comparative_language("Employee A is bad at backend.")
    with pytest.raises(JudgementalLanguageError):
        assert_comparative_language("A weak performer on design work.")
    with pytest.raises(JudgementalLanguageError):
        assert_comparative_language("Sam is not suited to API work.")
    # Clinical language is forbidden here too.
    with pytest.raises(JudgementalLanguageError):
        assert_comparative_language("This may indicate ADHD.")


def _random_outcomes(rng: random.Random, count: int) -> list[TaskOutcome]:
    categories = [c.value for c in TaskCategory]
    return [
        TaskOutcome(
            task_id=index,
            category=rng.choice(categories),
            quality_rating=rng.choice([None, rng.uniform(1, 5)]),
            on_time=rng.choice([None, True, False]),
            duration_ratio=rng.choice([None, rng.uniform(0.1, 4)]),
            focus_score=rng.choice([None, rng.uniform(0, 100)]),
            productivity_score=rng.choice([None, rng.uniform(0, 100)]),
            rework_count=rng.randint(0, 6),
        )
        for index in range(count)
    ]


def test_fuzzed_profiles_never_produce_judgemental_language() -> None:
    """'Cannot appear' is a claim about every input, so it is tested on many."""
    rng = random.Random(30)
    for _ in range(1500):
        outcomes = _random_outcomes(rng, rng.randint(0, 25))
        profile = employee_profile(outcomes)
        for text in strength_statements("Employee", profile):
            assert find_judgemental_language(text) == [], text

        for performance in profile:
            if performance.historical_score is not None:
                assert 0.0 <= performance.historical_score <= 100.0
                assert performance.completed_tasks >= MIN_TASKS_FOR_STRENGTH_CLAIM


# --- ranking (spec section 31) ----------------------------------------------


def test_candidates_are_ranked_by_historical_score_with_reasons() -> None:
    strong = [_outcome(i, quality_rating=4.9) for i in range(4)]
    steady = [_outcome(10 + i, quality_rating=3.5) for i in range(3)]
    new = [_outcome(20)]

    ranking = rank_candidates(
        "BACKEND",
        [
            CandidateInput(1, "Employee A", steady, open_tasks=0),
            CandidateInput(2, "Employee B", strong, open_tasks=3),
            CandidateInput(3, "Employee C", new, open_tasks=0),
        ],
    )

    assert [c.name for c in ranking.ranked] == ["Employee B", "Employee A"]
    assert [c.rank for c in ranking.ranked] == [1, 2]
    assert ranking.ranked[0].reasons[0].startswith("Backend historical score")
    assert "Currently 3 open task(s)" in ranking.ranked[0].reasons

    # Not enough history: listed, explained, never ranked.
    assert [c.name for c in ranking.insufficient_data] == ["Employee C"]
    assert ranking.insufficient_data[0].rank is None
    assert "not a decision" in ranking.notice


def test_ties_prefer_more_evidence_then_lighter_workload() -> None:
    same = [_outcome(i) for i in range(3)]
    more = [_outcome(i) for i in range(5)]

    ranking = rank_candidates(
        "BACKEND",
        [
            CandidateInput(1, "Busy", same, open_tasks=4),
            CandidateInput(2, "Free", same, open_tasks=0),
            CandidateInput(3, "Experienced", more, open_tasks=9),
        ],
    )
    assert [c.name for c in ranking.ranked] == ["Experienced", "Free", "Busy"]


def test_fuzzed_rankings_only_rank_sufficient_history() -> None:
    rng = random.Random(31)
    for _ in range(400):
        people = [
            CandidateInput(i, f"Person {i}", _random_outcomes(rng, rng.randint(0, 15)), i % 3)
            for i in range(rng.randint(1, 6))
        ]
        category = rng.choice([c.value for c in TaskCategory])
        ranking = rank_candidates(category, people)

        scores = [c.performance.historical_score for c in ranking.ranked]
        assert scores == sorted(scores, reverse=True)
        for candidate in ranking.ranked:
            assert candidate.performance.completed_tasks >= MIN_TASKS_FOR_STRENGTH_CLAIM
        for candidate in ranking.ranked + ranking.insufficient_data:
            for reason in candidate.reasons:
                assert find_judgemental_language(reason) == []
