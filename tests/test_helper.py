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

from focusguard_agent.capture.camera_apps import CameraReadiness, check_camera
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
    cameras: list = []

    class FakeProcess:
        def __init__(self, arguments):
            self.arguments = arguments
            self.done = False

        def poll(self):
            return 0 if self.done else None

    def open_camera(arguments):
        process = FakeProcess(arguments)
        cameras.append(process)
        return process

    def confirm(title: str, message: str) -> bool:
        asked.append((title, message))
        return answers.pop(0) if answers else False

    def camera_dialog(title: str, message: str) -> str:
        """True -> "once", False -> "no", or a literal "always"/"once"/"no"."""
        asked.append((title, message))
        answer = answers.pop(0) if answers else False
        if isinstance(answer, str):
            return answer
        return "once" if answer else "no"

    readiness = {"value": CameraReadiness(ready=True, device="Camo", message="ok")}
    opened: list[tuple] = []

    helper = FocusGuardHelper(
        settings=AgentSettings(),
        confirm=confirm,
        launcher=launched.append,
        client_factory=FakeClient,
        camera_launcher=open_camera,
        camera_dialog=camera_dialog,
        camera_probe=lambda: readiness["value"],
        extension_opener=lambda folder, browser: opened.append((folder, browser)),
    )
    server = HelperServer(helper, port=0)
    assert server.start()
    yield {
        "port": server.port, "answers": answers, "asked": asked,
        "launched": launched, "cameras": cameras, "readiness": readiness,
        "opened": opened, "helper": helper,
    }
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
        ["--name", hostile.strip(), "--task", "Trees", "--task-category", "STUDY", "--preview"]
    ]


def test_distracting_sites_are_passed_but_never_as_flags(world) -> None:
    world["answers"].append(True)
    call(
        world, "POST", "/sessions/start",
        {"name": "Study", "task": "Study", "task_category": "STUDY",
         "distracting": ["YouTube.com", "--seconds", "instagram.com", "youtube.com"]},
    )
    assert world["launched"][0][-4:] == [
        "--distracting", "youtube.com", "instagram.com", "--preview"
    ]


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
    assert world["launched"][0] == [
        "--name", "Build REST API", "--assigned-task", "12", "--preview"
    ]


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


# --- camera check ---------------------------------------------------------------


def test_the_camera_check_asks_then_opens_a_preview_with_calibration(world) -> None:
    world["answers"].append(True)

    status, _, _ = call(world, "POST", "/camera/check", {})

    assert status == 200
    assert "Nothing is recorded or uploaded" in world["asked"][0][1]
    assert world["cameras"][0].arguments == ["--show", "--keep-open", "--wait-on-exit"]
    camera = call(world, "GET", "/status")[1]["camera"]
    assert camera["check_running"] is True and camera["calibrated"] is False


def test_a_session_cannot_start_while_the_camera_check_holds_the_camera(world) -> None:
    world["answers"].append(True)
    call(world, "POST", "/camera/check", {})

    status, body, _ = call(world, "POST", "/sessions/start", {"name": "Study"})
    assert status == 409 and "camera check" in body["error"]
    assert call(world, "POST", "/camera/check", {})[0] == 409, "only one check at a time"

    world["cameras"][0].done = True
    world["answers"].append(True)
    assert call(world, "POST", "/sessions/start", {"name": "Study"})[0] == 200


def test_a_declined_camera_check_opens_nothing(world) -> None:
    world["answers"].append(False)
    assert call(world, "POST", "/camera/check", {})[0] == 403
    assert world["cameras"] == []


def test_the_camera_check_is_refused_from_another_website(world) -> None:
    status, _, _ = call(world, "POST", "/camera/check", {}, origin="https://evil.example")
    assert status == 403 and world["asked"] == []


def test_sessions_open_the_live_analysis_window(world) -> None:
    """The session lasts as long as its analysis window is open."""
    world["answers"].append(True)
    call(world, "POST", "/sessions/start", {"name": "Study"})
    assert world["launched"][0][-1] == "--preview"


def test_a_second_helper_cannot_bind_the_same_port(world) -> None:
    """Windows lets two sockets share a port with SO_REUSEADDR; the Helper must not."""
    second = HelperServer(
        FocusGuardHelper(settings=AgentSettings(), confirm=lambda *_: False), port=world["port"]
    )
    assert second.bind() is False


# --- remembered approval ("Always allow on this computer") -----------------------


def test_always_allow_is_remembered_and_skips_the_dialog_next_time(world) -> None:
    world["answers"].append("always")
    assert call(world, "POST", "/sessions/start", {"name": "First"})[0] == 200
    assert len(world["asked"]) == 1
    assert call(world, "GET", "/status")[1]["camera_always_allowed"] is True

    world["launched"].clear()
    assert call(world, "POST", "/camera/check", {})[0] == 200
    assert len(world["asked"]) == 1, "no second dialog once always-allowed"


