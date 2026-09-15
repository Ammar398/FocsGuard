"""Reports, recommendations and export through the API (spec sections 42, 43, 51).

The recommendation *rules* are tested exhaustively in `test_recommendations.py`.
What is tested here is everything around them: that the aggregation is right,
that the window behaves, that the export is real CSV, and that none of these new
aggregating routes leaks across a tenant boundary.

Aggregating routes are the easiest place for isolation to fail, because a missing
filter produces a plausible number rather than an error.
"""

from __future__ import annotations

import csv
import io
import uuid as uuid_module
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.auth.tokens import create_access_token, hash_token
from focusguard_backend.database.session import create_all, get_engine, reset_engine
from focusguard_backend.models import Device, Organization, User

NOW = datetime.now(UTC)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "reports.db"))
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


def _make_actor(session_factory, email: str, org_name: str | None = None, role: str = "STUDENT"):
    """A user with an enrolled device. Returns (user_headers, agent_headers, org_id)."""
    secret = f"secret-{uuid_module.uuid4()}"
    with session_factory() as db:
        organization_id = None
        if org_name:
            organization = Organization(name=org_name, slug=org_name.lower())
            db.add(organization)
            db.flush()
            organization_id = organization.id

        user = User(email=email, password_hash="x", role=role, organization_id=organization_id)
        db.add(user)
        db.flush()

        device_uuid = str(uuid_module.uuid4())
        db.add(
            Device(
                device_uuid=device_uuid,
                device_name="PC",
                user_id=user.id,
                organization_id=organization_id,
                secret_hash=hash_token(secret),
                is_active=True,
            )
        )
        db.commit()
        token = create_access_token(
            user_id=user.id, email=email, role=role, organization_id=organization_id
        )

    return (
        {"Authorization": f"Bearer {token}"},
        {"X-Device-Secret": secret, "_uuid": device_uuid},
        organization_id,
    )


