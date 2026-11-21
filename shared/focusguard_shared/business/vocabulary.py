"""Language the business analytics will not use about a person (spec section 30).

*"System could say: 'Historical task data suggests Employee A has performed more
strongly on frontend/design assignments.' NOT: 'Employee A is bad at backend.'"*

The same reasoning as the student vocabulary (`recommendations/vocabulary.py`):
a wording guideline is not a control, so the guideline is a list and every
generated sentence is checked against it at construction. A manager reading
"weak at backend" next to a colleague's name is making a decision about a
person's job on the strength of an adjective this code chose.

What remains allowed is comparison of **recorded task outcomes**: "performed
more strongly on X than on Y", "scored 86 across 5 tasks". That is a statement
about history, attributed to data, and it says nothing about anyone's worth.
"""

from __future__ import annotations

import re

from focusguard_shared.recommendations.vocabulary import find_clinical_language

#: Single words judged as whole words, so "poor" does not match "spoor".
JUDGEMENT_WORDS: frozenset[str] = frozenset(
    {
        "bad",
        "worse",
        "worst",
        "weak",
        "weakness",
        "weaknesses",
        "poor",
        "poorly",
        "lazy",
        "incompetent",
        "incapable",
        "slacker",
        "slacking",
        "underperformer",
        "underperforming",
        "underperforms",
        "failure",
        "inferior",
        "terrible",
        "useless",
        "unfit",
        "unreliable",
        "careless",
        "mediocre",
    }
)

#: Phrases that turn a measurement into a verdict on the person.
JUDGEMENT_PHRASES: tuple[str, ...] = (
    "is not good at",
    "isn't good at",
    "should not be given",
    "should never",
    "cannot handle",
    "can't handle",
    "not suited",
    "unsuited",
    "lacks skill",
    "lack of skill",
)


class JudgementalLanguageError(AssertionError):
    """A sentence would label an employee rather than describe task history."""


def find_judgemental_language(text: str) -> list[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z']+", lowered))
    found = [word for word in JUDGEMENT_WORDS if word in words]
    found.extend(phrase for phrase in JUDGEMENT_PHRASES if phrase in lowered)
    found.extend(find_clinical_language(text))
    return sorted(set(found))


def assert_comparative_language(*texts: str) -> None:
    """Raise if any generated sentence judges a person. Called on every statement."""
    for text in texts:
        found = find_judgemental_language(text)
        if found:
            raise JudgementalLanguageError(
                "Business analytics must describe task history, not judge a person "
                f"(spec section 30). Found: {', '.join(found)}. Text: {text!r}"
            )
