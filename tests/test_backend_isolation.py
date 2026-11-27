"""Tenant isolation (spec section 51).

*"A manager from Company A must NEVER access Company B data. All
organization-related queries must enforce tenant isolation."*

That is a security requirement, so it is proven here rather than asserted in a
comment. These tests exist to fail loudly the day someone writes a query without
a scope.

The design that makes them pass: every repository read takes a `Scope` that the
function signature **requires**. Forgetting it is a TypeError at import time, not
a data leak in production.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from focusguard_backend.database.session import create_all, get_engine, reset_engine
from focusguard_backend.models import Organization, User
from focusguard_backend.repositories.telemetry import Scope, TelemetryRepository

START = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "isolation.db"))
    reset_engine()
    create_all(get_engine())

    from focusguard_backend.database.session import get_session_factory

    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


@pytest.fixture
def two_companies(db):
    """Two organisations, each with an employee and a session of their own."""
    alpha = Organization(name="Company Alpha", slug="alpha")
    beta = Organization(name="Company Beta", slug="beta")
    db.add_all([alpha, beta])
    db.flush()

    alice = User(
        email="alice@alpha.com", password_hash="x", role="EMPLOYEE",
        organization_id=alpha.id,
    )
    bob = User(
        email="bob@beta.com", password_hash="x", role="EMPLOYEE", organization_id=beta.id
    )
    db.add_all([alice, bob])
    db.flush()

    repository = TelemetryRepository(db)
    alice_session = repository.create_session(
        session_uuid="alpha-session", user_id=alice.id, name="Alpha work",
        started_at=START, organization_id=alpha.id,
    )
    bob_session = repository.create_session(
        session_uuid="beta-session", user_id=bob.id, name="Beta work",
        started_at=START, organization_id=beta.id,
    )

    for session, uid in ((alice_session, "alpha-ev"), (bob_session, "beta-ev")):
        repository.ingest_vision_events(
            session,
            [
                {
                    "event_uuid": uid,
                    "event_type": "PHONE_VISIBLE",
                    "start_time": START,
                    "end_time": START + timedelta(seconds=10),
                    "duration_seconds": 10.0,
                    "confidence": 0.9,
                }
            ],
        )
        repository.ingest_activity_events(
            session,
            [
                {
                    "event_uuid": f"{uid}-act",
                    "application_name": "Secret Internal Tool",
                    "category": "DEVELOPMENT",
                    "classification": "PRODUCTIVE",
                    "rule_source": "TASK_RULE",
                    "start_time": START,
                    "end_time": START + timedelta(minutes=5),
                    "duration_seconds": 300.0,
                }
            ],
        )
        repository.ingest_scores(
            session,
            [
                {
                    "recorded_at": START,
                    "visual_attention_score": 90.0,
                    "combined_focus_score": 90.0,
                }
            ],
        )

    db.commit()
    return {
        "alpha": {"org": alpha.id, "user": alice.id, "session": alice_session.id},
        "beta": {"org": beta.id, "user": bob.id, "session": bob_session.id},
    }


# --- a scope cannot be omitted ---------------------------------------------


def test_an_unscoped_scope_cannot_be_constructed() -> None:
    """The structural defence: an unscoped query matches everything.

    Refusing to build the object means the mistake is caught at the point it is
    made, not by a security review months later.
    """
    with pytest.raises(ValueError, match="user or an organisation"):
        Scope()


def test_a_scope_must_be_passed_to_every_read(db) -> None:
    """Forgetting the filter is a TypeError, not a silent over-fetch."""
    repository = TelemetryRepository(db)

    with pytest.raises(TypeError):
        repository.list_sessions()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        repository.get_session(1)  # type: ignore[call-arg]


# --- organisation boundaries ------------------------------------------------


def test_a_manager_sees_only_their_own_organisations_sessions(db, two_companies) -> None:
    repository = TelemetryRepository(db)

    alpha_sessions = repository.list_sessions(Scope.for_organization(two_companies["alpha"]["org"]))

    assert len(alpha_sessions) == 1
    assert alpha_sessions[0].name == "Alpha work"


def test_another_organisations_session_is_invisible_by_id(db, two_companies) -> None:
    """Knowing the id must not be enough. Section 51's core requirement."""
    repository = TelemetryRepository(db)

    stolen = repository.get_session(
        two_companies["beta"]["session"],
        Scope.for_organization(two_companies["alpha"]["org"]),
    )

    assert stolen is None


