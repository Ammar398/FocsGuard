"""Turning browser reports into classified activity (spec sections 20, 21).

WHERE THIS SITS
---------------
    extension  --HTTP-->  ipc.server  -->  BrowserMonitor  -->  ActivityAggregator
    (minimises URL)       (validates)      (this module)        (durations)

The extension already reduced the URL to a domain. This module decides whether
the domain is currently relevant, and hands it to the classifier and the
task-context engine.

WHY REPORTS EXPIRE
------------------
The extension reports on a timer and on tab switches. If the browser is closed,
or the extension is disabled, or Chrome's service worker is asleep, the reports
simply stop - there is no "goodbye" message to rely on.

So a report is only considered current for `stale_after_seconds`. Without that,
closing the browser mid-session would leave the last domain apparently open
forever, and a user who spent thirty seconds on a site before switching to
Photoshop would be recorded as having browsed for the rest of the afternoon.

THE BROWSER MUST BE IN FRONT
----------------------------
A domain is only attributed while a browser is the foreground application. A
YouTube tab left open behind the IDE is not what the user is doing, and counting
it would make every report wrong in the same direction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from focusguard_agent.activity_monitor.classifier import ClassifiedActivity
from focusguard_agent.activity_monitor.task_context import TaskContextEngine
from focusguard_agent.ipc.server import BrowserActivity, IpcServer
from focusguard_shared.constants import ActivityCategory
from focusguard_shared.constants.catalog import APPLICATION_CATEGORIES
from focusguard_shared.utilities import get_logger

logger = get_logger("browser.monitor")

#: Applications whose category is decided by the site open inside them.
BROWSER_APPLICATIONS: frozenset[str] = frozenset(
    name
    for name, category in APPLICATION_CATEGORIES.items()
    if category is ActivityCategory.CONTEXT_DEPENDENT
)


@dataclass(frozen=True)
class BrowserState:
    """What the browser is currently showing, if anything usable."""

    domain: str | None
    path_prefix: str | None
    age_seconds: float
    stale: bool

    @property
    def usable(self) -> bool:
        return self.domain is not None and not self.stale


class BrowserMonitor:
    """Combines foreground application with the extension's domain reports."""

    def __init__(
        self,
        ipc: IpcServer | None = None,
        task_engine: TaskContextEngine | None = None,
        stale_after_seconds: float = 30.0,
    ) -> None:
        self._ipc = ipc
        self._engine = task_engine or TaskContextEngine()
        self._stale_after = stale_after_seconds
        self._latest: BrowserActivity | None = None

    @property
    def extension_connected(self) -> bool:
        """True once the extension has reported at least once recently."""
        return self._latest is not None and not self._state(time.monotonic()).stale

    def ingest(self, activity: BrowserActivity) -> None:
        """Accept a report directly. Used in tests and by the agent loop."""
        self._latest = activity

    def poll(self) -> None:
        """Drain anything the IPC server has received."""
        if self._ipc is None:
            return
        for activity in self._ipc.drain():
            self._latest = activity

    def _state(self, now: float) -> BrowserState:
        if self._latest is None:
            return BrowserState(None, None, age_seconds=0.0, stale=True)

        age = max(0.0, now - self._latest.timestamp)
        return BrowserState(
            domain=self._latest.domain,
            path_prefix=self._latest.path_prefix,
            age_seconds=age,
            stale=age > self._stale_after or not self._latest.active,
        )

    @staticmethod
    def is_browser(application_name: str) -> bool:
        return application_name in BROWSER_APPLICATIONS

    def classify(
        self,
        application_name: str,
        now: float | None = None,
    ) -> ClassifiedActivity:
        """Classify the foreground application, using the domain when relevant.

        For a non-browser the domain is irrelevant and ignored entirely: a stale
        YouTube report must not colour time spent in Photoshop.
        """
        now = time.monotonic() if now is None else now

        if not self.is_browser(application_name):
            return self._engine.classify(application_name)

        state = self._state(now)
        if not state.usable:
            # A browser with no current domain: the extension is missing, asleep,
            # or the tab is a browser-internal page. The browser is still tracked
            # as an application - resolution is lost, not function.
            return self._engine.classify(application_name)

        return self._engine.classify(application_name, domain=state.domain)

    def current_domain(self, now: float | None = None) -> str | None:
        state = self._state(time.monotonic() if now is None else now)
        return state.domain if state.usable else None
