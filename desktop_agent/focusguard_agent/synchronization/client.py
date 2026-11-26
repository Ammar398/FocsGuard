"""HTTP client for the FocusGuard backend (spec sections 33, 37).

WHAT THIS SENDS, AND WHAT IT CANNOT
-----------------------------------
Structured events and aggregate scores. There is no code path here that reads a
frame, a screenshot, a window's contents or typed input - the payload builders in
`uploader.py` construct dictionaries from database rows, and the backend's schema
would reject anything else with `extra="forbid"` (section 33).

FAILURE IS NORMAL, NOT EXCEPTIONAL
----------------------------------
A laptop closes, a hotspot drops, a server restarts. None of those are errors the
user should see. Every method here returns a result object rather than raising on
a network problem, so the agent's loop can keep monitoring while uploads wait.
Nothing is ever marked synced on a failure, so the data simply goes next time.

The one thing worth distinguishing is *why* it failed:

* a network or 5xx error is **temporary** - retry later;
* a 401 or 403 is **permanent** until a human acts - the credential is wrong or
  revoked, and retrying forever would hammer the server and hide the problem.

`UploadResult.retryable` carries that distinction so the caller does not have to
re-derive it from a status code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from focusguard_agent.synchronization.credentials import DeviceCredential
from focusguard_shared.utilities import get_logger

logger = get_logger("agent.sync.client")

API_PREFIX = "/api/v1"


@dataclass
class UploadResult:
    """The outcome of one request."""

    ok: bool
    status_code: int | None = None
    detail: str = ""

    #: False for a credential problem: retrying cannot fix it, and a human must.
    retryable: bool = True

    accepted: dict[str, int] = field(default_factory=dict)
    duplicates: dict[str, int] = field(default_factory=dict)
    session_id: int | None = None

    @property
    def total_accepted(self) -> int:
        return sum(self.accepted.values())

    @property
    def total_duplicates(self) -> int:
        return sum(self.duplicates.values())


class EnrollmentFailed(Exception):
    """The enrolment token was refused. A new one is needed."""


class TasksUnavailable(Exception):
    """The assigned-task list could not be fetched."""


class BackendClient:
    """Talks to one backend, as one device."""

    def __init__(
        self,
        base_url: str,
        credential: DeviceCredential | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._credential = credential
        self._timeout = timeout

        # One session, so TCP and TLS setup is paid once rather than per upload.
        # The agent uploads every 30 seconds for hours.
        self._http = requests.Session()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> BackendClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self._base}{API_PREFIX}{path}"

    # -- enrolment ---------------------------------------------------------

    def enroll(
        self,
        enrollment_token: str,
        device_uuid: str,
        platform: str | None = None,
        agent_version: str | None = None,
        consent_version: str | None = None,
        replace_device_secret: str | None = None,
    ) -> DeviceCredential:
        """Exchange a one-time enrolment token for a lasting credential.

        The only unauthenticated call the agent makes. Raises rather than
        returning a result: enrolment is interactive, a person is watching, and
        there is nothing sensible to do afterwards if it fails.
        """
        try:
            response = self._http.post(
                self._url("/devices/enroll"),
                json={
                    "enrollment_token": enrollment_token,
                    "device_uuid": device_uuid,
                    "platform": platform,
                    "agent_version": agent_version,
                    "consent_version": consent_version,
                    "replace_device_secret": replace_device_secret,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise EnrollmentFailed(
                f"Could not reach the backend at {self._base}. Is it running?"
            ) from error

        if response.status_code != 200:
            raise EnrollmentFailed(_detail(response))

        payload = response.json()
        return DeviceCredential(
            device_uuid=payload["device_uuid"],
            device_secret=payload["device_secret"],
            backend_url=self._base,
            user_id=payload.get("user_id"),
            organization_id=payload.get("organization_id"),
        )

    # -- upload ------------------------------------------------------------

    def upload_batch(self, batch: dict[str, Any]) -> UploadResult:
        """Send one batch of events."""
        return self._post("/agent/events", batch, expect=200)

    def heartbeat(
        self,
        session_uuid: str | None = None,
        pending_events: int = 0,
        agent_version: str | None = None,
    ) -> UploadResult:
        """Report liveness (spec section 36).

        Cheap and frequent, so a manager board can show who is working without
        holding a connection open to every machine.
        """
        if self._credential is None:
            return UploadResult(ok=False, detail="not enrolled", retryable=False)

        return self._post(
            "/agent/heartbeat",
            {
                "device_uuid": self._credential.device_uuid,
                "session_uuid": session_uuid,
                "pending_events": pending_events,
                "agent_version": agent_version,
            },
            expect=204,
        )

    def assigned_tasks(self) -> list[dict[str, Any]]:
        """The open tasks assigned to this device's user (Milestone E).

        Raises `TasksUnavailable` with a readable reason, because this is called
        interactively when an employee starts a task and they need to know why
        it did not work.
        """
        if self._credential is None:
            raise TasksUnavailable("This machine is not enrolled.")
        try:
            response = self._http.get(
                self._url("/agent/tasks"),
                headers=self._credential.headers,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            raise TasksUnavailable(
                f"Could not reach the backend at {self._base}. Is it running?"
            ) from error

        if response.status_code != 200:
            raise TasksUnavailable(_detail(response))
        body = response.json()
        return body if isinstance(body, list) else []

    def _post(self, path: str, payload: dict[str, Any], expect: int) -> UploadResult:
        if self._credential is None:
            return UploadResult(ok=False, detail="not enrolled", retryable=False)

        try:
            response = self._http.post(
                self._url(path),
                json=payload,
                headers=self._credential.headers,
                timeout=self._timeout,
            )
        except requests.RequestException as error:
            # Offline, DNS failure, timeout. Entirely expected on a laptop.
            return UploadResult(ok=False, detail=str(error), retryable=True)

        if response.status_code == expect:
            body = _json_or_empty(response) if expect != 204 else {}
            return UploadResult(
                ok=True,
                status_code=response.status_code,
                accepted=body.get("accepted", {}),
                duplicates=body.get("duplicates", {}),
                session_id=body.get("session_id"),
            )

        # 401/403: the credential is wrong or revoked. 422: this agent is sending
        # something this server will never accept. Retrying either forever is
        # noise that hides a problem needing a person.
        permanent = response.status_code in (401, 403, 422)
        if permanent:
            logger.error(
                "Upload refused (%d): %s", response.status_code, _detail(response)
            )

        return UploadResult(
            ok=False,
            status_code=response.status_code,
            detail=_detail(response),
            retryable=not permanent,
        )


def _json_or_empty(response: requests.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _detail(response: requests.Response) -> str:
    """A readable reason, without dumping an HTML error page into the log."""
    body = _json_or_empty(response)
    detail = body.get("detail")
    if isinstance(detail, str):
        return detail
    if detail:
        return str(detail)
    return response.text[:200]
