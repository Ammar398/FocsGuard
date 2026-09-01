"""Browser monitoring, IPC and task-context tests (spec sections 20, 22, 51).

The IPC tests start a real server on a real loopback port and speak HTTP to it,
because the things worth proving here - that a wrong token is rejected, that a
full URL is refused - are exactly the things a mock would happily fake.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

import pytest

from focusguard_agent.activity_monitor.task_context import (
    TaskContext,
    TaskContextEngine,
)
from focusguard_agent.browser_monitor.monitor import BrowserMonitor
from focusguard_agent.ipc.server import BrowserActivity, IpcServer
from focusguard_shared.constants import Classification, RuleSource, TaskCategory

PRODUCTIVE = Classification.PRODUCTIVE
NEUTRAL = Classification.NEUTRAL
DISTRACTING = Classification.DISTRACTING


# --- task context (spec section 22) ----------------------------------------


def test_without_a_task_the_base_classification_stands() -> None:
    engine = TaskContextEngine()
    assert engine.classify("Visual Studio Code").classification is PRODUCTIVE


def test_youtube_is_productive_when_the_task_says_so() -> None:
    """The canonical section 22 case: "Watch Python Tutorial"."""
    engine = TaskContextEngine(
        task=TaskContext(
            name="Watch Python Lecture",
            category=TaskCategory.STUDY,
            productive={"youtube.com"},
        )
    )

    result = engine.classify("Google Chrome", domain="youtube.com")
    assert result.classification is PRODUCTIVE
    assert result.rule_source is RuleSource.TASK_RULE


def test_the_same_site_can_be_distracting_for_a_different_task() -> None:
    engine = TaskContextEngine(
        task=TaskContext(
            name="Implement Authentication",
            category=TaskCategory.BACKEND,
            distracting={"youtube.com"},
        )
    )

    assert engine.classify("Google Chrome", domain="youtube.com").classification is DISTRACTING


def test_an_unlisted_context_dependent_site_stays_neutral() -> None:
    """FocusGuard does not guess whether a YouTube page is a lecture.

    The domain cannot tell us and the title is not collected, so guessing would
    produce exactly the confident-but-wrong verdict section 61 forbids.
    """
    engine = TaskContextEngine(
        task=TaskContext(name="Implement Authentication", category=TaskCategory.BACKEND)
    )

    assert engine.classify("Google Chrome", domain="youtube.com").classification is NEUTRAL


def test_task_category_makes_relevant_tools_productive_without_listing_them() -> None:
    """A manager should not have to enumerate every tool a developer opens."""
    engine = TaskContextEngine(
        task=TaskContext(name="Build the REST API", category=TaskCategory.BACKEND)
    )

    for application in ("Visual Studio Code", "Windows Terminal", "Postman"):
        result = engine.classify(application)
        assert result.classification is PRODUCTIVE, application
        assert result.rule_source is RuleSource.TASK_RULE


def test_design_tools_are_not_productive_for_a_backend_task() -> None:
    """Category affinity must actually discriminate, not bless everything."""
    engine = TaskContextEngine(
        task=TaskContext(name="Build the REST API", category=TaskCategory.BACKEND)
    )
    result = engine.classify("Figma")

    assert result.rule_source is not RuleSource.TASK_RULE


def test_design_tools_are_productive_for_a_design_task() -> None:
    engine = TaskContextEngine(
        task=TaskContext(name="Create Landing Page UI", category=TaskCategory.DESIGN)
    )
    assert engine.classify("Figma").classification is PRODUCTIVE


def test_an_explicit_rule_beats_category_affinity() -> None:
    engine = TaskContextEngine(
        task=TaskContext(
            name="Backend work",
            category=TaskCategory.BACKEND,
            distracting={"Visual Studio Code"},
        )
    )
    assert engine.classify("Visual Studio Code").classification is DISTRACTING


def test_distracting_wins_when_a_task_lists_a_site_twice() -> None:
    """The restrictive reading is safer, and likelier to be the deliberate entry."""
    task = TaskContext(
        name="Study", productive={"youtube.com"}, distracting={"youtube.com"}
    )
    assert task.rule_for("youtube.com") is DISTRACTING


def test_task_rules_are_case_insensitive() -> None:
    engine = TaskContextEngine(
        task=TaskContext(name="Study", productive={"YouTube.com"})
    )
    assert engine.classify("Google Chrome", domain="youtube.com").classification is PRODUCTIVE


def test_a_task_verdict_explains_itself() -> None:
    engine = TaskContextEngine(
        task=TaskContext(name="Build the REST API", category=TaskCategory.BACKEND)
    )
    explanation = engine.explain(engine.classify("Visual Studio Code"))

    assert "BACKEND" in explanation


def test_clearing_the_task_restores_base_classification() -> None:
    engine = TaskContextEngine(
        task=TaskContext(name="Study", productive={"netflix.com"})
    )
    assert engine.classify("Google Chrome", domain="netflix.com").classification is PRODUCTIVE

    engine.set_task(None)
    assert engine.classify("Google Chrome", domain="netflix.com").classification is DISTRACTING


# --- browser monitor --------------------------------------------------------


def _report(domain: str, timestamp: float, active: bool = True) -> BrowserActivity:
    return BrowserActivity(
        domain=domain, path_prefix=None, timestamp=timestamp, active=active
    )


def test_a_browser_is_classified_by_the_site_inside_it() -> None:
    monitor = BrowserMonitor()
    monitor.ingest(_report("github.com", timestamp=100.0))

    result = monitor.classify("Google Chrome", now=101.0)
    assert result.domain == "github.com"
    assert result.classification is PRODUCTIVE


def test_a_stale_report_is_ignored() -> None:
    """Closing the browser leaves no goodbye message, only silence.

    Without expiry the last domain would appear open for the rest of the day.
    """
    monitor = BrowserMonitor(stale_after_seconds=30.0)
    monitor.ingest(_report("netflix.com", timestamp=100.0))

    result = monitor.classify("Google Chrome", now=200.0)
    assert result.domain is None
    assert monitor.current_domain(now=200.0) is None


def test_a_domain_is_not_applied_to_a_non_browser_application() -> None:
    """A YouTube tab behind Photoshop is not what the user is doing."""
    monitor = BrowserMonitor()
    monitor.ingest(_report("netflix.com", timestamp=100.0))

    result = monitor.classify("Adobe Photoshop", now=101.0)
    assert result.domain is None
    assert result.classification is PRODUCTIVE, "Photoshop is DESIGN, not Netflix"


def test_an_inactive_report_is_treated_as_stale() -> None:
    monitor = BrowserMonitor()
    monitor.ingest(_report("github.com", timestamp=100.0, active=False))

    assert monitor.current_domain(now=101.0) is None


def test_the_agent_works_with_no_extension_at_all() -> None:
    """Without the extension the browser is still tracked as an application.

    The system loses resolution, not function.
    """
    monitor = BrowserMonitor()
    result = monitor.classify("Google Chrome", now=100.0)

    assert result.application_name == "Google Chrome"
    assert result.domain is None
    assert monitor.extension_connected is False


def test_browsers_are_recognised() -> None:
    assert BrowserMonitor.is_browser("Google Chrome") is True
    assert BrowserMonitor.is_browser("Microsoft Edge") is True
    assert BrowserMonitor.is_browser("Visual Studio Code") is False


# --- the IPC server, over real HTTP (spec section 51) ----------------------


@pytest.fixture
def server():
    """A real server on a real port, on an ephemeral port to avoid collisions."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    instance = IpcServer(port=port, token="test-token-1234567890")
    assert instance.start(), "server failed to bind"
    time.sleep(0.05)
    yield instance
    instance.stop()