def test_another_organisations_events_are_invisible(db, two_companies) -> None:
    repository = TelemetryRepository(db)
    alpha_scope = Scope.for_organization(two_companies["alpha"]["org"])
    beta_session = two_companies["beta"]["session"]

    assert repository.vision_events(beta_session, alpha_scope) == []
    assert repository.activity_events(beta_session, alpha_scope) == []
    assert repository.scores(beta_session, alpha_scope) == []


def test_an_application_name_does_not_leak_across_organisations(db, two_companies) -> None:
    """Even a name can be commercially sensitive - an unreleased internal tool."""
    repository = TelemetryRepository(db)

    events = repository.activity_events(
        two_companies["beta"]["session"],
        Scope.for_organization(two_companies["alpha"]["org"]),
    )

    assert events == []


def test_each_organisation_sees_its_own_data(db, two_companies) -> None:
    """Isolation must not be achieved by returning nothing to anybody."""
    repository = TelemetryRepository(db)

    for company in ("alpha", "beta"):
        scope = Scope.for_organization(two_companies[company]["org"])
        sessions = repository.list_sessions(scope)

        assert len(sessions) == 1
        assert len(repository.vision_events(sessions[0].id, scope)) == 1


# --- user boundaries --------------------------------------------------------


def test_a_student_sees_only_their_own_sessions(db, two_companies) -> None:
    repository = TelemetryRepository(db)

    sessions = repository.list_sessions(Scope.for_user(two_companies["alpha"]["user"]))

    assert len(sessions) == 1
    assert sessions[0].name == "Alpha work"


def test_one_user_cannot_read_anothers_session(db, two_companies) -> None:
    repository = TelemetryRepository(db)

    stolen = repository.get_session(
        two_companies["beta"]["session"], Scope.for_user(two_companies["alpha"]["user"])
    )

    assert stolen is None


def test_a_session_uuid_is_also_scoped(db, two_companies) -> None:
    """A guessable identifier must not be a way around the filter."""
    repository = TelemetryRepository(db)

    found = repository.get_session_by_uuid(
        "beta-session", Scope.for_user(two_companies["alpha"]["user"])
    )

    assert found is None


def test_both_filters_apply_together(db, two_companies) -> None:
    """A manager viewing one employee is limited to that employee AND their org.

    Neither filter alone is sufficient: the org alone would show every colleague,
    and the user alone would work across organisations.
    """
    repository = TelemetryRepository(db)

    mismatched = Scope(
        user_id=two_companies["beta"]["user"],
        organization_id=two_companies["alpha"]["org"],
    )

    assert repository.list_sessions(mismatched) == []


# --- the API layer ----------------------------------------------------------