def test_start_once_does_not_remember(world) -> None:
    world["answers"].append("once")
    call(world, "POST", "/sessions/start", {"name": "First"})
    assert call(world, "GET", "/status")[1]["camera_always_allowed"] is False


def test_the_portal_can_only_turn_always_allow_off(world) -> None:
    world["answers"].append("always")
    call(world, "POST", "/sessions/start", {"name": "First"})

    # Trying to switch it ON from a page does nothing.
    call(world, "POST", "/prefs", {"ask_every_time": True})
    status, body, _ = call(world, "POST", "/prefs", {"camera_always_allowed": True})
    assert status == 200 and body["camera_always_allowed"] is False


def test_the_portal_cannot_grant_always_allow(world) -> None:
    status, body, _ = call(world, "POST", "/prefs", {"camera_always_allowed": True})
    assert status == 200 and body["camera_always_allowed"] is False


# --- camera readiness -------------------------------------------------------------


def test_a_missing_phone_camera_app_is_refused_with_its_download_link(world) -> None:
    world["readiness"]["value"] = check_camera(
        "Camo", devices=["Camo"], processes=set(), find_app=lambda app: None
    )

    status, body, _ = call(world, "POST", "/sessions/start", {"name": "Study"})

    assert status == 424
    assert "isn't installed" in body["error"]
    assert body["camera"]["download_url"] == "https://reincubate.com/camo/"
    assert world["asked"] == [] and world["launched"] == [], "nothing asked or started"


def test_no_camera_at_all_is_refused_before_asking(world) -> None:
    world["readiness"]["value"] = check_camera("Camo", devices=[])
    status, body, _ = call(world, "POST", "/camera/check", {})
    assert status == 424 and "No camera was found" in body["error"]
    assert world["asked"] == []


def test_a_closed_phone_camera_app_is_opened_and_the_session_continues(world) -> None:
    opened: list[str] = []
    world["readiness"]["value"] = check_camera(
        "Camo", devices=["Camo"], processes=set(),
        find_app=lambda app: "Reincubate.Camo!App",
        opener=lambda app_id: opened.append(app_id) or True,
    )
    world["answers"].append(True)

    status, body, _ = call(world, "POST", "/sessions/start", {"name": "Study"})

    assert status == 200
    assert opened == ["Reincubate.Camo!App"]
    assert body["camera"]["opened_app"] is True
    assert "Opening Camo Studio" in body["camera"]["message"]


def test_a_real_webcam_needs_no_app() -> None:
    readiness = check_camera("Integrated Camera", devices=["Integrated Camera"], processes=set())
    assert readiness.ready and readiness.app is None


def test_a_running_phone_camera_app_is_ready() -> None:
    readiness = check_camera("Camo", devices=["Camo", "DroidCam Video"], processes={"camostudio"})
    assert readiness.ready and readiness.app_running is True and readiness.device == "Camo"


# --- the browser extension ------------------------------------------------------

from datetime import datetime  # noqa: E402

from focusguard_agent.helper.server import (  # noqa: E402
    EXTENSION_ORIGIN,
    due_now,
    next_scheduled,
)
from focusguard_agent.ipc.server import load_or_create_token  # noqa: E402


def _employee() -> None:
    save_credential(DeviceCredential(
        "dev", "secret", "http://127.0.0.1:8000", user_id=7, organization_id=3,
    ))


def test_only_the_extension_can_pair_itself_and_it_gets_the_agent_token(world) -> None:
    for origin in (PORTAL, "https://evil.example.com", None):
        status, _, _ = call(world, "POST", "/extension/hello", {"version": "0.2.0"}, origin=origin)
        assert status == 403

    status, body, _ = call(world, "POST", "/extension/hello",
                           {"version": "0.2.0", "browser": "edge", "enabled": True},
                           origin=EXTENSION_ORIGIN)
    assert status == 200 and body["token"] == load_or_create_token()

    _, state, _ = call(world, "GET", "/status")
    extension = state["extension"]
    assert extension["installed"] and extension["connected"] and extension["enabled"]
    assert extension["browser"] == "edge" and extension["version"] == "0.2.0"


def test_the_extension_popup_may_read_the_status(world) -> None:
    status, body, response = call(world, "GET", "/status", origin=EXTENSION_ORIGIN)
    assert status == 200 and body["extension"]["installed"] is False
    assert response.getheader("Access-Control-Allow-Origin") == EXTENSION_ORIGIN


