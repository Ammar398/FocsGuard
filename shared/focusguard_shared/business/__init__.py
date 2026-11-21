"""Business-mode logic shared by the backend and tests (spec sections 29-31).

Pure functions over plain dataclasses, in the same style as
`focusguard_shared.recommendations`: no database, no clock, so the rules that
decide what a manager is told about an employee can be tested exhaustively.
"""

from focusguard_shared.business.strengths import (
    DEFAULT_WEIGHTS,
    Candidate,
    CandidateInput,
    CandidateRanking,
    CategoryPerformance,
    StrengthWeights,
    TaskOutcome,
    category_label,
    category_performance,
    employee_profile,
    rank_candidates,
    strength_statements,
)
from focusguard_shared.business.task_scoring import (
    NEUTRAL_WEIGHT,
    TaskScores,
    TaskSessionFacts,
    productivity_score,
    score_task,
)
from focusguard_shared.business.vocabulary import (
    JudgementalLanguageError,
    assert_comparative_language,
    find_judgemental_language,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "NEUTRAL_WEIGHT",
    "Candidate",
    "CandidateInput",
    "CandidateRanking",
    "CategoryPerformance",
    "JudgementalLanguageError",
    "StrengthWeights",
    "TaskOutcome",
    "TaskScores",
    "TaskSessionFacts",
    "assert_comparative_language",
    "category_label",
    "category_performance",
    "employee_profile",
    "find_judgemental_language",
    "productivity_score",
    "rank_candidates",
    "score_task",
    "strength_statements",
]
