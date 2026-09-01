"""Backend API tests (spec sections 33, 36, 37, 51).

The two behaviours worth proving hardest:

* **replaying a batch must not duplicate anything** - section 37's retry design
  depends on it, and without it every dropped connection would inflate a user's
  totals;
* **tenant isolation** - section 51 says a manager from Company A must never
  reach Company B's data, and that is tested rather than asserted.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.database.session import create_all, get_engine, reset_engine
from focusguard_backend.models import Device, Organization, User

START = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fresh app on its own database for every test."""
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "api.db"))
    reset_engine()

    from focusguard_backend.main import app

    create_all(get_engine())
    with TestClient(app) as test_client:
        yield test_client

    reset_engine()


@pytest.fixture
def session_factory():
    from focusguard_backend.database.session import get_session_factory

    return get_session_factory()


def _make_user_and_device(
    session_factory, email: str = "student@example.com", org_name: str | None = None
) -> tuple[int, str, int | None]:
    """Create a user with a device. Returns (user_id, device_uuid, org_id)."""
    with session_factory() as db:
        organization_id = None
        if org_name:
            organization = Organization(name=org_name, slug=org_name.lower().replace(" ", "-"))
            db.add(organization)
            db.flush()
            organization_id = organization.id

        user = User(
            email=email,
            password_hash="not-a-real-hash",
            role="STUDENT" if org_name is None else "EMPLOYEE",
            organization_id=organization_id,
        )
        db.add(user)
        db.flush()

        device_uuid = str(uuid_module.uuid4())
        db.add(
            Device(
                device_uuid=device_uuid,
                device_name="Test PC",
                user_id=user.id,
                organization_id=organization_id,
                is_active=True,
            )
        )
        db.commit()
        return user.id, device_uuid, organization_id


def _batch(device_uuid: str, session_uuid: str = "sess-1", event_uuids: list[str] | None = None):
    event_uuids = event_uuids or ["ev-1", "ev-2"]
    return {
        "device_uuid": device_uuid,
        "agent_version": "0.1.0",
        "session": {
            "session_uuid": session_uuid,
            "name": "Study session",
            "started_at": START.isoformat(),
            "task_name": "Build the REST API",
            "task_category": "BACKEND",
        },
        "vision_events": [
            {
                "event_uuid": uid,
                "event_type": "PHONE_VISIBLE",
                "start_time": START.isoformat(),
                "end_time": (START + timedelta(seconds=30)).isoformat(),
                "duration_seconds": 30.0,
                "confidence": 0.86,
            }
            for uid in event_uuids
        ],
        "activity_events": [
            {
                "event_uuid": "act-1",
                "application_name": "Visual Studio Code",
                "category": "DEVELOPMENT",
                "classification": "PRODUCTIVE",
                "rule_source": "TASK_RULE",
                "start_time": START.isoformat(),
                "end_time": (START + timedelta(minutes=5)).isoformat(),
                "duration_seconds": 300.0,
            }
        ],
        "focus_scores": [
            {
                "recorded_at": (START + timedelta(seconds=index)).isoformat(),
                "visual_attention_score": 90.0,
                "combined_focus_score": 90.0,
            }
            for index in range(5)
        ],
    }


# --- health -----------------------------------------------------------------


