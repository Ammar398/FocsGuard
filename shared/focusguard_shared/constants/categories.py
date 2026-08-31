"""Application and website categories, and how they become a productivity verdict.

The central idea of spec section 22: a category is a *fact* about a site or app,
whereas a classification is a *judgement* that depends on what the user is
currently supposed to be doing. youtube.com is always ENTERTAINMENT-or-EDUCATION
by category; whether it is PRODUCTIVE depends entirely on the active task.
"""

from __future__ import annotations

from enum import StrEnum


class ActivityCategory(StrEnum):
    """What a domain or application *is*. Task-independent."""

    DEVELOPMENT = "DEVELOPMENT"
    DESIGN = "DESIGN"
    PRODUCTIVITY = "PRODUCTIVITY"
    EDUCATION = "EDUCATION"
    DOCUMENTATION = "DOCUMENTATION"
    COMMUNICATION = "COMMUNICATION"
    ENTERTAINMENT = "ENTERTAINMENT"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    SHOPPING = "SHOPPING"
    GAMING = "GAMING"
    NEWS = "NEWS"
    SYSTEM = "SYSTEM"

    #: Sites whose category cannot be decided from the domain alone. YouTube is
    #: the canonical example: it hosts both lecture material and entertainment.
    #: Resolving these needs task context, and until it is resolved the site is
    #: treated as NEUTRAL rather than assumed to be a distraction.
    CONTEXT_DEPENDENT = "CONTEXT_DEPENDENT"

    OTHER = "OTHER"


class Classification(StrEnum):
    """The productivity verdict for a span of activity (spec section 23)."""

    PRODUCTIVE = "PRODUCTIVE"
    NEUTRAL = "NEUTRAL"
    DISTRACTING = "DISTRACTING"


class RuleSource(StrEnum):
    """Which rule produced a classification, stored alongside every record.

    Every judgement the system makes must be explainable (spec sections 9, 41).
    Persisting the deciding rule is what lets the UI answer "why was this called
    distracting?" without re-running the classifier against rules that may since
    have changed.
    """

    TASK_RULE = "TASK_RULE"  # explicit rule on the active task - highest priority
    USER_OVERRIDE = "USER_OVERRIDE"  # student or organisation override
    CATEGORY_DEFAULT = "CATEGORY_DEFAULT"  # built-in category mapping
    UNCLASSIFIED = "UNCLASSIFIED"  # unknown app or domain - treated as NEUTRAL


class TaskCategory(StrEnum):
    """Kind of work a task represents.

    Used for employee strength analytics (spec section 30). Historical
    performance is only ever aggregated *within* a category, because comparing a
    design task against an API task would not be meaningful.
    """

    FRONTEND = "FRONTEND"
    BACKEND = "BACKEND"
    DESIGN = "DESIGN"
    DATABASE = "DATABASE"
    TESTING = "TESTING"
    DOCUMENTATION = "DOCUMENTATION"
    RESEARCH = "RESEARCH"
    DEVOPS = "DEVOPS"
    SUPPORT = "SUPPORT"
    MARKETING = "MARKETING"
    STUDY = "STUDY"
    OTHER = "OTHER"


#: Minimum completed tasks in a category before FocusGuard will make any
#: comparative statement about an employee's performance in it (spec section 30).
#: Below this, the UI must say "not enough data" rather than rank a person on a
#: single sample.
MIN_TASKS_FOR_STRENGTH_CLAIM: int = 3
