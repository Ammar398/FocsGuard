"""The FocusGuard Helper (one-click connect and start from the portal).

What would matter most if it broke:

* a web page that is not the portal cannot drive it, and a DNS-rebinding
  hostname cannot reach it;
* nothing enrols the machine or turns the camera on unless the person at the
  computer says yes;
* a declined dialog changes nothing;
* the agent is launched with an argument list, so a hostile session name is
  inert text.
"""

from __future__ import annotations

import http.client
import json

import pytest

from focusguard_agent.config import AgentSettings
from focusguard_agent.helper import FocusGuardHelper, HelperServer
from focusguard_agent.synchronization.credentials import (
    DeviceCredential,
    NotEnrolled,
    load_credential,
    save_credential,
)
from focusguard_agent.tools import stop as stop_tool

PORTAL = "http://localhost:5173"


class FakeClient:
    """Stands in for BackendClient; records what it was asked."""

    calls: list[tuple[str, dict]] = []
    tasks: list[dict] = []

    def __init__(self, base_url: str, credential: DeviceCredential | None = None) -> None:
        self.base_url = base_url

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def enroll(self, **kwargs) -> DeviceCredential:
        FakeClient.calls.append(("enroll", kwargs))
        return DeviceCredential(
            device_uuid=kwargs["device_uuid"],
            device_secret="secret",
            backend_url=self.base_url,
            user_id=7,
        )

    def assigned_tasks(self) -> list[dict]:
        return FakeClient.tasks


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_DATA_DIR", str(tmp_path))
    FakeClient.calls = []
    FakeClient.tasks = []
    answers: list[bool] = []
    asked: list[tuple[str, str]] = []
    launched: list[list[str]] = []

    def confirm(title: str, message: str) -> bool:
        asked.append((title, message))
        return answers.pop(0) if answers else False

    helper = FocusGuardHelper(
        settings=AgentSettings(),
        confirm=confirm,
        launcher=launched.append,
        client_factory=FakeClient,
    )
    server = HelperServer(helper, port=0)
    assert server.start()
    yield {"port": server.port, "answers": answers, "asked": asked, "launched": launched}
    server.stop()


def call(world, method, path, body=None, origin=PORTAL, host=None):
    connection = http.client.HTTPConnection("127.0.0.1", world["port"], timeout=5)
    headers = {"Host": host or f"127.0.0.1:{world['port']}"}
    if origin:
        headers["Origin"] = origin
    data = None
    if body is not None:
        data = json.dumps(body)
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, (json.loads(payload) if payload else None), response


# --- who may call it ----------------------------------------------------------


def test_status_answers_the_portal_with_cors(world) -> None:
    status, body, response = call(world, "GET", "/status")
    assert status == 200
    assert body["enrolled"] is False and body["session"]["running"] is False
    assert response.getheader("Access-Control-Allow-Origin") == PORTAL


def test_another_website_cannot_drive_the_helper(world) -> None:
    for path in ("/pair", "/sessions/start", "/sessions/stop"):
        status, _, response = call(world, "POST", path, {}, origin="https://evil.example")
        assert status == 403, path
        assert response.getheader("Access-Control-Allow-Origin") is None
    assert world["asked"] == [], "no dialog may even be shown for a foreign page"


def test_a_post_with_no_origin_is_refused(world) -> None:
    status, _, _ = call(world, "POST", "/sessions/start", {"name": "x"}, origin=None)
    assert status == 403


def test_a_rebound_hostname_is_refused(world) -> None:
    """DNS rebinding: evil.example resolved to 127.0.0.1 still sends its own Host."""
    status, _, _ = call(world, "GET", "/status", host=f"evil.example:{world['port']}")
    assert status == 421


def test_preflight_allows_private_network_access_for_the_portal_only(world) -> None:
    status, _, response = call(world, "OPTIONS", "/pair")
    assert status == 204
    assert response.getheader("Access-Control-Allow-Private-Network") == "true"
    status, _, _ = call(world, "OPTIONS", "/pair", origin="https://evil.example")
    assert status == 403


