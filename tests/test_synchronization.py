"""Agent to backend synchronisation (spec sections 33, 37).

The properties that would matter most if they broke:

* **nothing is marked synced until the server confirms it** - the reverse order
  loses data permanently the first time a connection drops;
* a retry after an ambiguous failure **adds nothing** on the server;
* a refused credential stops the loop instead of hammering the server;
* the payload has nowhere to put a frame, a screenshot, or a URL.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from focusguard_agent.local_store.database import LocalStore
from focusguard_agent.synchronization.client import UploadResult
from focusguard_agent.synchronization.credentials import (
    DeviceCredential,
    NotEnrolled,
    forget_credential,
    is_enrolled,
    load_credential,
    read_device_uuid,
    save_credential,
)
from focusguard_agent.synchronization.uploader import Uploader

START = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
DEVICE_UUID = "device-uuid-for-tests"


class FakeClient:
    """A backend that records what it was sent and answers as told."""

    def __init__(self, result: UploadResult | None = None) -> None:
        self.result = result or UploadResult(
            ok=True, status_code=200, accepted={"vision_events": 1}
        )
        self.batches: list[dict] = []

    def upload_batch(self, batch: dict) -> UploadResult:
        self.batches.append(batch)
        return self.result


@pytest.fixture
def store(tmp_path):
    store = LocalStore(tmp_path / "agent.db")
    yield store
    store.close()


def _populate(store: LocalStore, *, end: bool = False) -> int:
    """One session with one event of each kind."""
    session_id = store.start_session(
        name="Study session", agent_version="0.1.0", task_name="Phase 14"
    )

    store.record_vision_event(
        session_id=session_id,
        event_type="PHONE_VISIBLE",
        start_time=START,
        end_time=START + timedelta(seconds=12),
        duration_seconds=12.0,
        confidence=0.9,
        metadata={"detector": "yolo"},
    )
    store.record_activity_event(
        session_id=session_id,
        application_name="Visual Studio Code",
        process_name="Code.exe",
        window_title=None,
        domain=None,
        path_prefix=None,
        category="DEVELOPMENT",
        classification="PRODUCTIVE",
        rule_source="TASK_RULE",
        start_time=START,
        end_time=START + timedelta(minutes=5),
        duration_seconds=300.0,
    )
    store.record_score(
        session_id=session_id,
        recorded_at=START,
        visual_attention_score=88.0,
        digital_productivity_score=91.0,
        combined_focus_score=89.0,
        active_events=["PHONE_VISIBLE"],
    )

    if end:
        store.end_session(session_id, ended_at=START + timedelta(minutes=30))
    return session_id


def _uploader(store: LocalStore, client) -> Uploader:
    return Uploader(store, client, device_uuid=DEVICE_UUID)


# --- the credential file ----------------------------------------------------


def test_a_device_uuid_is_generated_once_and_reused(tmp_path) -> None:
    """Re-enrolling must read as the same machine, not a second one."""
    first = read_device_uuid(tmp_path)
    second = read_device_uuid(tmp_path)

    assert first == second
    assert len(first) == 36, "a uuid4"


def test_an_unenrolled_machine_says_so_clearly(tmp_path) -> None:
    assert is_enrolled(tmp_path) is False

    with pytest.raises(NotEnrolled, match="tools.enroll"):
        load_credential(tmp_path)


def test_a_credential_round_trips(tmp_path) -> None:
    credential = DeviceCredential(
        device_uuid="abc", device_secret="s3cret", backend_url="http://x", user_id=4
    )
    save_credential(credential, tmp_path)

    loaded = load_credential(tmp_path)

    assert loaded == credential
    assert loaded.headers == {"X-Device-Secret": "s3cret"}


def test_forgetting_keeps_the_device_identity(tmp_path) -> None:
    """Disconnecting a machine must not make it look like a different machine."""
    device_uuid = read_device_uuid(tmp_path)
    save_credential(
        DeviceCredential(device_uuid=device_uuid, device_secret="s", backend_url="http://x"),
        tmp_path,
    )

    assert forget_credential(tmp_path) is True

    assert is_enrolled(tmp_path) is False
    assert read_device_uuid(tmp_path) == device_uuid


def test_a_corrupt_credential_file_does_not_crash_the_agent(tmp_path) -> None:
    """The worst case must be 'please enrol again', not a crash loop."""
    (tmp_path / "device.json").write_text("{ this is not json", encoding="utf-8")

    assert is_enrolled(tmp_path) is False
    assert len(read_device_uuid(tmp_path)) == 36, "and it recovers by regenerating"


# --- the ordering guarantee -------------------------------------------------


def test_nothing_is_marked_synced_until_the_server_confirms(store) -> None:
    """The single most important property in this module.

    Marking first and uploading second would lose every event in flight the
    first time a connection dropped, with nothing left to retry from.
    """
    _populate(store)
    failing = FakeClient(UploadResult(ok=False, status_code=503, detail="down"))

    summary = _uploader(store, failing).sync_once()

    assert summary.failures == 1
    assert not summary.ok
    # Every row is still offered, so the next pass sends it again.
    assert store.pending_counts() == {
        "vision_events": 1,
        "activity_events": 1,
        "focus_scores": 1,
    }


def test_a_successful_upload_clears_the_queue(store) -> None:
    _populate(store)
    client = FakeClient()

    summary = _uploader(store, client).sync_once()

    assert summary.ok
    assert summary.sessions_uploaded == 1
    assert store.pending_counts() == {
        "vision_events": 0,
        "activity_events": 0,
        "focus_scores": 0,
    }


def test_a_second_pass_sends_nothing_when_nothing_changed(store) -> None:
    _populate(store, end=True)
    client = FakeClient()
    uploader = _uploader(store, client)

    uploader.sync_once()
    second = uploader.sync_once()

    assert second.sessions_uploaded == 0
    assert len(client.batches) == 1, "no pointless request"


def test_a_retry_resends_everything_after_a_failure(store) -> None:
    """Section 37: an agent that could not tell whether it landed simply resends."""
    _populate(store)
    client = FakeClient(UploadResult(ok=False, status_code=503, detail="down"))
    uploader = _uploader(store, client)

    uploader.sync_once()
    client.result = UploadResult(ok=True, status_code=200, accepted={"vision_events": 1})
    summary = uploader.sync_once()

    assert summary.ok
    assert len(client.batches) == 2
    assert client.batches[0] == client.batches[1], "byte for byte the same batch"


def test_a_live_session_keeps_uploading_as_it_gains_events(store) -> None:
    """A session already sent must not be treated as finished with."""
    session_id = _populate(store)
    client = FakeClient()
    uploader = _uploader(store, client)

    uploader.sync_once()

    store.record_vision_event(
        session_id=session_id,
        event_type="EYES_CLOSED",
        start_time=START + timedelta(minutes=1),
        end_time=START + timedelta(minutes=1, seconds=3),
        duration_seconds=3.0,
        confidence=0.8,
    )
    second = uploader.sync_once()

    assert second.sessions_uploaded == 1
    assert len(client.batches[1]["vision_events"]) == 1


# --- a refused credential ---------------------------------------------------


def test_a_revoked_credential_stops_the_loop(store) -> None:
    """Retrying a permanent refusal forever hides a problem needing a person."""
    _populate(store)
    _populate(store)
    client = FakeClient(
        UploadResult(ok=False, status_code=403, detail="revoked", retryable=False)
    )

    summary = _uploader(store, client).sync_once()

    assert summary.blocked
    assert summary.blocked_reason == "revoked"
    assert len(client.batches) == 1, "it stopped rather than trying the second session"


def test_a_temporary_failure_does_not_block(store) -> None:
    _populate(store)
    _populate(store)
    client = FakeClient(UploadResult(ok=False, status_code=503, detail="down"))

    summary = _uploader(store, client).sync_once()

    assert not summary.blocked
    assert summary.failures == 2, "both were attempted"


# --- what the payload contains ----------------------------------------------


def test_a_finished_session_carries_its_totals(store) -> None:
    _populate(store, end=True)
    client = FakeClient()

    _uploader(store, client).sync_once()
    envelope = client.batches[0]["session"]

    assert envelope["ended_at"] is not None
    assert "duration_seconds" in envelope
    assert "average_focus_score" in envelope


def test_a_running_session_omits_totals_rather_than_guessing(store) -> None:
    """Sending numbers that are wrong now and corrected later is worse than none."""
    _populate(store)
    client = FakeClient()

    _uploader(store, client).sync_once()
    envelope = client.batches[0]["session"]

    assert envelope.get("ended_at") is None
    assert "average_focus_score" not in envelope


def test_the_payload_has_nowhere_to_put_a_frame_or_a_url(store) -> None:
    """Section 33, proven on the real payload rather than asserted in a comment."""
    _populate(store, end=True)
    client = FakeClient()

    _uploader(store, client).sync_once()
    serialised = json.dumps(client.batches[0]).lower()

    for forbidden in ("frame", "screenshot", "image", "keystroke", "clipboard", "password"):
        assert forbidden not in serialised

    activity = client.batches[0]["activity_events"][0]
    assert "url" not in activity
    assert set(activity) <= {
        "event_uuid",
        "application_name",
        "process_name",
        "window_title",
        "domain",
        "path_prefix",
        "category",
        "classification",
        "rule_source",
        "start_time",
        "end_time",
        "duration_seconds",
    }


def test_the_batch_names_the_authenticated_device(store) -> None:
    _populate(store)
    client = FakeClient()

    _uploader(store, client).sync_once()

    assert client.batches[0]["device_uuid"] == DEVICE_UUID


def test_metadata_survives_the_round_trip_as_json(store) -> None:
    _populate(store)
    client = FakeClient()

    _uploader(store, client).sync_once()

    assert client.batches[0]["vision_events"][0]["metadata"] == {"detector": "yolo"}
    assert client.batches[0]["focus_scores"][0]["active_events"] == ["PHONE_VISIBLE"]


def test_one_batch_never_mixes_two_sessions(store) -> None:
    """A partial failure must not strand half of one session's events."""
    _populate(store)
    _populate(store)
    client = FakeClient()

    _uploader(store, client).sync_once()

    assert len(client.batches) == 2
    uuids = {batch["session"]["session_uuid"] for batch in client.batches}
    assert len(uuids) == 2


