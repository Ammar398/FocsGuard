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
    POST /camera/check    open the camera preview + personal calibration     -> asks first
    POST /prefs           turn "always allow" off again (never on - only the dialog can)
    POST /extension/setup open the extension folder and the browser's extensions page
    POST /extension/hello (the FocusGuard browser extension only) pair it: hand it
                          the agent's token, and note that it is installed and on

SCHEDULED WORK TIME
-------------------
A manager can give a task a weekly time ("Mon-Fri 09:00 for 4 hours"). The
Helper checks every 30 seconds and, inside that window, starts the task's
session by itself - with the same camera question as a manual start, unless
this computer said "Always allow". The session window is visible, and closing
it ends the session; it is not started again in the same window.

Turning the camera on (a session or the camera check) asks with three choices:
Start once, Always allow on this computer, or Cancel. "Always allow" is stored
on the computer and can only be granted in that dialog, by the person at the
computer; the portal can only take it away. Connecting the computer to an
account always asks, every time.

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
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from focusguard_agent.calibration.ear_calibration import load_profile
from focusguard_agent.capture.camera_apps import CameraReadiness, check_camera
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
from focusguard_shared.utilities.paths import app_data_dir

logger = get_logger("helper.server")

_MAX_BODY_BYTES = 4096

#: (title, message) -> True if the person at the computer agreed.
Confirm = Callable[[str, str], bool]

#: argv for `python -m focusguard_agent.agent ...` -> starts it.
Launcher = Callable[[list[str]], Any]

#: (title, message) -> "once", "always" or "no".
CameraDialog = Callable[[str, str], str]

#: () -> whether the camera can be used, opening Camo/DroidCam if needed.
CameraProbe = Callable[[], CameraReadiness]

_PREFS_FILE = "helper_prefs.json"

#: The FocusGuard browser extension. Its manifest carries a fixed public key, so
#: this ID is the same on every computer, in Chrome and in Edge - and a web page
#: can never send this Origin.
EXTENSION_ID = "fjlahpiccoolnocmkackboinmkolhhpi"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}"
_EXTENSION_FILE = "extension.json"
#: The extension says hello every ~15 seconds while the browser is open.
_EXTENSION_FRESH_SECONDS = 90

_SCHEDULE_FILE = "schedule_state.json"
_SCHEDULE_TASKS_SECONDS = 120

#: (folder, "chrome" | "edge") -> opens the folder and the extensions page.
ExtensionOpener = Callable[[Path, str], Any]


def extension_folder() -> Path | None:
    """Where the unpacked extension is on this computer, if it is."""
    base = Path(__file__).resolve().parents[3]
    # Installed: <app>/extension. A developer checkout: browser_extension/src.
    for candidate in (base / "extension", base / "browser_extension" / "src"):
        if (candidate / "manifest.json").is_file():
            return candidate
    return None


def _browser_exe(browser: str) -> str | None:
    name = {
        "chrome": r"Google\Chrome\Application\chrome.exe",
        "edge": r"Microsoft\Edge\Application\msedge.exe",
    }[browser]
    for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        folder = os.environ.get(base)
        if folder and (Path(folder) / name).is_file():
            return str(Path(folder) / name)
    return None


