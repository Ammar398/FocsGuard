"""The language this project will not use (spec section 8).

*"The project should NOT diagnose ADHD or any medical condition."*

WHY THIS IS CODE AND NOT A STYLE GUIDE
--------------------------------------
A note in a document saying "be careful with wording" is not a control. Someone
adds a rule six months from now, writes what feels like a helpful sentence, and
the system is suddenly telling a student they may have a disorder. The harm there
is real and specific: a person changes how they see themselves on the say-so of a
webcam and a threshold.

So the vocabulary is a list, `assert_no_clinical_language` is called on every
recommendation as it is built, and `tests/test_recommendations.py` runs every
rule across the whole input space to prove none of it can reach a user.

WHAT IS FORBIDDEN, AND WHY EACH GROUP
-------------------------------------
* **Conditions** - naming one is a diagnosis even hedged with "may".
* **Clinical framings** - "symptom", "disorder", "impairment" import a medical
  frame around ordinary behaviour.
* **Deficit language about the person** - "you are easily distracted" is a claim
  about character, not a measurement. The system measures sessions.
* **Treatment language** - "therapy", "medication", "treatment" put the tool in a
  role it has no business in.

WHAT IS STILL ALLOWED
---------------------
Describing what was observed, and what the data says about a *session*. "You
looked away eleven times" is a count. "Your score was lower after 9pm" is a
comparison. Neither names a condition or a trait.

The boundary is: **observations and session statistics, never claims about the
person or their health.**
"""

from __future__ import annotations

import re

#: Named conditions. Never appropriate, in any phrasing.
CONDITION_TERMS: frozenset[str] = frozenset(
    {
        "adhd",
        "add",
        "attention deficit",
        "hyperactivity",
        "autism",
        "autistic",
        "asperger",
        "dyslexia",
        "dyslexic",
        "narcolepsy",
        "insomnia",
        "sleep apnea",
        "sleep apnoea",
        "depression",
        "depressive",
        "anxiety disorder",
        "burnout syndrome",
        "ocd",
        "bipolar",
        "dementia",
    }
)

#: Clinical framing. Turns behaviour into pathology.
CLINICAL_TERMS: frozenset[str] = frozenset(
    {
        "diagnose",
        "diagnosis",
        "diagnostic",
        "diagnosed",
        "symptom",
        "symptoms",
        "symptomatic",
        "disorder",
        "syndrome",
        "pathology",
        "pathological",
        "clinical",
        "impairment",
        "impaired",
        "dysfunction",
        "deficit",
        "comorbid",
        "prognosis",
        "patient",
    }
)

#: Treatment language. Not this tool's role.
TREATMENT_TERMS: frozenset[str] = frozenset(
    {
        "treatment",
        "therapy",
        "therapist",
        "medication",
        "medicate",
        "prescribe",
        "prescription",
        "dose",
        "dosage",
        "psychiatrist",
        "psychiatric",
        "cure",
        "remedy",
    }
)

#: Claims about the person rather than the measurement. These are phrases rather
#: than single words, because "you are" is fine and "you are disordered" is not.
TRAIT_CLAIMS: tuple[str, ...] = (
    "you are easily distracted",
    "you have a short attention span",
    "you are unable to",
    "you cannot focus",
    "you can't focus",
    "you lack",
    "you suffer",
    "you are suffering",
    "your condition",
    "you may have",
    "you might have",
    "you probably have",
    "suggests you have",
    "indicates you have",
)

FORBIDDEN_TERMS: frozenset[str] = CONDITION_TERMS | CLINICAL_TERMS | TREATMENT_TERMS


class ClinicalLanguageError(AssertionError):
    """Text that would diagnose, pathologise, or label a person was produced.

    Deliberately an error rather than a filter that quietly strips the words. A
    rule that tried to say something clinical is a rule whose whole premise needs
    rethinking, and silently editing its output would hide that.
    """


def _tokens(text: str) -> set[str]:
    """Words, lowercased. Used for whole-word matching."""
    return set(re.findall(r"[a-z']+", text.lower()))


def find_clinical_language(text: str) -> list[str]:
    """Every forbidden term or phrase in `text`. Empty when the text is fine."""
    lowered = text.lower()
    words = _tokens(text)
    found: list[str] = []

    for term in FORBIDDEN_TERMS:
        if " " in term:
            # A multi-word term is a substring check: "attention deficit".
            if term in lowered:
                found.append(term)
        elif term in words:
            # Whole word only. Otherwise "add" matches "address" and "adding",
            # and "cure" matches "accurate" - false alarms that would train
            # everyone to ignore this check.
            found.append(term)

    found.extend(phrase for phrase in TRAIT_CLAIMS if phrase in lowered)
    return sorted(set(found))


def contains_clinical_language(text: str) -> bool:
    return bool(find_clinical_language(text))


def assert_no_clinical_language(*texts: str) -> None:
    """Raise if any text would diagnose or label. Called on every recommendation.

    Checked at construction rather than at display, so a bad rule fails in the
    test suite rather than in front of a student.
    """
    for text in texts:
        found = find_clinical_language(text)
        if found:
            raise ClinicalLanguageError(
                f"Recommendation text must not diagnose or label a person "
                f"(spec section 8). Found: {', '.join(found)}. Text: {text!r}"
            )