def _upload(
    client: TestClient,
    agent_headers: dict,
    *,
    days_ago: float = 0,
    name: str = "Session",
    minutes: float = 60,
    score: float = 80.0,
    focused_minutes: float | None = None,
    phone_count: int = 0,
    domain: str | None = None,
    domain_minutes: float = 0,
    classification: str = "PRODUCTIVE",
):
    """Push one finished session through the real ingest endpoint."""
    started = NOW - timedelta(days=days_ago)
    ended = started + timedelta(minutes=minutes)
    # Clamped, because the upload schema correctly refuses negative distracted
    # time and a short session would otherwise overshoot the default.
    focused = min(minutes, 40.0 if focused_minutes is None else focused_minutes)
    prefix = uuid_module.uuid4().hex[:8]

    vision = [
        {
            "event_uuid": f"{prefix}-v{index}",
            "event_type": "PHONE_VISIBLE",
            "start_time": (started + timedelta(minutes=index)).isoformat(),
            "end_time": (started + timedelta(minutes=index, seconds=20)).isoformat(),
            "duration_seconds": 20.0,
            "confidence": 0.9,
        }
        for index in range(phone_count)
    ]

    activity = []
    if domain is not None:
        activity.append(
            {
                "event_uuid": f"{prefix}-a0",
                "application_name": "Google Chrome",
                "domain": domain,
                "category": "VIDEO",
                "classification": classification,
                "rule_source": "TASK_RULE",
                "start_time": started.isoformat(),
                "end_time": (started + timedelta(minutes=domain_minutes)).isoformat(),
                "duration_seconds": domain_minutes * 60,
            }
        )

    body = {
        "device_uuid": agent_headers["_uuid"],
        "agent_version": "0.1.0",
        "session": {
            "session_uuid": f"{prefix}-session",
            "name": name,
            "started_at": started.isoformat(),
            "ended_at": ended.isoformat(),
            "duration_seconds": minutes * 60,
            "focused_seconds": focused * 60,
            "distracted_seconds": (minutes - focused) * 60,
            "average_focus_score": score,
            "longest_focus_block_seconds": 600.0,
        },
        "vision_events": vision,
        "activity_events": activity,
        "focus_scores": [],
    }

    headers = {k: v for k, v in agent_headers.items() if not k.startswith("_")}
    response = client.post("/api/v1/agent/events", json=body, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


# --- the summary (spec section 42) -----------------------------------------


def test_the_summary_totals_the_window(client, session_factory) -> None:
    user, agent, _ = _make_actor(session_factory, "s@example.com")
    _upload(client, agent, days_ago=0, minutes=60, focused_minutes=45, score=80)
    _upload(client, agent, days_ago=1, minutes=30, focused_minutes=15, score=60)

    payload = client.get("/api/v1/reports/summary?days=7", headers=user).json()

    assert payload["sessions"] == 2
    assert payload["monitored_seconds"] == pytest.approx(90 * 60)
    assert payload["focused_seconds"] == pytest.approx(60 * 60)


def test_empty_days_are_included(client, session_factory) -> None:
    """A chart that skips them makes a week off look like continuous work."""
    user, agent, _ = _make_actor(session_factory, "gaps@example.com")
    _upload(client, agent, days_ago=0)

    payload = client.get("/api/v1/reports/summary?days=7", headers=user).json()

    assert len(payload["daily"]) == 7
    assert sum(1 for day in payload["daily"] if day["sessions"] == 0) == 6


def test_the_average_is_weighted_by_time_not_by_session(client, session_factory) -> None:
    """A two-minute session must not count as much as a two-hour one."""
    user, agent, _ = _make_actor(session_factory, "weighted@example.com")
    _upload(client, agent, days_ago=0, minutes=120, score=90)
    _upload(client, agent, days_ago=0, minutes=2, score=10)

    payload = client.get("/api/v1/reports/summary?days=7", headers=user).json()

    assert payload["average_focus_score"] > 85, "a flat mean would give 50"


def test_sessions_outside_the_window_are_excluded(client, session_factory) -> None:
    user, agent, _ = _make_actor(session_factory, "window@example.com")
    _upload(client, agent, days_ago=0, name="Inside")
    _upload(client, agent, days_ago=20, name="Outside")

    week = client.get("/api/v1/reports/summary?days=7", headers=user).json()
    month = client.get("/api/v1/reports/summary?days=30", headers=user).json()

    assert week["sessions"] == 1
    assert month["sessions"] == 2


def test_an_absurd_window_is_refused(client, session_factory) -> None:
    user, _, _ = _make_actor(session_factory, "absurd@example.com")

    assert client.get("/api/v1/reports/summary?days=0", headers=user).status_code == 422
    assert client.get("/api/v1/reports/summary?days=9999", headers=user).status_code == 422


def test_reports_require_a_token(client) -> None:
    for path in ("summary", "recommendations", "export.csv"):
        assert client.get(f"/api/v1/reports/{path}").status_code == 401


# --- recommendations through the API ---------------------------------------


def test_recommendations_carry_their_evidence(client, session_factory) -> None:
    """The section 9 acceptance criterion, over HTTP."""
    user, agent, _ = _make_actor(session_factory, "advice@example.com")
    for index in range(4):
        _upload(client, agent, days_ago=index, minutes=60, phone_count=10, score=55)

    payload = client.get("/api/v1/reports/recommendations?days=7", headers=user).json()

    assert payload, "four sessions with a phone habit should produce something"
    for item in payload:
        assert item["evidence"], f"{item['rule_id']} has no evidence"
        assert all(e["label"] and e["value"] for e in item["evidence"])

    phone = next(item for item in payload if item["rule_id"] == "phone_in_reach")
    values = {e["label"]: e["value"] for e in phone["evidence"]}
    assert values["Times the phone was visible"] == "40"


def test_too_little_data_says_so_over_the_api(client, session_factory) -> None:
    user, agent, _ = _make_actor(session_factory, "sparse@example.com")
    _upload(client, agent, days_ago=0)

    payload = client.get("/api/v1/reports/recommendations", headers=user).json()

    assert len(payload) == 1
    assert payload[0]["rule_id"] == "not_enough_data"


def test_no_recommendation_response_contains_clinical_language(
    client, session_factory
) -> None:
    """Section 8, checked on the actual serialised response."""
    from focusguard_shared.recommendations import find_clinical_language

    user, agent, _ = _make_actor(session_factory, "language@example.com")
    for index in range(5):
        _upload(
            client,
            agent,
            days_ago=index,
            minutes=90,
            phone_count=12,
            score=35,
            focused_minutes=20,
            domain="youtube.com",
            domain_minutes=45,
            classification="DISTRACTING",
        )

    raw = client.get("/api/v1/reports/recommendations?limit=8", headers=user).text

    assert find_clinical_language(raw) == []


# --- export (spec section 43) ----------------------------------------------


def test_the_export_is_real_csv_with_the_sessions_in_it(client, session_factory) -> None:
    user, agent, _ = _make_actor(session_factory, "export@example.com")
    _upload(client, agent, days_ago=0, name="Revision", minutes=45, score=77)

    response = client.get("/api/v1/reports/export.csv", headers=user)

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]

    text = response.content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))

    assert len(rows) == 1
    assert rows[0]["name"] == "Revision"
    assert float(rows[0]["average_focus_score"]) == pytest.approx(77.0)
    assert float(rows[0]["duration_seconds"]) == pytest.approx(45 * 60)