def open_extension_setup(folder: Path, browser: str) -> None:
    """Open the extension folder, copy its path, and open the extensions page.

    A web page may not open chrome://extensions, and Chrome only installs an
    extension from outside its store when the person loads it themselves - so
    this is as far as it can be automated: everything ready, two clicks left.
    """
    if sys.platform != "win32":
        return
    try:  # the path on the clipboard, ready for the "Load unpacked" folder box
        subprocess.run(  # noqa: S603
            ["clip.exe"], input=("\ufeff" + str(folder)).encode("utf-16-le"),
            check=False, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        pass
    os.startfile(str(folder))  # noqa: S606 - Explorer, on a path we chose
    exe = _browser_exe(browser)
    if exe:
        page = "edge://extensions/" if browser == "edge" else "chrome://extensions/"
        subprocess.Popen([exe, page])  # noqa: S603


def _schedule_window(task: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The start and end of this task's scheduled time on `now`'s day, if any."""
    days = task.get("schedule_days") or []
    at = task.get("schedule_time") or ""
    try:
        hour, minute = (int(part) for part in str(at).split(":"))
        minutes = int(task.get("schedule_minutes") or 0)
    except (TypeError, ValueError):
        return None
    if minutes <= 0 or now.weekday() not in days:
        return None
    start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return start, start + timedelta(minutes=minutes)


def due_now(task: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The window if `now` is inside it, with at least a minute of it left."""
    window = _schedule_window(task, now)
    if window is None:
        return None
    start, end = window
    return window if start <= now < end - timedelta(minutes=1) else None


def next_scheduled(tasks: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """The next (or current) scheduled session over the coming week."""
    best: tuple[datetime, dict[str, Any], datetime] | None = None
    for offset in range(8):
        day = now + timedelta(days=offset)
        for task in tasks:
            window = _schedule_window(task, day)
            if window is None or window[1] <= now:
                continue
            if best is None or window[0] < best[0]:
                best = (window[0], task, window[1])
        if best is not None:
            break
    if best is None:
        return None
    start, task, end = best
    return {
        "task_id": task.get("id"),
        "title": task.get("title"),
        "starts_at": start.isoformat(timespec="minutes"),
        "ends_at": end.isoformat(timespec="minutes"),
        "now": start <= now,
    }


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


def _launch_module(module: str, arguments: list[str]) -> subprocess.Popen:
    """Run `python -m module ...` in a new, visible console window."""
    command = [sys.executable, "-m", module, *arguments]
    flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    # Same working directory as the helper, so the child reads the same .env.
    return subprocess.Popen(command, cwd=os.getcwd(), creationflags=flags)  # noqa: S603


def native_camera_dialog(title: str, message: str) -> str:
    """Ask before turning the camera on: Start once / Always allow / Cancel.

    A small, always-on-top window in FocusGuard's colours. Cancel has the
    focus, so a stray Enter never turns a camera on. Falls back to a standard
    Yes/No/Cancel box if Tk is unavailable.
    """
    try:
        import tkinter as tk
    except ImportError:  # pragma: no cover - Tk ships with Python on Windows
        return _messagebox_camera_dialog(title, message)

    answer = {"value": "no"}
    root = tk.Tk()
    root.title(title)
    root.configure(bg="#141310")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    def choose(value: str) -> None:
        answer["value"] = value
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", lambda: choose("no"))
    tk.Label(
        root, text=title.replace("FocusGuard - ", ""), bg="#141310", fg="#ffcc00",
        font=("Segoe UI Semibold", 13), anchor="w",
    ).pack(fill="x", padx=22, pady=(18, 4))
    tk.Label(
        root, text=message, bg="#141310", fg="#f2efe6", font=("Segoe UI", 10),
        justify="left", wraplength=460, anchor="w",
    ).pack(fill="x", padx=22, pady=(0, 16))

    row = tk.Frame(root, bg="#141310")
    row.pack(fill="x", padx=22, pady=(0, 18))
    style = {"font": ("Segoe UI Semibold", 10), "bd": 0, "padx": 14, "pady": 7,
             "cursor": "hand2"}
    tk.Button(row, text="Start once", bg="#ffcc00", fg="#0b0a08",
              activebackground="#ffd633", command=lambda: choose("once"), **style
              ).pack(side="left")
    tk.Button(row, text="Always allow on this computer", bg="#2a2823", fg="#f2efe6",
              activebackground="#3a3730", command=lambda: choose("always"), **style
              ).pack(side="left", padx=8)
    cancel = tk.Button(row, text="Cancel", bg="#141310", fg="#9a958a",
                       activebackground="#2a2823", command=lambda: choose("no"), **style)
    cancel.pack(side="right")
    root.bind("<Escape>", lambda _event: choose("no"))

    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_width()) // 2
    y = (root.winfo_screenheight() - root.winfo_height()) // 3
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.focus_force()
    cancel.focus_set()
    root.mainloop()
    return answer["value"]


def _messagebox_camera_dialog(title: str, message: str) -> str:
    if sys.platform != "win32":
        return "no"
    import ctypes

    text = (
        f"{message}\n\nYes = start now and always allow on this computer\n"
        "No = start just this once\nCancel = do not start"
    )
    flags = 0x03 | 0x20 | 0x40000 | 0x10000 | 0x200  # YESNOCANCEL, ?, topmost, front, default 3rd
    result = ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    return {6: "always", 7: "once"}.get(result, "no")


def launch_agent(arguments: list[str]) -> subprocess.Popen:
    """Start the monitoring agent in its own window."""
    return _launch_module("focusguard_agent.agent", arguments)


def launch_camera_check(arguments: list[str]) -> subprocess.Popen:
    """Open the camera preview and personal calibration in its own window."""
    return _launch_module("focusguard_agent.tools.calibrate", arguments)


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
    def __init__(self, status: int, message: str, extra: dict | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


class FocusGuardHelper:
    """The helper's behaviour, separate from HTTP so it can be tested directly."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        confirm: Confirm = native_confirm,
        launcher: Launcher = launch_agent,
        client_factory: Callable[..., BackendClient] = BackendClient,
        camera_launcher: Launcher = launch_camera_check,
        camera_dialog: CameraDialog = native_camera_dialog,
        camera_probe: CameraProbe | None = None,
        extension_opener: ExtensionOpener = open_extension_setup,
    ) -> None:
        self.settings = settings or get_settings()
        self._extension_opener = extension_opener
        #: (monotonic time, switched on?) of the extension's last hello.
        self._extension_seen: tuple[float, bool] | None = None
        #: (monotonic time, tasks) for the scheduler, so it asks the server rarely.
        self._schedule_cache: tuple[float, list[dict[str, Any]]] | None = None
        #: What the scheduler last did, for the portal.
        self._schedule_note: dict[str, Any] | None = None
        self._scheduler_stop = threading.Event()
        self._confirm = confirm
        self._launcher = launcher
        self._camera_launcher = camera_launcher
        self._camera_dialog = camera_dialog
        self._camera_probe = camera_probe or (
            lambda: check_camera(self.settings.camera.device_name)
        )
        #: The open camera-check window, so a session cannot start while it
        #: holds the camera (two processes cannot share most webcams).
        self._camera_process: Any = None
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

    # -- remembered approval -----------------------------------------------

    @staticmethod
    def _prefs_path():
        return app_data_dir() / _PREFS_FILE

    def _always_allowed(self) -> bool:
        try:
            return bool(json.loads(self._prefs_path().read_text(encoding="utf-8")).get(
                "camera_always_allowed"
            ))
        except (OSError, ValueError):
            return False

    def _set_always_allowed(self, value: bool) -> None:
        path = self._prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"camera_always_allowed": value}), encoding="utf-8")
        logger.info("Camera approval set to %s", "always allow" if value else "ask every time")

    def set_prefs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The portal may only turn 'always allow' OFF. Turning it on needs the dialog."""
        if payload.get("ask_every_time") is True:
            self._set_always_allowed(False)
        return {"ok": True, "camera_always_allowed": self._always_allowed()}

    def _approve_camera(self, title: str, message: str) -> None:
        """Ask before the camera turns on, unless this computer said 'always allow'."""
        if self._always_allowed():
            return
        if not self._asking.acquire(blocking=False):
            raise HelperError(409, "A FocusGuard question is already open on this computer.")
        try:
            answer = self._camera_dialog(title, message)
        finally:
            self._asking.release()
        if answer == "always":
            self._set_always_allowed(True)
        elif answer != "once":
            raise HelperError(403, "Turning the camera on was declined on the computer.")

    def _ready_camera(self) -> CameraReadiness:
        """Make sure a camera will send video; open Camo/DroidCam if it is closed."""
        readiness = self._camera_probe()
        if not readiness.ready:
            raise HelperError(424, readiness.message, {"camera": readiness.as_dict()})
        return readiness

    def _chosen_camera(self) -> str | None:
        """The camera a session would use right now (webcam first). Cached 15 s:
        the portal asks every few seconds, and listing devices costs a little."""
        import time

        now = time.monotonic()
        cached = getattr(self, "_camera_cache", None)
        if cached is not None and now - cached[0] < 15:
            return cached[1]
        try:
            from focusguard_agent.capture.camera_apps import choose_camera, running_processes
            from focusguard_agent.capture.devices import enumerate_devices

            names = [device.name for device in enumerate_devices()]
            chosen = choose_camera(names, self.settings.camera.device_name, running_processes())
        except Exception:  # noqa: BLE001 - status must never fail over this
            chosen = self.settings.camera.device_name or None
        self._camera_cache = (now, chosen)
        return chosen

    def _camera_check_running(self) -> bool:
        process = self._camera_process
        if process is None:
            return False
        poll = getattr(process, "poll", None)
        if poll is None or poll() is not None:
            self._camera_process = None
            return False
        return True

    def status(self) -> dict[str, Any]:
        try:
            credential = load_credential()
        except NotEnrolled:
            credential = None
        session = self._running_session()
        profile = load_profile()
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
            "camera_always_allowed": self._always_allowed(),
            "camera": {
                "device_name": self._chosen_camera(),
                "check_running": self._camera_check_running(),
                "calibrated": profile is not None,
                "calibrated_at": profile.created_at if profile else None,
            },
            "extension": self.extension_status(),
            "schedule": {
                "next": next_scheduled(
                    self._schedule_cache[1] if self._schedule_cache else [], datetime.now()
                ),
                "last": self._schedule_note,
            },
        }

    # -- the browser extension ---------------------------------------------

    @staticmethod
    def _extension_record() -> dict[str, Any]:
        try:
            data = json.loads((app_data_dir() / _EXTENSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def extension_status(self) -> dict[str, Any]:
        record = self._extension_record()
        seen = self._extension_seen
        folder = extension_folder()
        return {
            # Paired once on this computer (kept across restarts).
            "installed": bool(record.get("paired_at")),
            # Said hello in the last minute and a half: the browser is open.
            "connected": seen is not None
            and time.monotonic() - seen[0] < _EXTENSION_FRESH_SECONDS,
            "enabled": seen[1] if seen is not None else None,
            "browser": record.get("browser"),
            "version": record.get("version"),
            "paired_at": record.get("paired_at"),
            "folder": str(folder) if folder else None,
        }

    def extension_hello(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The extension checking in. Returns the token the agent expects."""
        from focusguard_agent.ipc.server import load_or_create_token

        browser = "edge" if payload.get("browser") == "edge" else "chrome"
        version = _clean_text(payload.get("version"), 20)
        self._extension_seen = (time.monotonic(), payload.get("enabled") is not False)
        record = self._extension_record()
        if (not record.get("paired_at") or record.get("browser") != browser
                or record.get("version") != version):
            record.update(browser=browser, version=version)
            record.setdefault("paired_at", datetime.now(UTC).isoformat(timespec="seconds"))
            path = app_data_dir() / _EXTENSION_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record), encoding="utf-8")
            logger.info("Browser extension paired (%s %s)", browser, version)
        return {"ok": True, "token": load_or_create_token()}

    def extension_setup(self, payload: dict[str, Any]) -> dict[str, Any]:
        folder = extension_folder()
        if folder is None:
            raise HelperError(
                404, "The extension files are not on this computer. Download FocusGuard "
                "from the portal and run the installer again."
            )
        browser = "edge" if payload.get("browser") == "edge" else "chrome"
        self._extension_opener(folder, browser)
        return {"ok": True, "folder": str(folder), "browser": browser}

    def _require_extension(self) -> None:
        """Employees: an organisation's sessions need the browser extension."""
        try:
            credential = load_credential()
        except NotEnrolled:
            return
        if credential.organization_id is None:
            return  # a student: the extension is recommended, not required
        if not self._extension_record().get("paired_at"):
            raise HelperError(
                412,
                "Your organisation requires the FocusGuard browser extension, so that work "
                "websites are counted. Add it under This computer -> Browser extension in "
                "the portal, then start again.",
                {"extension_required": True},
            )

    # -- scheduled work time -------------------------------------------------

    def _scheduled_tasks(self) -> list[dict[str, Any]]:
        cached = self._schedule_cache
        if cached is not None and time.monotonic() - cached[0] < _SCHEDULE_TASKS_SECONDS:
            return cached[1]
        try:
            tasks = [task for task in self.tasks() if task.get("schedule_time")]
        except HelperError:
            return cached[1] if cached else []
        self._schedule_cache = (time.monotonic(), tasks)
        return tasks

    @staticmethod
    def _handled(today: str) -> set[str]:
        try:
            data = json.loads((app_data_dir() / _SCHEDULE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return set(data.get("handled", [])) if data.get("day") == today else set()

    @staticmethod
    def _save_handled(today: str, handled: set[str]) -> None:
        path = app_data_dir() / _SCHEDULE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"day": today, "handled": sorted(handled)}), encoding="utf-8")

    def scheduled_tick(self, now: datetime | None = None) -> dict[str, Any] | None:
        """Start a scheduled task session if one is due. Returns what happened."""
        now = now or datetime.now()
        try:
            credential = load_credential()
        except NotEnrolled:
            return None
        if credential.organization_id is None:
            return None

        today = now.date().isoformat()
        handled = self._handled(today)
        for task in self._scheduled_tasks():
            window = due_now(task, now)
            if window is None:
                continue
            key = f"{task['id']}@{window[0].isoformat(timespec='minutes')}"
            if key in handled:
                continue
            # Marked before trying: a declined question, or a window the person
            # closed, must not come back every 30 seconds.
            handled.add(key)
            self._save_handled(today, handled)
            title = _clean_text(task.get("title"), 200) or "Scheduled task"
            note: dict[str, Any] = {
                "task_id": task["id"], "title": title,
                "at": now.isoformat(timespec="minutes"),
            }
            if self._running_session() is not None:
                note.update(ok=False, error="A session was already running.")
            else:
                seconds = int((window[1] - now).total_seconds())
                try:
                    self.start_session({"assigned_task_id": task["id"]}, scheduled_seconds=seconds)
                    note.update(ok=True, minutes=round(seconds / 60))
                except HelperError as error:
                    note.update(ok=False, error=error.message)
            self._schedule_note = note
            logger.info("Scheduled session %r: %s", title,
                        "started" if note.get("ok") else note.get("error"))
            return note
        return None

    def start_scheduler(self, interval: float = 30.0) -> threading.Thread:
        def loop() -> None:
            while not self._scheduler_stop.wait(interval):
                try:
                    self.scheduled_tick()
                except Exception:  # noqa: BLE001 - the Helper must keep running
                    logger.exception("Scheduled start check failed")

        thread = threading.Thread(target=loop, name="focusguard-schedule", daemon=True)
        thread.start()
        return thread

    def stop_scheduler(self) -> None:
        self._scheduler_stop.set()

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

        # Already connected (to another account, or an earlier one): send the
        # current secret as proof that this really is the same computer, so the
        # server lets the connection move rather than refusing it.
        try:
            current_secret: str | None = load_credential().device_secret
        except NotEnrolled:
            current_secret = None

        try:
            with self._client_factory(backend) as client:
                credential = client.enroll(
                    enrollment_token=token,
                    device_uuid=read_device_uuid(),
                    platform=f"{platform.system()} {platform.release()}",
                    agent_version=self.settings.agent_version,
                    consent_version=CONSENT_VERSION,
                    replace_device_secret=current_secret,
                )
        except EnrollmentFailed as error:
            raise HelperError(400, str(error)) from error

        save_credential(credential)
        if current_secret is not None:
            # Moved from another account: what was recorded before stays here.
            self._keep_unsent_local()
        logger.info("Computer connected for user %s", credential.user_id)
        return {"ok": True, "user_id": credential.user_id}

    @staticmethod
    def _keep_unsent_local() -> None:
        from focusguard_agent.local_store.database import LocalStore

        with LocalStore() as store:
            store.keep_unsynced_local()

    def tidy_local_store(self) -> None:
        """Close sessions a killed agent left open - unless one is running now."""
        if self._running_session() is not None:
            return
        from focusguard_agent.local_store.database import LocalStore

        with LocalStore() as store:
            store.close_orphaned_sessions()

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

    def camera_check(self) -> dict[str, Any]:
        """Open a camera preview that also measures this person's open-eye EAR.

        Twelve seconds of looking at the screen and blinking normally. The
        result is one number - a personal eye-closure threshold - saved on this
        computer and used by every later session. No frame is stored or sent.
        """
        if self._running_session() is not None:
            raise HelperError(409, "Stop the running session first; it is using the camera.")
        if self._camera_check_running():
            raise HelperError(409, "The camera check is already open on this computer.")

        readiness = self._ready_camera()
        camera = readiness.device or "your webcam"
        self._approve_camera(
            "FocusGuard - open the camera check?",
            f"Open a camera window using {camera}?\n\n"
            "You will see yourself with face points drawn on. For the first 12 seconds,\n"
            "look at the screen and blink normally while FocusGuard learns how your\n"
            "eyes look when they are open. The window then stays live until you close it.\n\n"
            "Nothing is recorded or uploaded. The result is one number, kept on this\n"
            "computer.",
        )

        self._camera_process = self._camera_launcher(["--show", "--keep-open", "--wait-on-exit"])
        logger.info("Camera check opened from the portal")
        return {"ok": True, "camera": readiness.as_dict()}

    def start_session(
        self, payload: dict[str, Any], scheduled_seconds: int | None = None
    ) -> dict[str, Any]:
        running = self._running_session()
        if running is not None:
            raise HelperError(409, f"A session is already running: {running!r}.")
        if self._camera_check_running():
            raise HelperError(409, "Close the camera check window first; it is using the camera.")

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

        self._require_extension()
        readiness = self._ready_camera()
        if scheduled_seconds is not None:
            minutes = max(1, round(scheduled_seconds / 60))
            question = (
                f'Your scheduled work time for "{name}" starts now.\n\n'
                f"Start the session for the next {minutes} minutes? Your webcam will be\n"
                "analysed on this computer, and the application and website domain you\n"
                "use will be recorded. Close the FocusGuard window to stop at any time.\n\n"
                "No video, screenshots or typing are ever recorded or uploaded."
            )
            arguments += ["--seconds", str(scheduled_seconds)]
        else:
            question = (
                f'Start the focus session "{name}"?\n\n'
                "Your webcam will be analysed on this computer, and the application\n"
                "and website domain you are using will be recorded, until you stop it.\n\n"
                "No video, screenshots or typing are ever recorded or uploaded.\n"
                "A FocusGuard window stays open for as long as monitoring runs."
            )
        self._approve_camera("FocusGuard - start monitoring?", question)

        # --preview: a live analysis window that stays up for the whole
        # session; closing it is how the person ends the session.
        self._launcher([*arguments, "--preview"])
        logger.info("Session %r started from the portal", name)
        return {"ok": True, "name": name, "camera": readiness.as_dict()}

    def stop_session(self) -> dict[str, Any]:
        if self._running_session() is None:
            raise HelperError(409, "No session is running.")
        request_stop()
        return {"ok": True}


# --- HTTP -------------------------------------------------------------------


def portal_origins(settings: AgentSettings) -> set[str]:
    """Web pages allowed to talk to the helper.

    The configured list (the local development portals) plus the server this
    computer uploads to: a deployed FocusGuard serves its portal from the same
    address as its API, so trusting the server means trusting its portal - and
    nothing else on the internet.
    """
    from urllib.parse import urlsplit

    origins = {origin.rstrip("/") for origin in settings.helper.allowed_origins}
    parts = urlsplit(settings.sync.backend_url)
    if parts.scheme in ("http", "https") and parts.netloc:
        origins.add(f"{parts.scheme}://{parts.netloc}")
    return origins


def make_handler(helper: FocusGuardHelper, port: int) -> type[BaseHTTPRequestHandler]:
    allowed_origins = portal_origins(helper.settings)
    extension_origins = {EXTENSION_ORIGIN}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "FocusGuardHelper/1.0"

        def log_message(self, format: str, *args: object) -> None:
            logger.debug("helper %s", format % args)

        # -- guards --------------------------------------------------------

        def _origin(self) -> str | None:
            origin = self.headers.get("Origin")
            return origin.rstrip("/") if origin else None

        def _guard(self, require_origin: bool, origins: set[str] = allowed_origins) -> bool:
            if self.headers.get("Host", "") not in allowed_hosts:
                self._send(421, {"error": "This helper only answers on 127.0.0.1."})
                return False
            origin = self._origin()
            if origin is None and not require_origin:
                return True
            if origin not in origins:
                self._send(403, {"error": "This page is not allowed to control FocusGuard."})
                return False
            return True

        def _send(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            origin = self._origin()
            if origin in allowed_origins or origin in extension_origins:
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
            # The extension's popup may read /status too ("is FocusGuard running?").
            origins = allowed_origins | extension_origins if self.path == "/status" else (
                allowed_origins
            )
            if not self._guard(require_origin=False, origins=origins):
                return
            try:
                if self.path == "/status":
                    self._send(200, helper.status())
                elif self.path == "/tasks":
                    self._send(200, helper.tasks())
                else:
                    self._send(404, {"error": "not found"})
            except HelperError as error:
                self._send(error.status, {"error": error.message, **error.extra})

        def do_POST(self) -> None:  # noqa: N802
            try:
                raw = self._read_raw()
            except HelperError as error:
                self.close_connection = True
                self._send(error.status, {"error": error.message, **error.extra})
                return
            if self.path == "/extension/hello":
                # Only the extension itself; no web page, not even the portal.
                if not self._guard(require_origin=True, origins=extension_origins):
                    return
                try:
                    self._send(200, helper.extension_hello(self._body(raw)))
                except HelperError as error:
                    self._send(error.status, {"error": error.message, **error.extra})
                return
            if not self._guard(require_origin=True):
                return
            routes = {
                "/pair": lambda body: helper.pair(body),
                "/sessions/start": lambda body: helper.start_session(body),
                "/sessions/stop": lambda body: helper.stop_session(),
                "/camera/check": lambda body: helper.camera_check(),
                "/prefs": lambda body: helper.set_prefs(body),
                "/extension/setup": lambda body: helper.extension_setup(body),
            }
            route = routes.get(self.path)
            if route is None:
                self._send(404, {"error": "not found"})
                return
            try:
                self._send(200, route(self._body(raw)))
            except HelperError as error:
                self._send(error.status, {"error": error.message, **error.extra})

    return Handler


class _ExclusiveServer(ThreadingHTTPServer):
    """A server that owns its port outright.

    The standard library sets SO_REUSEADDR, which on Windows means *another
    process may bind the same port* - so a second Helper would start beside the
    first instead of noticing it, and requests would land on either. Exclusive
    use makes the second bind fail, which is how `bind()` knows one is running.
    """

    allow_reuse_address = False

    def server_bind(self) -> None:
        import socket

        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class HelperServer:
    """Runs the helper on a background thread (tests) or in the foreground."""

    def __init__(self, helper: FocusGuardHelper, port: int | None = None) -> None:
        self.helper = helper
        self.port = port if port is not None else helper.settings.helper.port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def bind(self) -> bool:
        try:
            self._server = _ExclusiveServer(
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