def test_a_paused_extension_shows_as_paused(world) -> None:
    call(world, "POST", "/extension/hello", {"enabled": False}, origin=EXTENSION_ORIGIN)
    _, state, _ = call(world, "GET", "/status")
    assert state["extension"]["installed"] and state["extension"]["enabled"] is False


def test_employees_cannot_start_without_the_extension(world) -> None:
    _employee()
    FakeClient.tasks = [{"id": 12, "title": "Build REST API"}]
    status, body, _ = call(world, "POST", "/sessions/start", {"assigned_task_id": 12})
    assert status == 412 and body["extension_required"] is True
    assert world["asked"] == [] and world["launched"] == []

    call(world, "POST", "/extension/hello", {"version": "0.2.0"}, origin=EXTENSION_ORIGIN)
    world["answers"].append(True)
    status, _, _ = call(world, "POST", "/sessions/start", {"assigned_task_id": 12})
    assert status == 200 and world["launched"]


def test_extension_setup_opens_the_folder_for_the_portal_only(world) -> None:
    status, _, _ = call(world, "POST", "/extension/setup", {"browser": "chrome"},
                        origin="https://evil.example.com")
    assert status == 403 and world["opened"] == []

    status, body, _ = call(world, "POST", "/extension/setup", {"browser": "edge"})
    assert status == 200 and body["browser"] == "edge"
    folder, browser = world["opened"][0]
    assert (folder / "manifest.json").is_file() and browser == "edge"


# --- scheduled work time -------------------------------------------------------

MONDAY_9_30 = datetime(2026, 11, 30, 9, 30)  # a Monday
SCHEDULED = {"id": 12, "title": "Build REST API", "schedule_days": [0, 1, 2, 3, 4],
             "schedule_time": "09:00", "schedule_minutes": 120}


def test_a_window_is_due_only_inside_it_on_its_days() -> None:
    assert due_now(SCHEDULED, MONDAY_9_30) is not None
    assert due_now(SCHEDULED, MONDAY_9_30.replace(hour=8)) is None
    assert due_now(SCHEDULED, MONDAY_9_30.replace(hour=10, minute=59, second=30)) is None
    assert due_now(SCHEDULED, datetime(2026, 12, 5, 9, 30)) is None  # Saturday
    assert due_now({"id": 1, "title": "x"}, MONDAY_9_30) is None


def test_the_next_scheduled_session_rolls_over_the_weekend() -> None:
    friday_noon = datetime(2026, 12, 4, 12, 0)
    upcoming = next_scheduled([SCHEDULED], friday_noon)
    assert upcoming["starts_at"] == "2026-12-07T09:00" and upcoming["now"] is False
    assert next_scheduled([SCHEDULED], MONDAY_9_30)["now"] is True


def test_a_scheduled_task_starts_by_itself_for_the_rest_of_its_window(world) -> None:
    _employee()
    call(world, "POST", "/extension/hello", {"version": "0.2.0"}, origin=EXTENSION_ORIGIN)
    FakeClient.tasks = [SCHEDULED]
    world["answers"].append(True)

    note = world["helper"].scheduled_tick(MONDAY_9_30)
    assert note["ok"] is True and note["minutes"] == 90
    assert world["launched"][0] == [
        "--name", "Build REST API", "--assigned-task", "12",
        "--seconds", "5400", "--preview",
    ]
    assert "scheduled work time" in world["asked"][0][1]

    # Closed early (or still running): never started again in the same window.
    assert world["helper"].scheduled_tick(MONDAY_9_30.replace(minute=45)) is None
    assert len(world["launched"]) == 1


def test_a_declined_scheduled_start_is_not_asked_again(world) -> None:
    _employee()
    call(world, "POST", "/extension/hello", {}, origin=EXTENSION_ORIGIN)
    FakeClient.tasks = [SCHEDULED]
    note = world["helper"].scheduled_tick(MONDAY_9_30)
    assert note["ok"] is False and world["launched"] == []
    assert world["helper"].scheduled_tick(MONDAY_9_30.replace(minute=31)) is None
    assert len(world["asked"]) == 1
    _, state, _ = call(world, "GET", "/status")
    assert state["schedule"]["last"]["ok"] is False


def test_without_the_extension_the_scheduled_start_says_why(world) -> None:
    _employee()
    FakeClient.tasks = [SCHEDULED]
    note = world["helper"].scheduled_tick(MONDAY_9_30)
    assert note["ok"] is False and "browser extension" in note["error"]
    assert world["asked"] == []


def test_students_have_no_schedule(world) -> None:
    save_credential(DeviceCredential("dev", "secret", "http://127.0.0.1:8000", user_id=7))
    FakeClient.tasks = [SCHEDULED]
    assert world["helper"].scheduled_tick(MONDAY_9_30) is None
