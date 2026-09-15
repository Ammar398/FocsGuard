"""Rule-based recommendations with evidence attached (spec sections 9, 43).

Pure functions over plain data, so the same rules run in the backend against the
database and in the agent against its local store, and cannot disagree.
"""

from focusguard_shared.recommendations.models import (
    Evidence,
    FocusStatistics,
    Priority,
    Recommendation,
    RecommendationKind,
    SessionFacts,
)
from focusguard_shared.recommendations.rules import (
    ALL_RULES,
    MIN_SESSIONS_FOR_ADVICE,
    generate_recommendations,
)
from focusguard_shared.recommendations.vocabulary import (
    FORBIDDEN_TERMS,
    ClinicalLanguageError,
    assert_no_clinical_language,
    contains_clinical_language,
    find_clinical_language,
)

__all__ = [
    "ALL_RULES",
    "FORBIDDEN_TERMS",
    "MIN_SESSIONS_FOR_ADVICE",
    "ClinicalLanguageError",
    "Evidence",
    "FocusStatistics",
    "Priority",
    "Recommendation",
    "RecommendationKind",
    "SessionFacts",
    "assert_no_clinical_language",
    "contains_clinical_language",
    "find_clinical_language",
    "generate_recommendations",
]