def test_the_export_opens_correctly_in_excel(client, session_factory) -> None:
    """A UTF-8 BOM, or a session named in Urdu opens as mojibake on Windows."""
    user, agent, _ = _make_actor(session_factory, "bom@example.com")
    _upload(client, agent, days_ago=0, name="مطالعہ")

    response = client.get("/api/v1/reports/export.csv", headers=user)

    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "مطالعہ" in response.content.decode("utf-8-sig")


def test_the_export_contains_no_urls_or_frames(client, session_factory) -> None:
    """Section 33, on the file a student actually walks away with."""
    user, agent, _ = _make_actor(session_factory, "clean@example.com")
    _upload(client, agent, days_ago=0, domain="youtube.com", domain_minutes=10)

    text = client.get("/api/v1/reports/export.csv", headers=user).content.decode("utf-8-sig")

    for forbidden in ("http://", "https://", "screenshot", "frame", "window_title"):
        assert forbidden not in text.lower()


# --- tenant isolation on the aggregating routes (spec section 51) ----------


def test_one_students_report_never_includes_another(client, session_factory) -> None:
    mine_user, mine_agent, _ = _make_actor(session_factory, "mine@example.com")
    _, theirs_agent, _ = _make_actor(session_factory, "theirs@example.com")

    _upload(client, mine_agent, days_ago=0, minutes=30, name="Mine")
    for _ in range(5):
        _upload(client, theirs_agent, days_ago=0, minutes=120, name="Theirs")

    payload = client.get("/api/v1/reports/summary?days=7", headers=mine_user).json()

    assert payload["sessions"] == 1
    assert payload["monitored_seconds"] == pytest.approx(30 * 60)


def test_a_managers_report_stops_at_their_organisation(client, session_factory) -> None:
    """Aggregation is where a missing filter looks like a plausible number."""
    alpha_user, alpha_agent, _ = _make_actor(
        session_factory, "boss@alpha.com", org_name="Alpha", role="MANAGER"
    )
    _, beta_agent, _ = _make_actor(
        session_factory, "worker@beta.com", org_name="Beta", role="EMPLOYEE"
    )

    _upload(client, alpha_agent, days_ago=0, minutes=10, name="Alpha work")
    for _ in range(4):
        _upload(client, beta_agent, days_ago=0, minutes=180, name="Beta confidential")

    payload = client.get("/api/v1/reports/summary?days=7", headers=alpha_user).json()

    assert payload["sessions"] == 1
    assert payload["monitored_seconds"] == pytest.approx(10 * 60)


def test_a_cross_tenant_export_returns_only_your_own(client, session_factory) -> None:
    alpha_user, alpha_agent, _ = _make_actor(
        session_factory, "m@alpha.com", org_name="Alpha", role="MANAGER"
    )
    _, beta_agent, _ = _make_actor(
        session_factory, "w@beta.com", org_name="Beta", role="EMPLOYEE"
    )

    _upload(client, alpha_agent, days_ago=0, name="Alpha session")
    _upload(client, beta_agent, days_ago=0, name="Beta secret project")

    text = client.get("/api/v1/reports/export.csv", headers=alpha_user).content.decode(
        "utf-8-sig"
    )

    assert "Alpha session" in text
    assert "Beta secret project" not in text


def test_a_cross_tenant_recommendation_uses_only_your_own_data(
    client, session_factory
) -> None:
    """Advice built from another company's numbers would leak them by implication."""
    alpha_user, alpha_agent, _ = _make_actor(
        session_factory, "mm@alpha.com", org_name="Alpha", role="MANAGER"
    )
    _, beta_agent, _ = _make_actor(
        session_factory, "ww@beta.com", org_name="Beta", role="EMPLOYEE"
    )

    _upload(client, alpha_agent, days_ago=0)
    for index in range(6):
        _upload(
            client,
            beta_agent,
            days_ago=index,
            phone_count=20,
            domain="beta-internal.com",
            domain_minutes=50,
            classification="DISTRACTING",
        )

    payload = client.get("/api/v1/reports/recommendations", headers=alpha_user).json()

    assert len(payload) == 1
    assert payload[0]["rule_id"] == "not_enough_data", "Alpha has one session, not seven"
    assert "beta-internal.com" not in str(payload)
