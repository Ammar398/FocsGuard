"""Activity monitoring tests (spec sections 19, 21, 22, 23, 58).

All pure logic - no Windows APIs, no camera. The behaviour that matters most is
what happens to things the catalogue does not recognise: guessing they are
distractions would penalise users for niche tools.
"""

from __future__ import annotations

import pytest

from focusguard_agent.activity_monitor.aggregator import ActivityAggregator
from focusguard_agent.activity_monitor.classifier import (
    ActivityClassifier,
    ActivityNormalizer,
)
from focusguard_agent.activity_monitor.collector import ForegroundWindow
from focusguard_shared.constants import ActivityCategory, Classification, RuleSource

DEV = ActivityCategory.DEVELOPMENT
PRODUCTIVE = Classification.PRODUCTIVE
NEUTRAL = Classification.NEUTRAL
DISTRACTING = Classification.DISTRACTING


# --- normalisation (spec section 19) ---------------------------------------


@pytest.mark.parametrize(
    ("process", "expected"),
    [
        ("Code.exe", "Visual Studio Code"),
        ("code.exe", "Visual Studio Code"),
        ("CHROME.EXE", "Google Chrome"),
        ("msedge.exe", "Microsoft Edge"),
        ("figma.exe", "Figma"),
        ("slack.exe", "Slack"),
    ],
)
def test_known_executables_get_readable_names(process: str, expected: str) -> None:
    """"Code.exe" is meaningless in a report."""
    assert ActivityNormalizer().normalise(process) == expected


def test_an_unknown_executable_falls_back_to_a_tidy_name() -> None:
    """Better a slightly awkward label than "Unknown" filling every report."""
    assert ActivityNormalizer().normalise("myapp.exe") == "Myapp"


def test_an_empty_process_name_is_handled() -> None:
    assert ActivityNormalizer().normalise("") == "Unknown"


def test_normalisation_can_be_overridden() -> None:
    normalizer = ActivityNormalizer(overrides={"myapp.exe": "My Custom App"})
    assert normalizer.normalise("myapp.exe") == "My Custom App"


# --- categorisation ---------------------------------------------------------


def test_applications_are_categorised() -> None:
    classifier = ActivityClassifier()

    assert classifier.category_for_application("Visual Studio Code") is DEV
    assert classifier.category_for_application("Figma") is ActivityCategory.DESIGN
    assert classifier.category_for_application("Slack") is ActivityCategory.COMMUNICATION


def test_a_browser_alone_is_context_dependent() -> None:
    """A browser's category depends entirely on the site open inside it."""
    classifier = ActivityClassifier()
    assert (
        classifier.category_for_application("Google Chrome")
        is ActivityCategory.CONTEXT_DEPENDENT
    )


def test_domains_are_categorised() -> None:
    classifier = ActivityClassifier()

    assert classifier.category_for_domain("github.com") is DEV
    assert classifier.category_for_domain("instagram.com") is ActivityCategory.SOCIAL_MEDIA
    assert classifier.category_for_domain("netflix.com") is ActivityCategory.ENTERTAINMENT


def test_subdomains_inherit_from_their_parent() -> None:
    """gist.github.com must not fall through to OTHER."""
    assert ActivityClassifier().category_for_domain("gist.github.com") is DEV


def test_youtube_is_context_dependent_not_entertainment() -> None:
    """Spec section 22 is explicit: YouTube must NOT be assumed unproductive.

    It hosts lectures and entertainment alike; only the task can decide.
    """
    classifier = ActivityClassifier()

    assert (
        classifier.category_for_domain("youtube.com")
        is ActivityCategory.CONTEXT_DEPENDENT
    )
    assert classifier.classify("Google Chrome", domain="youtube.com").classification is NEUTRAL


# --- classification ---------------------------------------------------------


def test_development_work_is_productive_by_default() -> None:
    result = ActivityClassifier().classify("Visual Studio Code")

    assert result.classification is PRODUCTIVE
    assert result.rule_source is RuleSource.CATEGORY_DEFAULT


def test_communication_is_neutral_not_distracting() -> None:
    """Slack and email are real work (see scoring/productivity.py)."""
    assert ActivityClassifier().classify("Slack").classification is NEUTRAL


def test_entertainment_is_distracting_by_default() -> None:
    assert ActivityClassifier().classify("Netflix").classification is DISTRACTING


def test_an_unknown_application_is_neutral_never_distracting() -> None:
    """Guessing that unfamiliar software is a distraction would penalise users
    for niche tools - a design plugin, a university exam client."""
    result = ActivityClassifier().classify("Some Internal Tool")

    assert result.classification is NEUTRAL
    assert result.rule_source is RuleSource.UNCLASSIFIED
    assert result.category is ActivityCategory.OTHER


def test_the_domain_wins_over_the_browser() -> None:
    """The browser is CONTEXT_DEPENDENT; the site inside is informative."""
    result = ActivityClassifier().classify("Google Chrome", domain="github.com")

    assert result.category is DEV
    assert result.classification is PRODUCTIVE
    assert result.domain == "github.com"


def test_a_user_override_outranks_the_catalogue() -> None:
    classifier = ActivityClassifier(
        classification_overrides={"Spotify": Classification.NEUTRAL}
    )
    result = classifier.classify("Spotify")

    assert result.classification is NEUTRAL
    assert result.rule_source is RuleSource.USER_OVERRIDE