def test_the_api_refuses_to_serve_another_users_session(tmp_path, monkeypatch) -> None:
    """End to end over HTTP, not just at the repository."""
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "api_isolation.db"))
    reset_engine()

    from fastapi.testclient import TestClient

    from focusguard_backend.database.session import get_session_factory
    from focusguard_backend.main import app

    create_all(get_engine())

    with get_session_factory()() as db:
        victim = User(email="victim@example.com", password_hash="x", role="STUDENT")
        attacker = User(email="attacker@example.com", password_hash="x", role="STUDENT")
        db.add_all([victim, attacker])
        db.flush()

        session = TelemetryRepository(db).create_session(
            session_uuid="victim-session", user_id=victim.id,
            name="Private study", started_at=START,
        )
        db.commit()
        session_id, attacker_id, victim_id = session.id, attacker.id, victim.id

    from focusguard_backend.auth.tokens import create_access_token

    # The attacker is properly signed in - as themselves. Phase 13 removed the
    # query parameter that used to let a caller name whose data they wanted, so
    # the only thing left to try is their own valid token.
    attacker_token = create_access_token(
        user_id=attacker_id, email="attacker@example.com", role="STUDENT"
    )

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        # 404 rather than 403: confirming a session exists but is not yours still
        # tells a stranger that it exists.
        assert response.status_code == 404
        assert "Private study" not in response.text

        # And naming the victim in a parameter changes nothing: the scope comes
        # from the signed claim, and a stray query string is simply ignored.
        also_refused = client.get(
            f"/api/v1/sessions/{session_id}",
            params={"user_id": victim_id},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert also_refused.status_code == 404

    reset_engine()


# --- every org-scoped route, with a genuine manager token -------------------
#
# The Phase 13 acceptance criterion: a Company A manager must receive 403 or 404
# on **every** organisation-scoped route, and never data. One test per route, so
# a route added later without a scope shows up as a gap here rather than as a
# breach in production.


@pytest.fixture
def two_company_api(tmp_path, monkeypatch):
    """A live API with two organisations, each with a manager and an employee."""
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "cross_tenant.db"))
    reset_engine()

    from fastapi.testclient import TestClient

    from focusguard_backend.auth.tokens import create_access_token, hash_token
    from focusguard_backend.database.session import get_session_factory
    from focusguard_backend.main import app
    from focusguard_backend.models import Device

    create_all(get_engine())

    world: dict[str, object] = {}

    with get_session_factory()() as db:
        alpha = Organization(name="Company Alpha", slug="alpha")
        beta = Organization(name="Company Beta", slug="beta")
        db.add_all([alpha, beta])
        db.flush()

        alpha_boss = User(
            email="boss@alpha.test", password_hash="x", role="MANAGER",
            organization_id=alpha.id,
        )
        beta_boss = User(
            email="boss@beta.test", password_hash="x", role="MANAGER",
            organization_id=beta.id,
        )
        beta_worker = User(
            email="worker@beta.test", password_hash="x", role="EMPLOYEE",
            organization_id=beta.id,
        )
        db.add_all([alpha_boss, beta_boss, beta_worker])
        db.flush()

        beta_device = Device(
            device_uuid="beta-device-uuid",
            device_name="Beta workstation",
            user_id=beta_worker.id,
            organization_id=beta.id,
            secret_hash=hash_token("beta-device-secret"),
            is_active=True,
        )
        db.add(beta_device)
        db.flush()

        beta_session = TelemetryRepository(db).create_session(
            session_uuid="beta-session",
            user_id=beta_worker.id,
            name="Beta confidential work",
            started_at=START,
            device_id=beta_device.id,
            organization_id=beta.id,
        )
        db.commit()

        world["beta_session_id"] = beta_session.id
        world["beta_worker_id"] = beta_worker.id
        world["beta_device_id"] = beta_device.id
        world["alpha_token"] = create_access_token(
            user_id=alpha_boss.id,
            email=alpha_boss.email,
            role="MANAGER",
            organization_id=alpha.id,
        )

    with TestClient(app) as client:
        world["client"] = client
        yield world

    reset_engine()


def _alpha_headers(world) -> dict[str, str]:
    return {"Authorization": f"Bearer {world['alpha_token']}"}


def test_cross_tenant_session_listing_returns_nothing(two_company_api) -> None:
    client = two_company_api["client"]

    response = client.get("/api/v1/sessions", headers=_alpha_headers(two_company_api))

    assert response.status_code == 200
    assert response.json() == [], "Alpha has no sessions, and must not see Beta's"
    assert "Beta confidential work" not in response.text


