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
        session_id, attacker_id = session.id, attacker.id

    with TestClient(app) as client:
        response = client.get(
            f"/api/v1/sessions/{session_id}", params={"user_id": attacker_id}
        )
        # 404 rather than 403: confirming a session exists but is not yours still
        # tells a stranger that it exists.
        assert response.status_code == 404
        assert "Private study" not in response.text

    reset_engine()