def test_every_verdict_can_explain_itself() -> None:
    """Section 41: the user must be able to see WHY something was judged."""
    result = ActivityClassifier().classify("Netflix")

    explanation = result.explain()
    assert "Netflix" in explanation
    assert "ENTERTAINMENT" in explanation


def test_an_unclassified_verdict_says_so_plainly() -> None:
    explanation = ActivityClassifier().classify("Weird Tool").explain()
    assert "not categorised" in explanation


# --- aggregation (spec sections 19, 21) ------------------------------------


def _activity(name: str, classification: Classification = PRODUCTIVE) -> object:
    return ActivityClassifier().classify(name) if classification is None else (
        __import__(
            "focusguard_agent.activity_monitor.classifier", fromlist=["ClassifiedActivity"]
        ).ClassifiedActivity(
            application_name=name,
            category=DEV,
            classification=classification,
            rule_source=RuleSource.CATEGORY_DEFAULT,
        )
    )


def test_consecutive_samples_of_one_app_become_a_single_span() -> None:
    """28,800 samples a day would be almost all duplicates (spec section 45)."""
    aggregator = ActivityAggregator(min_span_seconds=3.0)
    code = _activity("Visual Studio Code")

    for second in range(0, 60, 1):
        assert aggregator.update(float(second), code) is None, "no span until it changes"

    span = aggregator.flush(60.0)
    assert span is not None
    assert span.duration_seconds == pytest.approx(60.0)


def test_switching_applications_closes_the_previous_span() -> None:
    aggregator = ActivityAggregator(min_span_seconds=3.0)

    aggregator.update(0.0, _activity("Visual Studio Code"))
    span = aggregator.update(30.0, _activity("Figma"))

    assert span is not None
    assert span.application_name == "Visual Studio Code"
    assert span.duration_seconds == pytest.approx(30.0)


def test_alt_tabbing_produces_no_spans() -> None:
    """Six windows in two seconds is not usage of any of them."""
    aggregator = ActivityAggregator(min_span_seconds=3.0)

    for index, name in enumerate(["App A", "App B", "App C", "App D"]):
        aggregator.update(index * 0.5, _activity(name))

    assert aggregator.totals.total_seconds == 0.0
    assert aggregator.totals.discarded_spans >= 3


def test_idle_time_is_not_counted_as_usage() -> None:
    """Walking away with VS Code open is not three hours of development."""
    aggregator = ActivityAggregator(min_span_seconds=3.0)

    aggregator.update(0.0, _activity("Visual Studio Code"))
    span = aggregator.update(30.0, _activity("Visual Studio Code"), idle=True)

    assert span is not None
    assert span.duration_seconds == pytest.approx(30.0)
    assert aggregator.current is None, "nothing is attributed while idle"


def test_idle_seconds_are_tracked_separately() -> None:
    aggregator = ActivityAggregator()
    aggregator.update(0.0, _activity("Visual Studio Code"))
    aggregator.update(10.0, None, idle=True)
    aggregator.update(70.0, None, idle=True)

    assert aggregator.totals.idle_seconds == pytest.approx(60.0, abs=1.0)


def test_totals_are_accumulated_per_application() -> None:
    aggregator = ActivityAggregator(min_span_seconds=1.0)

    aggregator.update(0.0, _activity("Visual Studio Code"))
    aggregator.update(100.0, _activity("Figma"))
    aggregator.update(150.0, _activity("Visual Studio Code"))
    aggregator.flush(200.0)

    totals = aggregator.totals
    assert totals.by_application["Visual Studio Code"] == pytest.approx(150.0)
    assert totals.by_application["Figma"] == pytest.approx(50.0)


def test_most_used_application_is_reported() -> None:
    aggregator = ActivityAggregator(min_span_seconds=1.0)

    aggregator.update(0.0, _activity("Figma"))
    aggregator.update(20.0, _activity("Visual Studio Code"))
    aggregator.flush(200.0)

    most_used = aggregator.totals.most_used()
    assert most_used[0][0] == "Visual Studio Code"


def test_time_is_totalled_by_classification() -> None:
    """Feeds the productive-vs-distracting split on the dashboard."""
    aggregator = ActivityAggregator(min_span_seconds=1.0)

    aggregator.update(0.0, _activity("Work App", PRODUCTIVE))
    aggregator.update(60.0, _activity("Fun App", DISTRACTING))
    aggregator.flush(90.0)

    totals = aggregator.totals
    assert totals.seconds_for(PRODUCTIVE) == pytest.approx(60.0)
    assert totals.seconds_for(DISTRACTING) == pytest.approx(30.0)


def test_the_current_application_is_reported_live() -> None:
    aggregator = ActivityAggregator()
    aggregator.update(0.0, _activity("Visual Studio Code"))

    assert aggregator.current is not None
    assert aggregator.current.application_name == "Visual Studio Code"
    assert aggregator.current_duration(45.0) == pytest.approx(45.0)


# --- collector helpers ------------------------------------------------------


def test_transient_shell_windows_are_recognised() -> None:
    """These hold the foreground briefly during a window switch."""
    assert ForegroundWindow(process_name="SearchHost.exe").is_transient is True
    assert ForegroundWindow(process_name="Code.exe").is_transient is False
