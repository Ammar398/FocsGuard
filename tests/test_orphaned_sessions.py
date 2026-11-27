"""Sessions left open by a killed agent, and data that predates an account move.

Found by a real test run: after connecting a computer, the portal showed a
three-month-old session as "Live" with 0 s everywhere. Its agent had been
killed, so it never got an end time - and with no end time it has no totals
and looks like it is still running. The same run also uploaded sessions that
were recorded before the computer was connected to the new account.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from focusguard_agent.local_store.database import LocalStore
from focusguard_shared.constants import SyncStatus

START = datetime(2026, 9, 1, 12, 16, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    instance = LocalStore(tmp_path / "agent.db")
    yield instance
    instance.close()


def _killed_session(store: LocalStore, seconds: float = 45.0) -> int:
    session_id = store.start_session(name="Stop test", agent_version="0.1.0", started_at=START)
    store.record_score(
        session_id=session_id,
        recorded_at=START + timedelta(seconds=seconds),
        visual_attention_score=90.0,
        digital_productivity_score=None,
        combined_focus_score=88.0,
        active_events=[],
    )
    return session_id


def test_a_session_a_killed_agent_left_open_is_closed_at_its_last_record(store):
    session_id = _killed_session(store, seconds=45)

    assert store.close_orphaned_sessions() == 1

    row = store.get_session(session_id)
    assert row["ended_at"] == (START + timedelta(seconds=45)).isoformat()
    assert row["sync_status"] == SyncStatus.PENDING.value, "re-queued so its totals upload"


def test_the_running_session_is_left_alone(store):
    running = _killed_session(store)
    assert store.close_orphaned_sessions(keep_open=running) == 0
    assert store.get_session(running)["ended_at"] is None


def test_a_session_with_no_records_ends_where_it_started(store):
    session_id = store.start_session(name="Empty", agent_version="0.1.0", started_at=START)
    store.close_orphaned_sessions()
    assert store.get_session(session_id)["ended_at"] == START.isoformat()


def test_moving_accounts_keeps_unsent_data_on_the_computer(store):
    old = _killed_session(store)
    store.close_orphaned_sessions()

    held = store.keep_unsynced_local()

    assert held == 1
    assert store.get_session(old)["sync_status"] == SyncStatus.LOCAL_ONLY.value
    assert store.sessions_with_pending() == [], "nothing from before the move is uploaded"

    # A session recorded after the move uploads normally.
    new = store.start_session(name="Mine", agent_version="0.1.0")
    assert [row["id"] for row in store.sessions_with_pending()] == [new]


def test_closing_orphans_never_requeues_data_kept_local(store):
    old = _killed_session(store)
    store.keep_unsynced_local()

    store.close_orphaned_sessions()

    row = store.get_session(old)
    assert row["ended_at"] is not None
    assert row["sync_status"] == SyncStatus.LOCAL_ONLY.value