# --- pairing ------------------------------------------------------------------


def test_pairing_asks_on_the_computer_then_connects(world) -> None:
    world["answers"].append(True)

    status, body, _ = call(world, "POST", "/pair", {"enrollment_token": "tok-123"})

    assert status == 200 and body["user_id"] == 7
    title, message = world["asked"][0]
    assert "connect this computer" in title.lower()
    assert "will NOT upload" in message, "the full consent notice is shown"
    enroll = FakeClient.calls[0][1]
    assert enroll["enrollment_token"] == "tok-123"
    assert enroll["consent_version"]
    assert load_credential().user_id == 7
    assert call(world, "GET", "/status")[1]["enrolled"] is True


def test_declining_the_dialog_changes_nothing(world) -> None:
    world["answers"].append(False)

    status, _, _ = call(world, "POST", "/pair", {"enrollment_token": "tok-123"})

    assert status == 403
    assert FakeClient.calls == [], "the token was never even sent"
    with pytest.raises(NotEnrolled):
        load_credential()


# --- sessions -------------------------------------------------------------------


def test_starting_a_session_asks_and_launches_with_an_argument_list(world) -> None:
    world["answers"].append(True)
    hostile = 'Study"; del C:\\* & echo '

    status, _, _ = call(
        world, "POST", "/sessions/start",
        {"name": hostile, "task": "Trees", "task_category": "study"},
    )

    assert status == 200
    assert "webcam" in world["asked"][0][1]
    assert world["launched"] == [
        ["--name", hostile.strip(), "--task", "Trees", "--task-category", "STUDY"]
    ]


def test_distracting_sites_are_passed_but_never_as_flags(world) -> None:
    world["answers"].append(True)
    call(
        world, "POST", "/sessions/start",
        {"name": "Study", "task": "Study", "task_category": "STUDY",
         "distracting": ["YouTube.com", "--seconds", "instagram.com", "youtube.com"]},
    )
    assert world["launched"][0][-3:] == ["--distracting", "youtube.com", "instagram.com"]


def test_a_declined_start_launches_nothing(world) -> None:
    world["answers"].append(False)
    status, _, _ = call(world, "POST", "/sessions/start", {"name": "Study"})
    assert status == 403
    assert world["launched"] == []


def test_an_unknown_category_is_refused_before_asking(world) -> None:
    status, _, _ = call(
        world, "POST", "/sessions/start", {"name": "x", "task": "y", "task_category": "ALCHEMY"}
    )
    assert status == 400 and world["asked"] == []


def test_an_assigned_task_must_be_one_of_mine(world) -> None:
    save_credential(DeviceCredential("dev", "secret", "http://127.0.0.1:8000", user_id=7))
    FakeClient.tasks = [{"id": 12, "title": "Build REST API"}]

    status, _, _ = call(world, "POST", "/sessions/start", {"assigned_task_id": 99})
    assert status == 404 and world["asked"] == []

    world["answers"].append(True)
    status, body, _ = call(world, "POST", "/sessions/start", {"assigned_task_id": 12})
    assert status == 200 and body["name"] == "Build REST API"
    assert world["launched"][0] == ["--name", "Build REST API", "--assigned-task", "12"]


def test_only_one_session_at_a_time_and_stop_asks_the_agent(world, monkeypatch) -> None:
    monkeypatch.setattr(
        "focusguard_agent.helper.server.read_pid_file", lambda: (1234, "Study")
    )
    monkeypatch.setattr("focusguard_agent.helper.server.is_running", lambda pid: True)

    status, _, _ = call(world, "POST", "/sessions/start", {"name": "Another"})
    assert status == 409 and world["asked"] == []

    assert call(world, "GET", "/status")[1]["session"] == {"running": True, "name": "Study"}
    status, _, _ = call(world, "POST", "/sessions/stop", {})
    assert status == 200
    assert stop_tool.stop_requested()


def test_stop_with_nothing_running_is_a_409(world) -> None:
    assert call(world, "POST", "/sessions/stop", {})[0] == 409