def test_cross_tenant_session_detail_is_a_404(two_company_api) -> None:
    client = two_company_api["client"]

    response = client.get(
        f"/api/v1/sessions/{two_company_api['beta_session_id']}",
        headers=_alpha_headers(two_company_api),
    )

    assert response.status_code == 404
    assert "Beta confidential work" not in response.text


def test_cross_tenant_device_listing_returns_nothing(two_company_api) -> None:
    client = two_company_api["client"]

    response = client.get("/api/v1/devices", headers=_alpha_headers(two_company_api))

    assert response.status_code == 200
    assert response.json() == []
    assert "beta-device-uuid" not in response.text


def test_cross_tenant_enrolment_is_refused(two_company_api) -> None:
    """Alpha's manager must not be able to attach a device to Beta's employee."""
    client = two_company_api["client"]

    response = client.post(
        "/api/v1/devices",
        json={"device_name": "Wedge", "user_id": two_company_api["beta_worker_id"]},
        headers=_alpha_headers(two_company_api),
    )

    assert response.status_code == 404


def test_cross_tenant_device_revocation_is_refused(two_company_api) -> None:
    """Refusing to *read* is not enough; a write across the boundary is worse."""
    client = two_company_api["client"]

    response = client.delete(
        f"/api/v1/devices/{two_company_api['beta_device_id']}",
        headers=_alpha_headers(two_company_api),
    )

    assert response.status_code == 404

    from focusguard_backend.database.session import get_session_factory
    from focusguard_backend.models import Device

    with get_session_factory()() as db:
        device = db.get(Device, two_company_api["beta_device_id"])
        assert device.revoked_at is None, "Beta's device is untouched"
        assert device.is_active is True


def test_every_org_scoped_route_is_covered_here(two_company_api) -> None:
    """A route added without a scope should fail this, not leak quietly.

    The list is written out by hand on purpose: an automated crawl would happily
    grow to include a new unprotected route and keep passing.
    """
    client = two_company_api["client"]
    spec = client.app.openapi()

    reachable_with_a_user_token = {
        path
        for path in spec["paths"]
        # /auth is about the caller themselves and /agent takes a device
        # credential, neither of which is organisation-scoped. /downloads is the
        # public Windows installer: the same for everybody, no account data
        # (tests/test_installer_download.py checks what it contains).
        if path.startswith("/api/v1/")
        and not path.startswith(("/api/v1/auth", "/api/v1/agent", "/api/v1/downloads"))
    }
    covered = {
        "/api/v1/sessions",
        "/api/v1/sessions/{session_id}",
        "/api/v1/devices",
        "/api/v1/devices/{device_id}",
        "/api/v1/devices/enroll",
        # Phase 15. Cross-tenant coverage for these lives in
        # tests/test_backend_reports.py, which is where the fixtures for
        # uploading real sessions already are.
        "/api/v1/reports/summary",
        "/api/v1/reports/recommendations",
        "/api/v1/reports/export.csv",
        # Milestone E. Cross-tenant coverage for every manager route lives in
        # tests/test_backend_business.py (test_alpha_lists_contain_nothing_of_beta
        # and test_alpha_cannot_read_or_write_betas_records_by_id).
        "/api/v1/manager/organization",
        "/api/v1/manager/dashboard",
        "/api/v1/manager/departments",
        "/api/v1/manager/departments/{department_id}",
        "/api/v1/manager/employees",
        "/api/v1/manager/employees/{employee_id}",
        "/api/v1/manager/employees/{employee_id}/strengths",
        "/api/v1/manager/projects",
        "/api/v1/manager/projects/{project_id}",
        "/api/v1/manager/tasks",
        "/api/v1/manager/tasks/suggestions",
        "/api/v1/manager/tasks/{task_id}",
        "/api/v1/manager/reports/employees.csv",
        "/api/v1/manager/reports/tasks.csv",
    }

    assert reachable_with_a_user_token <= covered, (
        "a new organisation-scoped route exists with no cross-tenant test: "
        f"{sorted(reachable_with_a_user_token - covered)}"
    )