def _post(server: IpcServer, payload: dict, token: str | None = None) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{server.port}/activity",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            **({"X-FocusGuard-Token": token} if token else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_a_valid_report_is_accepted(server: IpcServer) -> None:
    status = _post(server, {"domain": "github.com", "path_prefix": "/user"}, server.token)

    assert status == 200
    latest = server.latest()
    assert latest is not None
    assert latest.domain == "github.com"


def test_a_report_without_a_token_is_rejected(server: IpcServer) -> None:
    """Otherwise any web page could POST fabricated activity."""
    assert _post(server, {"domain": "github.com"}) == 401
    assert server.latest() is None


def test_a_wrong_token_is_rejected(server: IpcServer) -> None:
    assert _post(server, {"domain": "github.com"}, "wrong-token") == 401
    assert server.latest() is None


def test_a_full_url_is_refused(server: IpcServer) -> None:
    """The backstop: if the extension regresses, the agent must refuse the data
    rather than quietly storing a query string."""
    status = _post(
        server,
        {"domain": "https://google.com/search?q=private+medical+question"},
        server.token,
    )

    assert status == 400
    assert server.latest() is None


def test_a_path_prefix_with_a_query_string_is_refused(server: IpcServer) -> None:
    status = _post(
        server, {"domain": "google.com", "path_prefix": "/search?q=secret"}, server.token
    )
    assert status == 400


def test_a_malformed_domain_is_refused(server: IpcServer) -> None:
    assert _post(server, {"domain": "localhost"}, server.token) == 400
    assert _post(server, {"domain": ""}, server.token) == 400


def test_the_status_endpoint_reveals_nothing_about_the_user(server: IpcServer) -> None:
    """It exists so the extension can show whether the agent is running."""
    with urllib.request.urlopen(
        f"http://127.0.0.1:{server.port}/status", timeout=3
    ) as response:
        payload = json.loads(response.read())

    assert payload["service"] == "focusguard"
    assert "domain" not in payload
    assert "token" not in payload


def test_unknown_paths_are_not_routed(server: IpcServer) -> None:
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}/../secrets")
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code

    assert status == 404


def test_draining_returns_and_clears_reports(server: IpcServer) -> None:
    _post(server, {"domain": "github.com"}, server.token)
    _post(server, {"domain": "stackoverflow.com"}, server.token)

    drained = server.drain()
    assert len(drained) == 2
    assert server.drain() == []


def test_binding_to_a_non_loopback_address_is_refused() -> None:
    """A misconfigured host would expose a user's browsing to the network."""
    with pytest.raises(ValueError, match="loopback"):
        IpcServer(host="0.0.0.0")