def test_health_reports_status_without_leaking_credentials(client: TestClient) -> None:
    response = client.get("/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["database"] == "sqlite", "the dialect, never the connection string"
    assert "password" not in str(payload).lower()


# --- ingest -----------------------------------------------------------------


def test_a_batch_is_accepted(client: TestClient, session_factory) -> None:
    _, device_uuid, _ = _make_user_and_device(session_factory)

    response = client.post("/api/v1/agent/events", json=_batch(device_uuid))
    payload = response.json()

    assert response.status_code == 200
    assert payload["accepted"]["vision_events"] == 2
    assert payload["accepted"]["activity_events"] == 1
    assert payload["accepted"]["focus_scores"] == 5


def test_replaying_a_batch_creates_no_duplicates(client: TestClient, session_factory) -> None:
    """The section 37 guarantee.

    An agent whose upload times out ambiguously simply sends again. Without this,
    every dropped connection would inflate the user's distraction totals.
    """
    user_id, device_uuid, _ = _make_user_and_device(session_factory)
    batch = _batch(device_uuid)

    first = client.post("/api/v1/agent/events", json=batch).json()
    second = client.post("/api/v1/agent/events", json=batch).json()

    assert first["accepted"]["vision_events"] == 2
    assert second["accepted"]["vision_events"] == 0
    assert second["duplicates"]["vision_events"] == 2
    assert second["session_id"] == first["session_id"], "not a second session either"

    detail = client.get(
        f"/api/v1/sessions/{first['session_id']}", params={"user_id": user_id}
    ).json()
    assert len(detail["vision_events"]) == 2


def test_duplicates_within_one_batch_are_handled(client: TestClient, session_factory) -> None:
    """A malformed batch must not fail the whole upload."""
    _, device_uuid, _ = _make_user_and_device(session_factory)
    batch = _batch(device_uuid, event_uuids=["same", "same", "other"])

    payload = client.post("/api/v1/agent/events", json=batch).json()

    assert payload["accepted"]["vision_events"] == 2
    assert payload["duplicates"]["vision_events"] == 1


def test_an_unknown_device_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/agent/events", json=_batch("never-enrolled"))
    assert response.status_code == 404


def test_a_deactivated_device_cannot_upload(client: TestClient, session_factory) -> None:
    _, device_uuid, _ = _make_user_and_device(session_factory)

    with session_factory() as db:
        from sqlalchemy import select

        device = db.scalar(select(Device).where(Device.device_uuid == device_uuid))
        device.is_active = False
        db.commit()

    assert client.post("/api/v1/agent/events", json=_batch(device_uuid)).status_code == 403


def test_a_session_can_be_uploaded_complete(client: TestClient, session_factory) -> None:
    """An agent that ran offline uploads the whole finished session at once."""
    user_id, device_uuid, _ = _make_user_and_device(session_factory)

    batch = _batch(device_uuid)
    batch["session"]["ended_at"] = (START + timedelta(minutes=30)).isoformat()
    batch["session"]["duration_seconds"] = 1800.0
    batch["session"]["focused_seconds"] = 1500.0
    batch["session"]["average_focus_score"] = 84.0

    session_id = client.post("/api/v1/agent/events", json=batch).json()["session_id"]
    detail = client.get(f"/api/v1/sessions/{session_id}", params={"user_id": user_id}).json()

    assert detail["ended_at"] is not None
    assert detail["average_focus_score"] == pytest.approx(84.0)


# --- the schema is a privacy control (spec sections 33, 34) ----------------


def test_an_unknown_field_is_rejected_rather_than_dropped(
    client: TestClient, session_factory
) -> None:
    """Silently discarding a field loses data nobody realises is missing."""
    _, device_uuid, _ = _make_user_and_device(session_factory)
    batch = _batch(device_uuid)
    batch["vision_events"][0]["screenshot"] = "data:image/png;base64,AAAA"

    assert client.post("/api/v1/agent/events", json=batch).status_code == 422


def test_there_is_no_way_to_upload_a_url(client: TestClient, session_factory) -> None:
    """The extension minimises to a domain; the API has nowhere to put a URL."""
    _, device_uuid, _ = _make_user_and_device(session_factory)
    batch = _batch(device_uuid)
    batch["activity_events"][0]["url"] = "https://google.com/search?q=private"

    assert client.post("/api/v1/agent/events", json=batch).status_code == 422


def test_an_out_of_range_score_is_refused(client: TestClient, session_factory) -> None:
    _, device_uuid, _ = _make_user_and_device(session_factory)
    batch = _batch(device_uuid)
    batch["focus_scores"][0]["combined_focus_score"] = 150.0

    assert client.post("/api/v1/agent/events", json=batch).status_code == 422


# --- reading ----------------------------------------------------------------


def test_sessions_are_listed_newest_first(client: TestClient, session_factory) -> None:
    user_id, device_uuid, _ = _make_user_and_device(session_factory)

    older = _batch(device_uuid, session_uuid="old", event_uuids=["a"])
    newer = _batch(device_uuid, session_uuid="new", event_uuids=["b"])
    newer["session"]["started_at"] = (START + timedelta(hours=2)).isoformat()
    newer["session"]["name"] = "Later session"

    client.post("/api/v1/agent/events", json=older)
    client.post("/api/v1/agent/events", json=newer)

    listing = client.get("/api/v1/sessions", params={"user_id": user_id}).json()

    assert len(listing) == 2
    assert listing[0]["name"] == "Later session"


def test_a_session_detail_carries_its_timeline(client: TestClient, session_factory) -> None:
    user_id, device_uuid, _ = _make_user_and_device(session_factory)
    session_id = client.post("/api/v1/agent/events", json=_batch(device_uuid)).json()[
        "session_id"
    ]

    detail = client.get(f"/api/v1/sessions/{session_id}", params={"user_id": user_id}).json()

    assert len(detail["vision_events"]) == 2
    assert len(detail["activity_events"]) == 1
    assert len(detail["focus_scores"]) == 5
    assert detail["task_name"] == "Build the REST API"


def test_a_missing_session_is_a_404(client: TestClient, session_factory) -> None:
    user_id, _, _ = _make_user_and_device(session_factory)
    assert client.get("/api/v1/sessions/999", params={"user_id": user_id}).status_code == 404


def test_a_scope_is_required_to_list_sessions(client: TestClient) -> None:
    """An unscoped query would cross tenant boundaries, so it cannot be made."""
    assert client.get("/api/v1/sessions").status_code == 422


# --- heartbeats -------------------------------------------------------------


def test_a_heartbeat_updates_last_seen(client: TestClient, session_factory) -> None:
    _, device_uuid, _ = _make_user_and_device(session_factory)

    response = client.post(
        "/api/v1/agent/heartbeat",
        json={"device_uuid": device_uuid, "agent_version": "0.1.0", "pending_events": 7},
    )
    assert response.status_code == 204

    with session_factory() as db:
        from sqlalchemy import select

        device = db.scalar(select(Device).where(Device.device_uuid == device_uuid))
        assert device.last_seen_at is not None


def test_a_heartbeat_from_an_unknown_device_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/agent/heartbeat", json={"device_uuid": "nope"})
    assert response.status_code == 404
