"""Batched upload from the local queue to the backend (spec sections 33, 37).

THE ORDER OF OPERATIONS IS THE WHOLE DESIGN
-------------------------------------------
Upload first, mark synced **only after** the server confirms. Never the reverse.

If the agent marked rows synced before the response arrived and the connection
then dropped, those events would be gone forever - the local queue would no
longer offer them and the server never received them. Doing it in this order
means the failure mode is a **duplicate send**, not a loss. The server deduplicates
on `event_uuid`, so a duplicate send costs nothing.

That is the whole reason every event carries a client-generated uuid (section 37):
it lets the agent be careless about retrying and still be exactly correct.

WHAT GETS SENT WITH WHAT
------------------------
Events belong to a session, and the server needs the session before it can store
them. So a batch is built **per session**: its session envelope plus that
session's pending rows. A single upload never mixes two sessions, which also
keeps a partial failure from stranding half a session's events.

A finished session carries its totals. Those are computed from the stored rows by
`reporting/session_report.py` rather than tracked separately, so the numbers on
the dashboard are the same numbers the local report prints - there is no second
implementation to disagree.

WHAT IS NEVER SENT
------------------
Frames, screenshots, window contents, typed input, URLs. Every value below is
read from a database column, and the local schema has nowhere to hold any of
those in the first place (section 33). `tests/test_privacy_imports.py` keeps it
that way.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from focusguard_agent.local_store.database import LocalStore
from focusguard_agent.reporting.session_report import build_session_report
from focusguard_agent.synchronization.client import BackendClient, UploadResult
from focusguard_shared.utilities import get_logger

logger = get_logger("agent.sync.uploader")

@dataclass
class SyncSummary:
    """What one `sync_once()` pass did."""

    sessions_uploaded: int = 0
    events_accepted: int = 0
    events_duplicated: int = 0
    failures: int = 0

    #: Set when the backend refused the credential. The caller should stop
    #: retrying and tell the user, rather than looping on a permanent failure.
    blocked: bool = False
    blocked_reason: str = ""

    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failures == 0 and not self.blocked


class Uploader:
    """Drains the local queue into the backend."""

    def __init__(
        self,
        store: LocalStore,
        client: BackendClient,
        device_uuid: str,
        agent_version: str = "0.1.0",
        batch_size: int = 50,
    ) -> None:
        self._store = store
        self._client = client
        self._device_uuid = device_uuid
        self._agent_version = agent_version
        self._batch_size = batch_size

    def sync_once(self) -> SyncSummary:
        """Upload everything currently pending, one session at a time."""
        summary = SyncSummary()

        for session_row in self._store.sessions_with_pending():
            if summary.blocked:
                # A refused credential will refuse the next session too. Stopping
                # here keeps one misconfiguration from becoming a burst of
                # failing requests.
                break
            self._sync_session(session_row, summary)

        if summary.sessions_uploaded or summary.failures:
            logger.info(
                "Sync: %d session(s), %d event(s) accepted, %d duplicate(s), %d failure(s)",
                summary.sessions_uploaded,
                summary.events_accepted,
                summary.events_duplicated,
                summary.failures,
            )
        return summary

    def _sync_session(self, session_row: sqlite3.Row, summary: SyncSummary) -> None:
        session_id = session_row["id"]
        pending = self._store.pending_for_session(session_id, limit=self._batch_size)

        payload = self._build_batch(session_row, pending)
        result = self._client.upload_batch(payload)

        if not result.ok:
            summary.failures += 1
            summary.errors.append(result.detail)
            if not result.retryable:
                summary.blocked = True
                summary.blocked_reason = result.detail
            # Nothing is marked synced, so every row goes again next pass.
            return

        # Confirmed stored. Only now is it safe to stop offering these rows.
        for table, rows in pending.items():
            if rows:
                self._store.mark_synced(table, [row["id"] for row in rows])

        if session_row["ended_at"]:
            self._store.mark_synced("sessions", [session_id])

        summary.sessions_uploaded += 1
        summary.events_accepted += result.total_accepted
        summary.events_duplicated += result.total_duplicates

    # -- building the payload ----------------------------------------------

    def _build_batch(
        self, session_row: sqlite3.Row, pending: dict[str, list[sqlite3.Row]]
    ) -> dict:
        return {
            "device_uuid": self._device_uuid,
            "agent_version": self._agent_version,
            "session": self._session_envelope(session_row),
            "vision_events": [
                _vision_event(row) for row in pending.get("vision_events", [])
            ],
            "activity_events": [
                _activity_event(row) for row in pending.get("activity_events", [])
            ],
            "focus_scores": [_score(row) for row in pending.get("focus_scores", [])],
        }

    def _session_envelope(self, row: sqlite3.Row) -> dict:
        envelope: dict = {
            "session_uuid": row["uuid"],
            "name": row["name"],
            "started_at": row["started_at"],
            "task_name": row["task_name"],
            "task_category": row["task_category"],
        }

        if not row["ended_at"]:
            # Still running. Totals would be wrong and would have to be corrected
            # later, so they are simply omitted until the session is complete.
            return envelope

        envelope["ended_at"] = row["ended_at"]

        report = build_session_report(self._store, row["id"])
        if report is not None:
            envelope["duration_seconds"] = report.duration_seconds
            envelope["focused_seconds"] = report.focused_seconds
            envelope["distracted_seconds"] = report.distracted_seconds
            envelope["average_focus_score"] = report.average_focus_score
            envelope["longest_focus_block_seconds"] = (
                report.longest_focus_block_seconds
            )

        return envelope


def _vision_event(row: sqlite3.Row) -> dict:
    return {
        "event_uuid": row["uuid"],
        "event_type": row["event_type"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_seconds": row["duration_seconds"],
        "confidence": row["confidence"],
        "metadata": _decode_json(row["metadata"]),
    }


def _activity_event(row: sqlite3.Row) -> dict:
    return {
        "event_uuid": row["uuid"],
        "application_name": row["application_name"],
        "process_name": row["process_name"],
        "window_title": row["window_title"],
        "domain": row["domain"],
        "path_prefix": row["path_prefix"],
        "category": row["category"],
        "classification": row["classification"],
        "rule_source": row["rule_source"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "duration_seconds": row["duration_seconds"],
    }


def _score(row: sqlite3.Row) -> dict:
    return {
        "recorded_at": row["recorded_at"],
        "visual_attention_score": row["visual_attention_score"],
        "digital_productivity_score": row["digital_productivity_score"],
        "combined_focus_score": row["combined_focus_score"],
        "active_events": _decode_json(row["active_events"]),
    }


def _decode_json(value: str | None):
    """Stored JSON columns come back as text.

    A malformed value is dropped rather than failing the whole batch: one bad
    metadata blob must not strand a session's real events.
    """
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None


__all__ = ["SyncSummary", "UploadResult", "Uploader"]
