"""Local store tests (spec sections 37, 45).

The two properties that matter: a retried upload must never duplicate a row, and
the database must not grow forever on a 128 GB SSD.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime, timedelta

import pytest

from focusguard_agent.local_store.database import LocalStore, SessionClock

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    instance = LocalStore(tmp_path / "test.db")
    yield instance
    instance.close()


def _session(store: LocalStore) -> int:
    return store.start_session(name="Test session", agent_version="0.1.0")


def _add_vision(store: LocalStore, session_id: int, **overrides) -> bool:
    payload = {
        "event_type": "PHONE_VISIBLE",
        "start_time": NOW,
        "end_time": NOW + timedelta(seconds=30),
        "duration_seconds": 30.0,
        "confidence": 0.85,
    }
    payload.update(overrides)
    return store.record_vision_event(session_id=session_id, **payload)


# --- sessions ---------------------------------------------------------------


def test_a_session_can_be_started_and_ended(store: LocalStore) -> None:
    session_id = _session(store)
    row = store.get_session(session_id)

    assert row is not None
    assert row["name"] == "Test session"
    assert row["ended_at"] is None

    store.end_session(session_id, ended_at=NOW)
    assert store.get_session(session_id)["ended_at"] is not None


def test_sessions_record_their_task(store: LocalStore) -> None:
    session_id = store.start_session(
        name="Work", agent_version="0.1.0", task_name="Build API", task_category="BACKEND"
    )
    row = store.get_session(session_id)

    assert row["task_name"] == "Build API"
    assert row["task_category"] == "BACKEND"


def test_recent_sessions_are_newest_first(store: LocalStore) -> None:
    store.start_session(name="Older", agent_version="0.1.0", started_at=NOW)
    store.start_session(
        name="Newer", agent_version="0.1.0", started_at=NOW + timedelta(hours=1)
    )

    assert store.recent_sessions()[0]["name"] == "Newer"


# --- idempotency: the section 37 guarantee ----------------------------------


def test_the_same_event_uuid_is_only_stored_once(store: LocalStore) -> None:
    """The whole retry design.

    An upload that times out ambiguously can simply be sent again; the duplicate
    is rejected by the database, not by hopeful application logic.
    """
    session_id = _session(store)
    event_uuid = str(uuid_module.uuid4())

    assert _add_vision(store, session_id, event_uuid=event_uuid) is True
    assert _add_vision(store, session_id, event_uuid=event_uuid) is False

    assert len(store.vision_events(session_id)) == 1


def test_different_events_are_all_stored(store: LocalStore) -> None:
    session_id = _session(store)
    for _ in range(5):
        _add_vision(store, session_id)

    assert len(store.vision_events(session_id)) == 5


def test_activity_events_are_idempotent_too(store: LocalStore) -> None:
    session_id = _session(store)
    event_uuid = str(uuid_module.uuid4())

    def add() -> bool:
        return store.record_activity_event(
            session_id=session_id,
            application_name="Visual Studio Code",
            category="DEVELOPMENT",
            classification="PRODUCTIVE",
            rule_source="CATEGORY_DEFAULT",
            start_time=NOW,
            end_time=NOW + timedelta(minutes=5),
            duration_seconds=300.0,
            event_uuid=event_uuid,
        )

    assert add() is True
    assert add() is False
    assert len(store.activity_events(session_id)) == 1


# --- what is stored ---------------------------------------------------------


def test_a_vision_event_round_trips(store: LocalStore) -> None:
    session_id = _session(store)
    _add_vision(store, session_id, confidence=0.77)

    row = store.vision_events(session_id)[0]
    assert row["event_type"] == "PHONE_VISIBLE"
    assert row["duration_seconds"] == pytest.approx(30.0)
    assert row["confidence"] == pytest.approx(0.77)


def test_domains_are_stored_without_query_strings(store: LocalStore) -> None:
    """The privacy guarantee reaching storage."""
    session_id = _session(store)
    store.record_activity_event(
        session_id=session_id,
        application_name="Google Chrome",
        category="DEVELOPMENT",
        classification="PRODUCTIVE",
        rule_source="CATEGORY_DEFAULT",
        start_time=NOW,
        end_time=NOW + timedelta(minutes=2),
        duration_seconds=120.0,
        domain="github.com",
        path_prefix="/user",
    )

    row = store.activity_events(session_id)[0]
    assert row["domain"] == "github.com"
    assert "?" not in (row["path_prefix"] or "")


def test_scores_are_stored_with_both_axes(store: LocalStore) -> None:
    session_id = _session(store)
    store.record_score(
        session_id=session_id,
        recorded_at=NOW,
        visual_attention_score=91.0,
        combined_focus_score=63.0,
        digital_productivity_score=35.0,
        active_events=["GAZE_AWAY"],
    )

    row = store.scores(session_id)[0]
    assert row["visual_attention_score"] == pytest.approx(91.0)
    assert row["digital_productivity_score"] == pytest.approx(35.0)


def test_a_missing_productivity_score_is_stored_as_null(store: LocalStore) -> None:
    """None must not become 0, which would read as terrible productivity."""
    session_id = _session(store)
    store.record_score(
        session_id=session_id,
        recorded_at=NOW,
        visual_attention_score=95.0,
        combined_focus_score=95.0,
        digital_productivity_score=None,
    )

    assert store.scores(session_id)[0]["digital_productivity_score"] is None


# --- sync queue -------------------------------------------------------------


def test_new_events_are_pending_upload(store: LocalStore) -> None:
    session_id = _session(store)
    _add_vision(store, session_id)

    assert store.pending_counts()["vision_events"] == 1


def test_synced_events_leave_the_queue(store: LocalStore) -> None:
    session_id = _session(store)
    _add_vision(store, session_id)

    batch = store.take_pending("vision_events")
    store.mark_synced("vision_events", [row["id"] for row in batch])

    assert store.pending_counts()["vision_events"] == 0


def test_pending_batches_are_limited(store: LocalStore) -> None:
    session_id = _session(store)
    for _ in range(10):
        _add_vision(store, session_id)

    assert len(store.take_pending("vision_events", limit=4)) == 4


def test_an_unknown_table_is_refused(store: LocalStore) -> None:
    with pytest.raises(ValueError):
        store.take_pending("users")


# --- retention (spec section 45) -------------------------------------------


def test_old_events_are_summarised_then_deleted(store: LocalStore) -> None:
    """On a 128 GB SSD this is the difference between a database that grows
    forever and one that does not."""
    session_id = _session(store)
    old = NOW - timedelta(days=60)

    for _ in range(20):
        _add_vision(store, session_id, start_time=old, end_time=old + timedelta(seconds=10))

    removed = store.roll_up_and_prune(retention_days=30, now=NOW)

    assert removed >= 20
    assert store.vision_events(session_id) == []


def test_recent_events_survive_pruning(store: LocalStore) -> None:
    session_id = _session(store)
    _add_vision(store, session_id, start_time=NOW, end_time=NOW + timedelta(seconds=5))

    store.roll_up_and_prune(retention_days=30, now=NOW)

    assert len(store.vision_events(session_id)) == 1


def test_a_summary_is_kept_after_the_raw_rows_go(store: LocalStore) -> None:
    """The totals survive so long-term reports still work (section 45)."""
    session_id = _session(store)
    old = NOW - timedelta(days=45)
    _add_vision(store, session_id, start_time=old, end_time=old + timedelta(seconds=30))

    store.roll_up_and_prune(retention_days=30, now=NOW)

    with store._cursor() as cursor:  # noqa: SLF001 - inspecting storage is the point
        cursor.execute("SELECT * FROM daily_summaries")
        summaries = cursor.fetchall()

    assert len(summaries) == 1
    assert "PHONE_VISIBLE" in summaries[0]["event_seconds"]


def test_pruning_twice_does_not_duplicate_summaries(store: LocalStore) -> None:
    session_id = _session(store)
    old = NOW - timedelta(days=45)
    _add_vision(store, session_id, start_time=old, end_time=old + timedelta(seconds=30))

    store.roll_up_and_prune(retention_days=30, now=NOW)
    store.roll_up_and_prune(retention_days=30, now=NOW)

    with store._cursor() as cursor:  # noqa: SLF001
        cursor.execute("SELECT COUNT(*) FROM daily_summaries")
        assert cursor.fetchone()[0] == 1


# --- the clock --------------------------------------------------------------


def test_monotonic_times_convert_to_wall_clock() -> None:
    """Durations are measured monotonically because a wall clock can jump
    backwards on a time sync, which would produce negative durations."""
    clock = SessionClock(started_wall=NOW, started_monotonic=1000.0)

    assert clock.to_wall(1000.0) == NOW
    assert clock.to_wall(1060.0) == NOW + timedelta(seconds=60)


def test_the_clock_handles_a_long_session() -> None:
    clock = SessionClock(started_wall=NOW, started_monotonic=0.0)
    assert clock.to_wall(7200.0) == NOW + timedelta(hours=2)


# --- housekeeping -----------------------------------------------------------


def test_deleting_a_session_removes_its_events(store: LocalStore) -> None:
    """Foreign keys are on, so a deleted session leaves no orphans behind."""
    session_id = _session(store)
    _add_vision(store, session_id)

    with store._cursor() as cursor:  # noqa: SLF001
        cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    assert store.vision_events(session_id) == []


def test_the_database_file_is_created(store: LocalStore) -> None:
    assert store.path.exists()
    assert store.size_bytes() > 0
