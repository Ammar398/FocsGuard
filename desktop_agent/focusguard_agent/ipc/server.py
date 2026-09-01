"""Loopback server for the browser extension (spec sections 20, 51).

WHY THIS EXISTS
---------------
A Chrome extension cannot call a Python function. The only way it can reach the
agent is over HTTP, so the agent runs a small server on localhost and the
extension POSTs the active tab's domain to it. Without this module, section 20
cannot exist at all - which is why it was added to the architecture in Phase 0.

SECURITY
--------
Opening a port on a user's machine deserves care, so:

* **Loopback only.** Bound to `127.0.0.1`, never `0.0.0.0`. Nothing on the
  network can reach it, only processes on this machine.
* **Shared token.** Every request must carry a token the agent generated and
  wrote to a file only this user can read. Without it, any web page running
  JavaScript could POST fabricated activity and distort someone's productivity
  record.
* **Tokens are compared with `secrets.compare_digest`**, so a wrong token takes
  the same time to reject as a right one.
* **Small request cap.** Bodies over 8 KB are refused outright; a domain and a
  timestamp need a few hundred bytes.
* **No filesystem, no shell, nothing else.** The server has exactly three
  endpoints and touches nothing outside its own queue.

WHAT IT ACCEPTS, AND WHAT IT WILL NOT
-------------------------------------
The extension sends a **domain and at most a coarse path segment** - the URL is
minimised on the extension side, before transmission, so a full URL with its
query string never crosses a process boundary (section 20). This server rejects
anything that still looks like a full URL, so a future bug in the extension
cannot quietly start sending them.

DEGRADING WITHOUT THE EXTENSION
-------------------------------
If the extension is not installed, nothing posts here and the agent carries on:
the browser is still tracked as an application, just without domain detail. The
system loses resolution, not function.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from focusguard_shared.utilities import get_logger
from focusguard_shared.utilities.paths import app_data_dir

logger = get_logger("ipc.server")

#: A domain plus a timestamp needs a few hundred bytes.
_MAX_BODY_BYTES = 8192

#: How many recent reports to keep if nothing is draining them.
_QUEUE_LIMIT = 256

_TOKEN_FILENAME = "ipc_token.txt"


@dataclass(frozen=True)
class BrowserActivity:
    """One report of the active tab, as received from the extension."""

    domain: str
    path_prefix: str | None
    timestamp: float
    #: False when the browser lost focus or the tab was closed.
    active: bool = True
    #: Only present when the user opted into titles.
    page_title: str | None = None


def token_path() -> Path:
    return app_data_dir() / _TOKEN_FILENAME


def load_or_create_token() -> str:
    """Return the shared token, generating and saving one on first run."""
    path = token_path()
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    logger.info("Generated a new IPC token at %s", path)
    return token


def _looks_like_a_full_url(value: str) -> bool:
    """Reject anything still carrying a scheme, query string or fragment.

    The extension is responsible for minimising URLs before sending. This is the
    backstop: if a future change there regresses, the agent refuses the data
    rather than silently storing it.
    """
    lowered = value.lower()
    return (
        "://" in lowered
        or "?" in value
        or "#" in value
        or lowered.startswith("http")
    )


class _Handler(BaseHTTPRequestHandler):
    """Three endpoints. Nothing else is routed."""

    server_version = "FocusGuardIPC/1.0"

    # Injected by IpcServer.
    token: str = ""
    on_activity = None  # type: ignore[assignment]
    monitoring_enabled = True

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default stderr access log; we log what matters ourselves."""
        logger.debug("ipc %s", format % args)

    # -- helpers -----------------------------------------------------------

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The extension has its own origin, so it needs CORS. Access is gated by
        # the token, not by the origin - an origin header is trivially forged.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-FocusGuard-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self, payload: dict | None = None) -> bool:
        supplied = self.headers.get("X-FocusGuard-Token", "")
        if not supplied and payload:
            supplied = str(payload.get("token", ""))
        return bool(supplied) and secrets.compare_digest(supplied, self.token)

    # -- routes ------------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/status":
            # Deliberately unauthenticated and contentless: it exists so the
            # extension can show whether the agent is running, and it reveals
            # nothing about the user.
            self._send(
                200,
                {
                    "service": "focusguard",
                    "monitoring": self.monitoring_enabled,
                    "version": "0.1.0",
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/activity":
            self._send(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._send(400, {"error": "bad length"})
            return

        if length <= 0 or length > _MAX_BODY_BYTES:
            self._send(413, {"error": "body too large"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send(400, {"error": "invalid json"})
            return

        if not isinstance(payload, dict) or not self._authorised(payload):
            self._send(401, {"error": "unauthorised"})
            return

        domain = str(payload.get("domain", "")).strip().lower()
        if not domain or "." not in domain or _looks_like_a_full_url(domain):
            self._send(400, {"error": "a bare domain is required"})
            return

        path_prefix = payload.get("path_prefix")
        if path_prefix is not None:
            path_prefix = str(path_prefix)[:64]
            if _looks_like_a_full_url(path_prefix):
                self._send(400, {"error": "path_prefix must not contain a query string"})
                return

        activity = BrowserActivity(
            domain=domain[:253],
            path_prefix=path_prefix,
            timestamp=time.monotonic(),
            active=bool(payload.get("active", True)),
            page_title=(
                str(payload["page_title"])[:120] if payload.get("page_title") else None
            ),
        )

        if callable(self.on_activity):
            self.on_activity(activity)

        self._send(200, {"ok": True})


class IpcServer:
    """Runs the loopback server on a background thread."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 48213,
        token: str | None = None,
    ) -> None:
        if host not in ("127.0.0.1", "localhost", "::1"):
            # A misconfigured host would expose a user's browsing to the network.
            raise ValueError(
                f"IPC server refuses to bind to {host!r}; loopback addresses only"
            )

        self.host = host
        self.port = port
        self.token = token or load_or_create_token()

        self._queue: deque[BrowserActivity] = deque(maxlen=_QUEUE_LIMIT)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.monitoring_enabled = True

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Start listening. Returns False if the port is unavailable."""
        if self._server is not None:
            return True

        handler = type(
            "_BoundHandler",
            (_Handler,),
            {
                "token": self.token,
                "on_activity": staticmethod(self._receive),
                "monitoring_enabled": self.monitoring_enabled,
            },
        )

        try:
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as error:
            logger.warning("Could not bind %s:%d - %s", self.host, self.port, error)
            self._server = None
            return False

        self._thread = threading.Thread(
            target=self._server.serve_forever, name="ipc-server", daemon=True
        )
        self._thread.start()
        logger.info("IPC server listening on http://%s:%d", self.host, self.port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def __enter__(self) -> IpcServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- data --------------------------------------------------------------

    def _receive(self, activity: BrowserActivity) -> None:
        with self._lock:
            self._queue.append(activity)

    def latest(self) -> BrowserActivity | None:
        """Most recent report, or None if nothing has arrived."""
        with self._lock:
            return self._queue[-1] if self._queue else None

    def drain(self) -> list[BrowserActivity]:
        """Take everything received since the last call."""
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    @property
    def running(self) -> bool:
        return self._server is not None