def test_the_batch_size_caps_each_table_separately(store) -> None:
    """Thousands of score samples must not starve out the vision events."""
    session_id = store.start_session(name="Long session", agent_version="0.1.0")
    for index in range(10):
        store.record_score(
            session_id=session_id,
            recorded_at=START + timedelta(seconds=index),
            visual_attention_score=80.0,
            combined_focus_score=80.0,
        )
    store.record_vision_event(
        session_id=session_id,
        event_type="EYES_CLOSED",
        start_time=START,
        end_time=START + timedelta(seconds=2),
        duration_seconds=2.0,
        confidence=0.7,
    )

    client = FakeClient()
    Uploader(store, client, device_uuid=DEVICE_UUID, batch_size=3).sync_once()

    batch = client.batches[0]
    assert len(batch["focus_scores"]) == 3
    assert len(batch["vision_events"]) == 1, "not crowded out by the scores"


# --- the background worker --------------------------------------------------


class FakeUploader:
    """Returns prepared summaries and counts how often it was asked."""

    def __init__(self, *summaries) -> None:
        from focusguard_agent.synchronization.uploader import SyncSummary as S

        self._summaries = list(summaries) or [S(sessions_uploaded=1, events_accepted=3)]
        self.calls = 0

    def sync_once(self):
        from focusguard_agent.synchronization.uploader import SyncSummary as S

        self.calls += 1
        if self._summaries:
            return self._summaries.pop(0)
        return S()


