"""Normalisation and classification of activity (spec sections 19, 22, 23, 58).

THE PIPELINE THIS IMPLEMENTS
----------------------------
    Code.exe                    raw process name
      -> Visual Studio Code     ActivityNormalizer  - what a person calls it
      -> DEVELOPMENT            ActivityClassifier  - what it IS
      -> PRODUCTIVE             (task context, Phase 10) - what it is WORTH

The first two steps are here. The third is deliberately not: whether an
application is productive depends on what the user is meant to be doing, and this
module has no idea about that.

FACT VERSUS JUDGEMENT
---------------------
A **category** is task-independent. Photoshop is DESIGN whoever is running it.

A **classification** is a judgement. Photoshop is productive for "Design the
landing page" and a distraction during "Fix the login bug". Until Phase 10
supplies a task, this module falls back to the category defaults - and records
`RuleSource.CATEGORY_DEFAULT` so the UI can say *why* something was called
distracting, rather than presenting a verdict with no reasoning (sections 9, 41).

UNKNOWN IS NEUTRAL, NEVER DISTRACTING
-------------------------------------
An application nobody catalogued is `OTHER`, classified NEUTRAL, sourced
`UNCLASSIFIED`. Guessing that unfamiliar software is a distraction would penalise
users for niche tools - a designer's plugin, a university's exam client - and
section 22 is explicit that assumptions like that are not acceptable.
"""

from __future__ import annotations

from dataclasses import dataclass

from focusguard_shared.constants import ActivityCategory, Classification, RuleSource
from focusguard_shared.constants.catalog import (
    APPLICATION_CATEGORIES,
    APPLICATION_NAMES,
    CATEGORY_DEFAULT_CLASSIFICATION,
    DOMAIN_CATEGORIES,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("activity.classifier")


@dataclass(frozen=True)
class ClassifiedActivity:
    """An application or domain, named, categorised and judged."""

    application_name: str
    category: ActivityCategory
    classification: Classification
    rule_source: RuleSource

    #: Domain, when the activity came from a browser (Phase 10).
    domain: str | None = None

    @property
    def is_productive(self) -> bool:
        return self.classification is Classification.PRODUCTIVE

    @property
    def is_distracting(self) -> bool:
        return self.classification is Classification.DISTRACTING

    def explain(self) -> str:
        """Human-readable reason, shown wherever a verdict is displayed."""
        subject = self.domain or self.application_name
        reasons = {
            RuleSource.TASK_RULE: f"{subject} is listed on the current task",
            RuleSource.USER_OVERRIDE: f"you classified {subject} yourself",
            RuleSource.CATEGORY_DEFAULT: f"{subject} is categorised as {self.category}",
            RuleSource.UNCLASSIFIED: f"{subject} is not categorised, so it counts as neutral",
        }
        return reasons[self.rule_source]


class ActivityNormalizer:
    """Turns a raw process name into a name a person recognises."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._names = dict(APPLICATION_NAMES)
        if overrides:
            self._names.update({key.lower(): value for key, value in overrides.items()})

    def normalise(self, process_name: str) -> str:
        """`Code.exe` -> `Visual Studio Code`.

        An unknown executable falls back to its own name with the extension
        stripped and capitalised - `myapp.exe` becomes `Myapp`. Better a slightly
        awkward label than "unknown" in every report.
        """
        if not process_name:
            return "Unknown"

        known = self._names.get(process_name.lower())
        if known:
            return known

        stem = process_name.rsplit(".", 1)[0]
        return stem[:1].upper() + stem[1:] if stem else process_name


class ActivityClassifier:
    """Assigns a category and a fallback productivity verdict."""

    def __init__(
        self,
        category_overrides: dict[str, ActivityCategory] | None = None,
        classification_overrides: dict[str, Classification] | None = None,
    ) -> None:
        """
        Args:
            category_overrides: application or domain -> category.
            classification_overrides: user or organisation verdicts, which
                outrank the catalogue but not a task rule.
        """
        self._categories = dict(APPLICATION_CATEGORIES)
        self._domains = dict(DOMAIN_CATEGORIES)
        if category_overrides:
            self._categories.update(category_overrides)

        self._overrides = {
            key.lower(): value for key, value in (classification_overrides or {}).items()
        }

    def category_for_application(self, application_name: str) -> ActivityCategory:
        return self._categories.get(application_name, ActivityCategory.OTHER)

    def category_for_domain(self, domain: str) -> ActivityCategory:
        """Category for a domain, checking parent domains too.

        `gist.github.com` should inherit from `github.com` rather than falling
        through to OTHER.
        """
        if not domain:
            return ActivityCategory.OTHER

        domain = domain.lower()
        if domain in self._domains:
            return self._domains[domain]

        parts = domain.split(".")
        for index in range(1, len(parts) - 1):
            parent = ".".join(parts[index:])
            if parent in self._domains:
                return self._domains[parent]

        return ActivityCategory.OTHER

    def classify(
        self,
        application_name: str,
        domain: str | None = None,
    ) -> ClassifiedActivity:
        """Classify an activity without task context.

        The domain wins when present: a browser's own category is
        CONTEXT_DEPENDENT, and the site inside it is the informative part.
        """
        if domain:
            category = self.category_for_domain(domain)
            subject = domain
        else:
            category = self.category_for_application(application_name)
            subject = application_name

        override = self._overrides.get(subject.lower())
        if override is not None:
            return ClassifiedActivity(
                application_name=application_name,
                category=category,
                classification=override,
                rule_source=RuleSource.USER_OVERRIDE,
                domain=domain,
            )

        if category is ActivityCategory.OTHER:
            return ClassifiedActivity(
                application_name=application_name,
                category=category,
                classification=Classification.NEUTRAL,
                rule_source=RuleSource.UNCLASSIFIED,
                domain=domain,
            )

        return ClassifiedActivity(
            application_name=application_name,
            category=category,
            classification=CATEGORY_DEFAULT_CLASSIFICATION.get(
                category, Classification.NEUTRAL
            ),
            rule_source=RuleSource.CATEGORY_DEFAULT,
            domain=domain,
        )
