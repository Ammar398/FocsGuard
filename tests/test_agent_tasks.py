"""Agent support for assigned tasks and liveness (Milestone E).

* a store written by an older agent is upgraded in place, queue intact;
* a session started against a task uploads its task id, and a student's does not;
* the manager's task rules become the agent's classification rules;
* the worker heartbeats after a good pass, and never lets a heartbeat failure
  break an upload.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from focusguard_agent.activity_monitor.task_context import (
    TaskContextEngine,
    task_context_from_assignment,
)
from focusguard_agent.local_store.database import LocalStore
from focusguard_agent.synchronization.client import UploadResult
from focusguard_agent.synchronization.uploader import SyncSummary, Uploader
from focusguard_agent.synchronization.worker import SyncWorker
from focusguard_shared.constants import Classification, RuleSource, TaskCategory

START = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


class RecordingClient:
    def __init__(self) -> None:
        self.batches: list[dict] = []

    def upload_batch(self, batch: dict) -> UploadResult:
        self.batches.append(batch)
        return UploadResult(ok=True, status_code=200)


# --- local store ------------------------------------------------------------


def test_a_version_one_store_gains_task_id_without_losing_its_queue(tmp_path) -> None:
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old:
        old.executescript(
            """
            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                task_name TEXT,
                task_category TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                agent_version TEXT NOT NULL,
                sync_status TEXT NOT NULL DEFAULT 'PENDING'
            );
            INSERT INTO sessions(uuid, name, started_at, agent_version)
            VALUES ('queued-before-upgrade', 'Old session', '2026-09-01T10:00:00+00:00', '0.1.0');
            """
        )

    store = LocalStore(path)
    try:
        row = store.get_session(1)
        assert row["uuid"] == "queued-before-upgrade"
        assert row["task_id"] is None
        new_id = store.start_session(name="Task work", agent_version="0.2.0", task_id=42)
        assert store.get_session(new_id)["task_id"] == 42
    finally:
        store.close()


def test_a_task_session_uploads_its_task_id_and_a_student_session_does_not(tmp_path) -> None:
    store = LocalStore(tmp_path / "agent.db")
    try:
        client = RecordingClient()
        uploader = Uploader(store, client, device_uuid="dev")

        store.start_session(name="Student", agent_version="0.1.0", started_at=START)
        uploader.sync_once()
        store.start_session(
            name="Build API", agent_version="0.1.0", started_at=START, task_id=7
        )
        uploader.sync_once()

        envelopes = [batch["session"] for batch in client.batches]
        student = next(e for e in envelopes if e["name"] == "Student")
        task = next(e for e in envelopes if e["name"] == "Build API")
        assert "task_id" not in student
        assert task["task_id"] == 7
    finally:
        store.close()


# --- task context -----------------------------------------------------------


def test_an_assignment_becomes_a_task_context_with_the_managers_rules() -> None:
    context = task_context_from_assignment(
        {
            "id": 12,
            "title": "Create Landing Page UI",
            "category": "FRONTEND",
            "productive_rules": ["figma.com", "Visual Studio Code"],
            "neutral_rules": ["slack"],
            "distracting_rules": ["instagram.com"],
            "expected_minutes": 120,
        }
    )
    assert context.task_id == 12
    assert context.category is TaskCategory.FRONTEND
    assert context.expected_minutes == 120

    engine = TaskContextEngine(task=context)
    distracting = engine.classify("Google Chrome", domain="instagram.com")
    productive = engine.classify("Google Chrome", domain="figma.com")
    assert distracting.classification is Classification.DISTRACTING
    assert productive.classification is Classification.PRODUCTIVE
    assert productive.rule_source is RuleSource.TASK_RULE


def test_an_unknown_category_from_the_server_falls_back_to_other() -> None:
    context = task_context_from_assignment({"id": 1, "title": "x", "category": "ALCHEMY"})
    assert context.category is TaskCategory.OTHER


# --- heartbeat --------------------------------------------------------------


class StubUploader:
    def __init__(self, summary: SyncSummary) -> None:
        self.summary = summary

    def sync_once(self) -> SyncSummary:
        return self.summary


def test_the_worker_heartbeats_after_a_good_pass() -> None:
    beats: list[int] = []
    worker = SyncWorker(StubUploader(SyncSummary()), heartbeat=lambda: beats.append(1))

    worker.flush()

    assert beats == [1]


def test_no_heartbeat_when_the_backend_is_unreachable() -> None:
    beats: list[int] = []
    worker = SyncWorker(StubUploader(SyncSummary(failures=1)), heartbeat=lambda: beats.append(1))

    worker.flush()

    assert beats == []


def test_a_failing_heartbeat_never_breaks_the_upload_pass() -> None:
    def explode() -> None:
        raise RuntimeError("network fell over")

    worker = SyncWorker(
        StubUploader(SyncSummary(sessions_uploaded=1, events_accepted=4)), heartbeat=explode
    )

    summary = worker.flush()

    assert summary.events_accepted == 4
    assert worker.total_uploaded == 4