def _worker(uploader, **kwargs):
    from focusguard_agent.synchronization.worker import SyncWorker

    return SyncWorker(uploader, **kwargs)


def test_a_flush_uploads_on_the_callers_thread() -> None:
    """Shutdown wants to block: the user is already waiting for the agent."""
    from focusguard_agent.synchronization.uploader import SyncSummary

    uploader = FakeUploader(SyncSummary(sessions_uploaded=1, events_accepted=7))
    worker = _worker(uploader)

    summary = worker.flush()

    assert uploader.calls == 1
    assert summary.events_accepted == 7
    assert worker.total_uploaded == 7


def test_a_failure_widens_the_backoff_and_success_resets_it() -> None:
    from focusguard_agent.synchronization.uploader import SyncSummary

    worker = _worker(
        FakeUploader(
            SyncSummary(failures=1),
            SyncSummary(failures=1),
            SyncSummary(sessions_uploaded=1),
        ),
        interval_seconds=30.0,
    )

    worker.flush()
    assert worker._backoff == 60.0  # noqa: SLF001
    worker.flush()
    assert worker._backoff == 120.0  # noqa: SLF001

    worker.flush()
    assert worker._backoff == 30.0, "recovery is immediate, not gradual"  # noqa: SLF001


def test_the_backoff_is_capped() -> None:
    from focusguard_agent.synchronization.uploader import SyncSummary

    worker = _worker(
        FakeUploader(*[SyncSummary(failures=1) for _ in range(20)]),
        interval_seconds=30.0,
        max_backoff_seconds=300.0,
    )

    for _ in range(20):
        worker.flush()

    assert worker._backoff == 300.0  # noqa: SLF001


def test_a_revoked_credential_stops_the_worker_for_good() -> None:
    """Retrying a permanent refusal forever would hide a problem needing a person."""
    from focusguard_agent.synchronization.uploader import SyncSummary

    uploader = FakeUploader(SyncSummary(blocked=True, blocked_reason="revoked"))
    worker = _worker(uploader)

    worker.flush()
    assert worker.blocked

    worker.flush()
    assert uploader.calls == 1, "it did not try again"
    assert "revoked" in worker.status_line()


def test_an_unexpected_error_does_not_take_the_agent_down() -> None:
    """Whatever went wrong, the rows are still pending and monitoring continues."""

    class Exploding:
        def sync_once(self):
            raise RuntimeError("something unforeseen")

    worker = _worker(Exploding())

    summary = worker.flush()

    assert summary.failures == 1
    assert not worker.blocked, "an unknown error is temporary, not permanent"
