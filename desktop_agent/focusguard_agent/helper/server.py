"""The FocusGuard Helper: lets the portal connect this computer and start sessions.

    python -m focusguard_agent.helper                     # run it now
    python -m focusguard_agent.helper --install-startup   # and every time you sign in to Windows

WHY THIS EXISTS
---------------
A web page cannot run a program or switch on a webcam - and must not be able to.
Without this helper, connecting a computer meant copying a token into a
terminal, and starting a session meant typing a command. With it, the portal
asks the helper, the helper asks **the person at this computer** in a native
Windows dialog, and on "Yes" it does the work itself.

The helper never opens the camera and never monitors anything. It only:

    GET  /status          is this computer connected, and is a session running?
    GET  /tasks           (employees) the open tasks assigned to this computer's user
    POST /pair            connect this computer to the signed-in account   -> asks first
    POST /sessions/start  start the monitoring agent in its own window     -> asks first
    POST /sessions/stop   ask the running agent to stop cleanly

SECURITY
--------
Opening a port that a browser can reach is the sensitive part, so:

* **Loopback only.** Bound to 127.0.0.1; nothing on the network can reach it.
* **Origin allow-list.** Every state-changing request must come from a portal
  origin in `helper.allowed_origins`. Any other web page the user happens to
  have open is refused - the browser sets `Origin` and a page cannot forge it.
* **Host check.** The `Host` header must be 127.0.0.1 or localhost on this
  port, which defeats DNS-rebinding (a hostile domain re-pointed at 127.0.0.1).
* **A human confirms on the device.** Pairing and starting a session each show
  a native dialog on this computer. A compromised portal, or a manager, cannot
  enrol a machine or turn a camera on without the person sitting at it agreeing.
* **No shell.** The agent is launched with an argument list, never a command
  string, so a session name cannot inject anything.
* The monitoring agent opens in its **own visible window** for as long as it
  runs (spec sections 25, 34): monitoring is never hidden.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from focusguard_agent.config import AgentSettings, get_settings
from focusguard_agent.synchronization.client import (
    BackendClient,
    EnrollmentFailed,
    TasksUnavailable,
)
from focusguard_agent.synchronization.credentials import (
    NotEnrolled,
    load_credential,
    read_device_uuid,
    save_credential,
)
from focusguard_agent.tools.enroll import CONSENT, CONSENT_VERSION
from focusguard_agent.tools.stop import is_running, read_pid_file, request_stop
from focusguard_shared.constants import TaskCategory
from focusguard_shared.utilities import get_logger

logger = get_logger("helper.server")

_MAX_BODY_BYTES = 4096

#: (title, message) -> True if the person at the computer agreed.
Confirm = Callable[[str, str], bool]

#: argv for `python -m focusguard_agent.agent ...` -> starts it.
Launcher = Callable[[list[str]], Any]


def native_confirm(title: str, message: str) -> bool:
    """Ask in a native Windows dialog, on top of every other window.

    Anywhere without one there is no trustworthy way to ask the person at the
    machine, so the answer is no.
    """
    if sys.platform != "win32":
        logger.warning("No native dialog on %s; refusing %r", sys.platform, title)
        return False

    import ctypes

    mb_yesno, mb_iconquestion = 0x04, 0x20
    mb_topmost, mb_setforeground, mb_defbutton2 = 0x40000, 0x10000, 0x100
    idyes = 6
    flags = mb_yesno | mb_iconquestion | mb_topmost | mb_setforeground | mb_defbutton2
    # Default button is "No": an accidental Enter must not enrol a machine.
    return ctypes.windll.user32.MessageBoxW(None, message, title, flags) == idyes


def launch_agent(arguments: list[str]) -> subprocess.Popen:
    """Start the monitoring agent in a new, visible console window."""
    command = [sys.executable, "-m", "focusguard_agent.agent", *arguments]
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    # Same working directory as the helper, so the agent reads the same .env.
    return subprocess.Popen(command, cwd=os.getcwd(), creationflags=flags)  # noqa: S603


def _clean_text(value: Any, limit: int) -> str:
    """Printable, single-line, bounded. It ends up in a dialog and a window title."""
    text = "".join(ch for ch in str(value or "") if ch.isprintable()).strip()
    return text[:limit]


def _rule_list(value: Any) -> list[str]:
    """Apps or domains for a task rule: bounded, and never mistakable for a flag."""
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for raw in value[:20]:
        item = _clean_text(raw, 120).lower()
        if item and not item.startswith("-") and item not in items:
            items.append(item)
    return items


class HelperError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class FocusGuardHelper:
    """The helper's behaviour, separate from HTTP so it can be tested directly."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        confirm: Confirm = native_confirm,
        launcher: Launcher = launch_agent,
        client_factory: Callable[..., BackendClient] = BackendClient,
    ) -> None:
        self.settings = settings or get_settings()
        self._confirm = confirm
        self._launcher = launcher
        self._client_factory = client_factory
        #: One dialog at a time. Two stacked consent prompts invite a mis-click.
        self._asking = threading.Lock()

    # -- state -------------------------------------------------------------

    @staticmethod
    def _running_session() -> str | None:
        found = read_pid_file()
        if found is None or not is_running(found[0]):
            return None
        return found[1] or "Focus session"

    def status(self) -> dict[str, Any]:
        try:
            credential = load_credential()
        except NotEnrolled:
            credential = None
        session = self._running_session()
        return {
            "service": "focusguard-helper",
            "version": self.settings.agent_version,
            "enrolled": credential is not None,
            "user_id": credential.user_id if credential else None,
            "organization_id": credential.organization_id if credential else None,
            "backend_url": (
                credential.backend_url if credential else self.settings.sync.backend_url
            ),
            "session": {"running": session is not None, "name": session},
        }

    def _ask(self, title: str, message: str) -> bool:
        if not self._asking.acquire(blocking=False):
            raise HelperError(409, "A FocusGuard question is already open on this computer.")
        try:
            return self._confirm(title, message)
        finally:
            self._asking.release()

    # -- actions -----------------------------------------------------------

    def pair(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = _clean_text(payload.get("enrollment_token"), 100)
        if not token:
            raise HelperError(400, "An enrolment token is required.")

        backend = self.settings.sync.backend_url.rstrip("/")
        notice = CONSENT.format(backend=backend).strip()
        notice = notice.split("You can stop monitoring")[0].strip()
        approved = self._ask(
            "FocusGuard - connect this computer?",
            "Your FocusGuard account is asking to connect this computer.\n\n"
            f"{notice}\n\n"
            "You can disconnect this computer at any time.\n\n"
            "Connect this computer?",
        )
        if not approved:
            raise HelperError(403, "Connection was declined on the computer.")

        try:
            with self._client_factory(backend) as client:
                credential = client.enroll(
                    enrollment_token=token,
                    device_uuid=read_device_uuid(),
                    platform=f"{platform.system()} {platform.release()}",
                    agent_version=self.settings.agent_version,
                    consent_version=CONSENT_VERSION,
                )
        except EnrollmentFailed as error:
            raise HelperError(400, str(error)) from error

        save_credential(credential)
        logger.info("Computer connected for user %s", credential.user_id)
        return {"ok": True, "user_id": credential.user_id}

    def tasks(self) -> list[dict[str, Any]]:
        try:
            credential = load_credential()
        except NotEnrolled as error:
            raise HelperError(409, "This computer is not connected yet.") from error
        try:
            with self._client_factory(credential.backend_url, credential=credential) as client:
                return client.assigned_tasks()
        except TasksUnavailable as error:
            raise HelperError(502, str(error)) from error

    def start_session(self, payload: dict[str, Any]) -> dict[str, Any]:
        running = self._running_session()
        if running is not None:
            raise HelperError(409, f"A session is already running: {running!r}.")

        name = _clean_text(payload.get("name"), 200) or "Focus session"
        arguments = ["--name", name]

        task_id = payload.get("assigned_task_id")
        if task_id is not None:
            try:
                task_id = int(task_id)
            except (TypeError, ValueError) as error:
                raise HelperError(400, "assigned_task_id must be a number.") from error
            # Checked here rather than left to the agent: a refusal inside the
            # agent's own window would flash and close before anyone read it.
            match = next((t for t in self.tasks() if int(t["id"]) == task_id), None)
            if match is None:
                raise HelperError(404, "That task is not one of your open tasks.")
            if name == "Focus session":
                name = _clean_text(match.get("title"), 200) or name
            arguments = ["--name", name, "--assigned-task", str(task_id)]
        else:
            task = _clean_text(payload.get("task"), 200)
            category = _clean_text(payload.get("task_category"), 40).upper()
            if task:
                if category and category not in TaskCategory.__members__:
                    raise HelperError(400, f"Unknown task category {category!r}.")
                arguments += ["--task", task, "--task-category", category or "OTHER"]
                for flag in ("productive", "distracting"):
                    items = _rule_list(payload.get(flag))
                    if items:
                        arguments += [f"--{flag}", *items]

        approved = self._ask(
            "FocusGuard - start monitoring?",
            f'Start the focus session "{name}"?\n\n'
            "Your webcam will be analysed on this computer, and the application\n"
            "and website domain you are using will be recorded, until you stop it.\n\n"
            "No video, screenshots or typing are ever recorded or uploaded.\n"
            "A FocusGuard window stays open for as long as monitoring runs.",
        )
        if not approved:
            raise HelperError(403, "Starting the session was declined on the computer.")

        self._launcher(arguments)
        logger.info("Session %r started from the portal", name)
        return {"ok": True, "name": name}

    def stop_session(self) -> dict[str, Any]:
        if self._running_session() is None:
            raise HelperError(409, "No session is running.")
        request_stop()
        return {"ok": True}


# --- HTTP -------------------------------------------------------------------


def make_handler(helper: FocusGuardHelper, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_origins = {origin.rstrip("/") for origin in helper.settings.helper.allowed_origins}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "FocusGuardHelper/1.0"

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("helper %s", format % args)

        # -- guards --------------------------------------------------------

        def _origin(self) -> str | None:
            origin = self.headers.get("Origin")
            return origin.rstrip("/") if origin else None

        def _guard(self, require_origin: bool) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(421, {"error": "This helper only answers on 127.0.0.1."})
                return False
            origin = self._origin()
            if origin is None and not require_origin:
                return True
            if origin not in allowed_origins:
                self._send(403, {"error": "This page is not allowed to control FocusGuard."})
                return False
            return True

        def _send(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            origin = self._origin()
            if origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_raw(self) -> bytes:
            """Consume the body before any reply, even a refusal.

            Answering with the body still unread makes Windows reset the
            connection, and the caller sees a network error instead of the
            refusal. Capped, so a huge body is never read.
            """
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError as error:
                raise HelperError(400, "Bad length.") from error
            if length > _MAX_BODY_BYTES:
                raise HelperError(413, "Request too large.")
            return self.rfile.read(length) if length > 0 else b""

        def _body(self, raw: bytes) -> dict[str, Any]:
            if not raw:
                return {}
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as error:
                raise HelperError(400, "Invalid JSON.") from error
            if not isinstance(payload, dict):
                raise HelperError(400, "Expected a JSON object.")
            return payload

        # -- routes --------------------------------------------------------

        def do_OPTIONS(self) -> None:  # noqa: N802
            if not self._guard(require_origin=True):
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self._origin() or "")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            # Chrome's Private Network Access: a page may reach a loopback
            # service only if the service says it expects that.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if not self._guard(require_origin=False):
                return
            try:
                if self.path == "/status":
                    self._send(200, helper.status())
                elif self.path == "/tasks":
                    self._send(200, helper.tasks())
                else:
                    self._send(404, {"error": "not found"})
            except HelperError as error:
                self._send(error.status, {"error": error.message})

        def do_POST(self) -> None:  # noqa: N802
            try:
                raw = self._read_raw()
            except HelperError as error:
                self.close_connection = True
                self._send(error.status, {"error": error.message})
                return
            if not self._guard(require_origin=True):
                return
            routes = {
                "/pair": lambda body: helper.pair(body),
                "/sessions/start": lambda body: helper.start_session(body),
                "/sessions/stop": lambda body: helper.stop_session(),
            }
            route = routes.get(self.path)
            if route is None:
                self._send(404, {"error": "not found"})
                return
            try:
                self._send(200, route(self._body(raw)))
            except HelperError as error:
                self._send(error.status, {"error": error.message})

    return Handler


class HelperServer:
    """Runs the helper on a background thread (tests) or in the foreground."""

    def __init__(self, helper: FocusGuardHelper, port: int | None = None) -> None:
        self.helper = helper
        self.port = port if port is not None else helper.settings.helper.port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def bind(self) -> bool:
        try:
            self._server = ThreadingHTTPServer(
                ("127.0.0.1", self.port), make_handler(self.helper, self.port)
            )
        except OSError:
            return False
        # Port 0 in tests: pick up the port the OS chose.
        self.port = self._server.server_address[1]
        self._server.RequestHandlerClass = make_handler(self.helper, self.port)
        return True

    def start(self) -> bool:
        if not self.bind():
            return False
        assert self._server is not None
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return True

    def serve_forever(self) -> None:
        assert self._server is not None
        self._server.serve_forever()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
