"""The background thread that uploads while the agent keeps watching.

WHY A THREAD AND NOT A CALL IN THE LOOP
---------------------------------------
The agent's tick runs about a hundred times a second and has a frame budget
measured in milliseconds. One HTTP request can take fifteen seconds when a
network is failing badly - which is exactly when it is least acceptable to stop
looking at the camera. Monitoring must not pause because a server is slow.

So uploads run here, on their own thread, and the capture loop never waits.

GIL-BOUND WORK IS NOT A PROBLEM HERE
------------------------------------
This thread is almost entirely blocked on a socket, and `requests` releases the
GIL while it waits. On a 4-core, 4-thread i5-6500 that matters: a busy thread
would be stealing from the vision pipeline, and a sleeping one is free.

BACKOFF IS EXPONENTIAL, AND CAPPED
----------------------------------
A backend that is down stays down for a while. Retrying every thirty seconds for
an hour is a thousand pointless requests and a log full of noise, so each
consecutive failure doubles the wait, up to a ceiling. A success resets it
immediately, so recovery is fast even after a long outage.

A **permanent** refusal - a revoked or wrong credential - stops the thread
entirely instead of backing off. Retrying cannot fix it, and quietly looping
forever would hide a problem that needs a person.
"""

from __future__ import annotations

import threading

from focusguard_agent.synchronization.uploader import SyncSummary, Uploader
from focusguard_shared.utilities import get_logger

logger = get_logger("agent.sync.worker")


class SyncWorker:
    """Runs `Uploader.sync_once()` on a schedule, off the capture thread."""

    def __init__(
        self,
        uploader: Uploader,
        interval_seconds: float = 30.0,
        max_backoff_seconds: float = 600.0,
    ) -> None:
        self._uploader = uploader
        self._interval = max(1.0, interval_seconds)
        self._max_backoff = max_backoff_seconds

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._backoff = self._interval
        self._lock = threading.Lock()

        #: Read by the agent for its status line and closing summary.
        self.last_summary: SyncSummary | None = None
        self.total_uploaded = 0
        self.blocked = False
        self.blocked_reason = ""

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        if self._thread is not None:
            return True

        self._stop.clear()
        # A daemon thread: if the agent dies unexpectedly this must not keep the
        # process alive. Nothing is lost, because nothing is marked synced until
        # the server confirms it.
        self._thread = threading.Thread(
            target=self._run, name="focusguard-sync", daemon=True
        )
        self._thread.start()
        logger.info("Sync worker started (every %.0fs)", self._interval)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to finish and wait briefly for it."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        # The first pass waits, so start-up is not competing with MediaPipe and
        # YOLO loading for the same few cores.
        while not self._stop.wait(self._backoff):
            if self.blocked:
                return
            self._pass()

    # -- one pass ----------------------------------------------------------

    def _pass(self) -> SyncSummary:
        try:
            summary = self._uploader.sync_once()
        except Exception:  # noqa: BLE001
            # An upload must never take the agent down. Whatever went wrong, the
            # rows are still pending and the next pass will try again.
            logger.warning("Sync pass failed unexpectedly", exc_info=True)
            self._widen_backoff()
            return SyncSummary(failures=1)

        with self._lock:
            self.last_summary = summary
            self.total_uploaded += summary.events_accepted

            if summary.blocked:
                self.blocked = True
                self.blocked_reason = summary.blocked_reason
                logger.error(
                    "Uploads stopped: %s. Re-enrol this machine to resume.",
                    summary.blocked_reason,
                )
            elif summary.failures:
                self._widen_backoff()
            else:
                # Recovery is immediate rather than gradual: a server that just
                # answered is not a server to keep treating as down.
                self._backoff = self._interval

        return summary

    def _widen_backoff(self) -> None:
        self._backoff = min(self._backoff * 2, self._max_backoff)
        logger.info("Backing off; next sync attempt in %.0fs", self._backoff)

    def flush(self) -> SyncSummary:
        """Run one pass on the caller's thread.

        Used at shutdown, where blocking is exactly what is wanted: the user is
        waiting for the agent to finish, and a session that uploads on the way
        out is far friendlier than one that waits for the next launch.
        """
        if self.blocked:
            return SyncSummary(blocked=True, blocked_reason=self.blocked_reason)
        return self._pass()

    # -- reporting ---------------------------------------------------------

    def status_line(self) -> str:
        """One line for the agent's closing summary."""
        if self.blocked:
            return f"uploads stopped ({self.blocked_reason})"
        if self.last_summary is None:
            return "no upload attempted yet"
        if self.last_summary.failures:
            return f"offline, {self.total_uploaded} event(s) uploaded so far"
        return f"{self.total_uploaded} event(s) uploaded"
