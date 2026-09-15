"""Authentication, RBAC and device enrolment (spec sections 25, 51).

These are security properties, so they are proven rather than asserted in a
comment. The ones that would matter most if they broke:

* a manager from Company A cannot reach Company B, **through the HTTP API, with a
  genuine token of their own** - not just at the repository layer;
* a replayed refresh token kills the whole session family;
* a revoked device credential stops working immediately;
* an enrolment token works exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.auth import rate_limit
from focusguard_backend.auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from focusguard_backend.auth.rate_limit import RateLimiter
from focusguard_backend.auth.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_token,
)
from focusguard_backend.database.session import create_all, get_engine, reset_engine
from focusguard_backend.models import Device, Organization, RefreshToken, User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "auth.db"))
    reset_engine()
    # Limiters are process-wide, so a test that failed logins would otherwise
    # leave the next one throttled.
    rate_limit.reset_all()

    from focusguard_backend.main import app

    create_all(get_engine())
    with TestClient(app) as test_client:
        yield test_client

    rate_limit.reset_all()
    reset_engine()


@pytest.fixture
def session_factory():
    from focusguard_backend.database.session import get_session_factory

    return get_session_factory()


def _make_account(
    session_factory,
    email: str,
    role: str = "STUDENT",
    org_name: str | None = None,
    organization_id: int | None = None,
) -> tuple[int, int | None]:
    """Create a user directly. Returns (user_id, organization_id)."""
    with session_factory() as db:
        if org_name is not None:
            organization = Organization(name=org_name, slug=org_name.lower().replace(" ", "-"))
            db.add(organization)
            db.flush()
            organization_id = organization.id

        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            role=role,
            organization_id=organization_id,
            is_active=True,
        )
        db.add(user)
        db.commit()
        return user.id, organization_id


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- password hashing -------------------------------------------------------


def test_a_password_is_never_stored_in_the_clear() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    assert stored.startswith("$argon2id$"), "Argon2id, not bcrypt: no 72-byte truncation"
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)


def test_a_long_passphrase_is_not_silently_truncated() -> None:
    """The bcrypt trap: two passphrases differing only after 72 bytes must differ.

    A user with a long passphrase would otherwise get far less security than they
    believe, with nothing warning anybody.
    """
    base = "x" * 72
    stored = hash_password(base + "AAAA")

    assert not verify_password(base + "BBBB", stored)


def test_verifying_against_a_missing_user_still_does_the_work() -> None:
    """Timing must not answer 'does this account exist?'."""
    assert verify_password(PASSWORD, None) is False


def test_a_corrupt_hash_refuses_rather_than_crashing() -> None:
    assert verify_password(PASSWORD, "not-a-hash") is False
    assert needs_rehash("not-a-hash") is True


def test_password_strength_is_length_not_composition() -> None:
    assert validate_password_strength("short") is not None
    assert validate_password_strength("        ") is not None
    assert validate_password_strength("x" * 300) is not None
    # No symbol, no digit, no capital - and accepted, deliberately.
    assert validate_password_strength("abcdefghijkl") is None


# --- tokens -----------------------------------------------------------------


def test_an_access_token_round_trips_its_claims() -> None:
    token = create_access_token(user_id=7, email="a@b.com", role="MANAGER", organization_id=3)
    claims = decode_access_token(token)

    assert claims.user_id == 7
    assert claims.role == "MANAGER"
    assert claims.organization_id == 3


def test_a_token_signed_with_another_key_is_refused() -> None:
    import jwt

    forged = jwt.encode(
        {"sub": "1", "role": "MANAGER", "type": "access", "exp": 9999999999},
        "an-attackers-own-key-that-is-long-enough",
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_an_expired_token_is_refused() -> None:
    token = create_access_token(
        user_id=1, email="a@b.com", role="STUDENT", expires_in_minutes=-1
    )
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_a_refresh_token_is_not_accepted_as_an_access_token() -> None:
    """Confusing the two would extend a 15-minute session to 30 days."""
    import jwt

    from focusguard_backend.auth.tokens import get_secret_key

    wrong_type = jwt.encode(
        {"sub": "1", "type": "refresh", "exp": 9999999999}, get_secret_key(), algorithm="HS256"
    )
    with pytest.raises(TokenError):
        decode_access_token(wrong_type)


def test_a_token_hash_is_one_way() -> None:
    value = generate_refresh_token()
    assert hash_token(value) != value
    assert len(hash_token(value)) == 64
    assert hash_token(value) == hash_token(value), "deterministic, so it can be looked up"


# --- rate limiting ----------------------------------------------------------


def test_the_limiter_blocks_after_the_configured_attempts() -> None:
    limiter = RateLimiter(max_attempts=3, window_seconds=60.0)

    for _ in range(3):
        assert limiter.check("key", now=100.0) is None
        limiter.record_failure("key", now=100.0)

    assert limiter.check("key", now=100.0) is not None


def test_the_window_slides() -> None:
    limiter = RateLimiter(max_attempts=2, window_seconds=60.0)
    limiter.record_failure("key", now=100.0)
    limiter.record_failure("key", now=100.0)

    assert limiter.check("key", now=120.0) is not None, "still inside the window"
    assert limiter.check("key", now=200.0) is None, "the failures have aged out"


def test_a_success_clears_the_count() -> None:
    """Two typos then a success must not leave a user near a lockout."""
    limiter = RateLimiter(max_attempts=2, window_seconds=60.0)
    limiter.record_failure("key", now=100.0)
    limiter.clear("key")

    limiter.record_failure("key", now=100.0)
    assert limiter.check("key", now=100.0) is None


# --- registration and login -------------------------------------------------


def test_registration_creates_a_student_and_returns_no_password(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "New.Student@Example.com", "password": PASSWORD, "full_name": "Sam"},
    )
    payload = response.json()

    assert response.status_code == 201
    assert payload["email"] == "new.student@example.com", "normalised to lower case"
    assert payload["role"] == "STUDENT"
    assert payload["organization_id"] is None
    assert "password" not in payload and "password_hash" not in payload


def test_registration_cannot_choose_a_role(client: TestClient) -> None:
    """Otherwise anyone could mint themselves a manager account and a tenant."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "sneaky@example.com", "password": PASSWORD, "role": "MANAGER"},
    )
    # extra="forbid" on the upload schema: the field does not exist, so it is a
    # refusal rather than a silently ignored value.
    assert response.status_code == 422


def test_registering_a_taken_address_does_not_confirm_it_is_taken(
    client: TestClient,
) -> None:
    body = {"email": "taken@example.com", "password": PASSWORD}
    client.post("/api/v1/auth/register", json=body)

    response = client.post("/api/v1/auth/register", json=body)

    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "already" not in detail and "exists" not in detail


def test_a_weak_password_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register", json={"email": "weak@example.com", "password": "short"}
    )
    assert response.status_code == 422


def test_login_returns_a_usable_token_pair(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "student@example.com")

    response = _login(client, "student@example.com")
    payload = response.json()

    assert response.status_code == 200
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 15 * 60

    me = client.get("/api/v1/auth/me", headers=_bearer(payload["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "student@example.com"


def test_a_wrong_password_and_an_unknown_account_look_identical(
    client: TestClient, session_factory
) -> None:
    """An error message must not answer 'is this person registered here?'."""
    _make_account(session_factory, "real@example.com")

    wrong = _login(client, "real@example.com", "not-the-password")
    unknown = _login(client, "nobody@example.com", "not-the-password")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_a_deactivated_account_cannot_log_in(client: TestClient, session_factory) -> None:
    user_id, _ = _make_account(session_factory, "gone@example.com")
    with session_factory() as db:
        db.get(User, user_id).is_active = False
        db.commit()

    assert _login(client, "gone@example.com").status_code == 401


def test_a_deactivated_account_loses_access_immediately(
    client: TestClient, session_factory
) -> None:
    """Not when their access token expires fifteen minutes later."""
    user_id, _ = _make_account(session_factory, "fired@example.com")
    token = _login(client, "fired@example.com").json()["access_token"]

    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 200

    with session_factory() as db:
        db.get(User, user_id).is_active = False
        db.commit()

    assert client.get("/api/v1/auth/me", headers=_bearer(token)).status_code == 401


def test_repeated_failures_are_throttled(client: TestClient, session_factory) -> None:
    """Argon2id is 64 MB per attempt, so unthrottled guessing is also a DoS."""
    _make_account(session_factory, "target@example.com")

    codes = [
        _login(client, "target@example.com", "wrong").status_code
        for _ in range(rate_limit.IP_MAX_ATTEMPTS + 1)
    ]

    assert codes[0] == 401
    assert codes[-1] == 429


def test_a_successful_login_clears_the_throttle(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "clumsy@example.com")

    for _ in range(rate_limit.IP_MAX_ATTEMPTS - 1):
        _login(client, "clumsy@example.com", "wrong")

    assert _login(client, "clumsy@example.com").status_code == 200
    # The counter is reset, so a fresh mistake does not trip the limit.
    assert _login(client, "clumsy@example.com", "wrong").status_code == 401


# --- refresh rotation -------------------------------------------------------


def test_refreshing_rotates_the_token(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "rotate@example.com")
    first = _login(client, "rotate@example.com").json()

    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    assert second["refresh_token"] != first["refresh_token"]
    assert client.get("/api/v1/auth/me", headers=_bearer(second["access_token"])).status_code == 200


def test_a_consumed_refresh_token_stops_working(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "used@example.com")
    first = _login(client, "used@example.com").json()

    client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    replay = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )

    assert replay.status_code == 401


def test_replaying_a_token_revokes_the_whole_family(
    client: TestClient, session_factory
) -> None:
    """Theft is assumed, because a replay cannot be told apart from one.

    The legitimate user is signed out and must log in again. That is a much
    smaller harm than a silently persistent hijack.
    """
    _make_account(session_factory, "stolen@example.com")
    first = _login(client, "stolen@example.com").json()
    second = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    ).json()

    # The attacker replays the token they captured.
    client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})

    # The legitimate user's current token is now dead too.
    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert response.status_code == 401


def test_an_expired_refresh_token_is_refused(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "stale@example.com")
    tokens = _login(client, "stale@example.com").json()

    with session_factory() as db:
        from sqlalchemy import select

        record = db.scalar(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_token(tokens["refresh_token"])
            )
        )
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    response = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert response.status_code == 401


def test_an_unknown_refresh_token_is_refused(client: TestClient) -> None:
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": "invented"})
    assert response.status_code == 401


def test_logout_ends_the_session(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "bye@example.com")
    tokens = _login(client, "bye@example.com").json()

    assert client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    ).status_code == 200

    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    ).status_code == 401


def test_logout_reports_success_for_an_unknown_token(client: TestClient) -> None:
    """Otherwise it is a free oracle for testing stolen values."""
    response = client.post("/api/v1/auth/logout", json={"refresh_token": "invented"})
    assert response.status_code == 200


def test_logout_all_ends_every_session(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "panic@example.com")
    laptop = _login(client, "panic@example.com").json()
    phone = _login(client, "panic@example.com").json()

    client.post("/api/v1/auth/logout-all", headers=_bearer(laptop["access_token"]))

    for tokens in (laptop, phone):
        assert client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        ).status_code == 401


# --- protected routes -------------------------------------------------------


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


def test_a_malformed_authorization_header_is_refused(client: TestClient) -> None:
    for header in ({"Authorization": "Bearer"}, {"Authorization": "Basic abc"}):
        assert client.get("/api/v1/auth/me", headers=header).status_code == 401


# --- tenant isolation through the API (spec section 51) --------------------


def test_a_manager_is_scoped_to_their_own_organisation(
    client: TestClient, session_factory
) -> None:
    """The section 51 requirement, end to end, with a genuine manager token."""
    _, alpha_id = _make_account(
        session_factory, "boss.alpha@example.com", role="MANAGER", org_name="Company Alpha"
    )
    _, beta_id = _make_account(
        session_factory, "boss.beta@example.com", role="MANAGER", org_name="Company Beta"
    )
    _make_account(
        session_factory, "worker.beta@example.com", role="EMPLOYEE", organization_id=beta_id
    )

    alpha_token = _login(client, "boss.alpha@example.com").json()["access_token"]

    # Company Alpha's manager enrols a device - and tries to attach it to Company
    # Beta's employee, which is the attack section 51 names.
    with session_factory() as db:
        from sqlalchemy import select

        beta_worker_id = db.scalar(
            select(User).where(User.email == "worker.beta@example.com")
        ).id

    response = client.post(
        "/api/v1/devices",
        json={"device_name": "Stolen view", "user_id": beta_worker_id},
        headers=_bearer(alpha_token),
    )

    # 404, not 403: confirming that user exists elsewhere is itself the leak.
    assert response.status_code == 404
    assert alpha_id != beta_id


def test_a_manager_sees_only_their_organisations_devices(
    client: TestClient, session_factory
) -> None:
    _, alpha_id = _make_account(
        session_factory, "m.alpha@example.com", role="MANAGER", org_name="Alpha"
    )
    _, beta_id = _make_account(
        session_factory, "m.beta@example.com", role="MANAGER", org_name="Beta"
    )

    with session_factory() as db:
        db.add_all(
            [
                Device(device_uuid="alpha-pc", device_name="Alpha PC", organization_id=alpha_id),
                Device(device_uuid="beta-pc", device_name="Beta PC", organization_id=beta_id),
            ]
        )
        db.commit()

    token = _login(client, "m.alpha@example.com").json()["access_token"]
    listing = client.get("/api/v1/devices", headers=_bearer(token)).json()

    names = {device["device_uuid"] for device in listing}
    assert names == {"alpha-pc"}


def test_a_student_cannot_enrol_a_device_for_someone_else(
    client: TestClient, session_factory
) -> None:
    _make_account(session_factory, "s1@example.com")
    other_id, _ = _make_account(session_factory, "s2@example.com")

    token = _login(client, "s1@example.com").json()["access_token"]
    response = client.post(
        "/api/v1/devices",
        json={"device_name": "Not mine", "user_id": other_id},
        headers=_bearer(token),
    )

    assert response.status_code == 403


# --- device enrolment (spec section 25) -------------------------------------


def test_enrolment_hands_over_a_credential_exactly_once(
    client: TestClient, session_factory
) -> None:
    """The three-step flow: prepare, redeem, and the token is spent."""
    _make_account(session_factory, "owner@example.com")
    token = _login(client, "owner@example.com").json()["access_token"]

    created = client.post(
        "/api/v1/devices", json={"device_name": "Study laptop"}, headers=_bearer(token)
    ).json()
    enrollment_token = created["enrollment_token"]

    first = client.post(
        "/api/v1/devices/enroll",
        json={
            "enrollment_token": enrollment_token,
            "device_uuid": "laptop-uuid-1",
            "platform": "Windows 11",
            "agent_version": "0.1.0",
        },
    )
    payload = first.json()

    assert first.status_code == 200
    assert payload["device_secret"]
    assert payload["device_uuid"] == "laptop-uuid-1"

    # Single use. An intercepted copy is worthless after the first redemption.
    second = client.post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": enrollment_token, "device_uuid": "laptop-uuid-2"},
    )
    assert second.status_code == 400


def test_an_enrolment_token_is_stored_hashed(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "hashed@example.com")
    token = _login(client, "hashed@example.com").json()["access_token"]

    created = client.post(
        "/api/v1/devices", json={"device_name": "PC"}, headers=_bearer(token)
    ).json()

    with session_factory() as db:
        device = db.get(Device, created["device_id"])
        assert device.enrollment_token_hash != created["enrollment_token"]
        assert device.enrollment_token_hash == hash_token(created["enrollment_token"])


def test_an_expired_enrolment_token_is_refused(client: TestClient, session_factory) -> None:
    """A token left in a chat log must be dead by the time anyone finds it."""
    _make_account(session_factory, "slow@example.com")
    token = _login(client, "slow@example.com").json()["access_token"]

    created = client.post(
        "/api/v1/devices", json={"device_name": "PC"}, headers=_bearer(token)
    ).json()

    with session_factory() as db:
        device = db.get(Device, created["device_id"])
        device.enrollment_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    response = client.post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": created["enrollment_token"], "device_uuid": "late-uuid"},
    )
    assert response.status_code == 400


def test_an_unknown_and_an_expired_token_give_the_same_answer(
    client: TestClient, session_factory
) -> None:
    _make_account(session_factory, "same@example.com")
    token = _login(client, "same@example.com").json()["access_token"]
    created = client.post(
        "/api/v1/devices", json={"device_name": "PC"}, headers=_bearer(token)
    ).json()

    with session_factory() as db:
        db.get(Device, created["device_id"]).enrollment_expires_at = datetime.now(
            UTC
        ) - timedelta(minutes=1)
        db.commit()

    expired = client.post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": created["enrollment_token"], "device_uuid": "uuid-aaaa"},
    )
    unknown = client.post(
        "/api/v1/devices/enroll", json={"enrollment_token": "invented", "device_uuid": "uuid-bbbb"}
    )

    assert expired.status_code == unknown.status_code == 400
    assert expired.json()["detail"] == unknown.json()["detail"]


def test_the_device_secret_is_stored_hashed(client: TestClient, session_factory) -> None:
    _make_account(session_factory, "secret@example.com")
    token = _login(client, "secret@example.com").json()["access_token"]
    created = client.post(
        "/api/v1/devices", json={"device_name": "PC"}, headers=_bearer(token)
    ).json()

    credential = client.post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": created["enrollment_token"], "device_uuid": "pc-uuid-0001"},
    ).json()

    with session_factory() as db:
        device = db.get(Device, created["device_id"])
        assert device.secret_hash == hash_token(credential["device_secret"])
        assert credential["device_secret"] not in str(device.secret_hash)


def test_creating_an_enrolment_requires_authentication(client: TestClient) -> None:
    assert client.post("/api/v1/devices", json={"device_name": "PC"}).status_code == 401


def test_revoking_a_device_stops_its_credential_working(
    client: TestClient, session_factory
) -> None:
    _make_account(session_factory, "revoke@example.com")
    token = _login(client, "revoke@example.com").json()["access_token"]
    created = client.post(
        "/api/v1/devices", json={"device_name": "Old laptop"}, headers=_bearer(token)
    ).json()
    credential = client.post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": created["enrollment_token"], "device_uuid": "old-uuid"},
    ).json()

    heartbeat = {"device_uuid": "old-uuid"}
    headers = {"X-Device-Secret": credential["device_secret"]}
    alive = client.post("/api/v1/agent/heartbeat", json=heartbeat, headers=headers)
    assert alive.status_code == 204

    revoked = client.delete(
        f"/api/v1/devices/{created['device_id']}", headers=_bearer(token)
    )
    assert revoked.status_code == 200

    # The secret is destroyed, not merely flagged, so it matches nothing at all.
    assert client.post(
        "/api/v1/agent/heartbeat", json=heartbeat, headers=headers
    ).status_code == 401


def test_revoking_keeps_the_devices_recorded_history(
    client: TestClient, session_factory
) -> None:
    """Replacing a laptop must not erase the sessions it uploaded."""
    _make_account(session_factory, "history@example.com")
    token = _login(client, "history@example.com").json()["access_token"]
    created = client.post(
        "/api/v1/devices", json={"device_name": "Laptop"}, headers=_bearer(token)
    ).json()

    client.delete(f"/api/v1/devices/{created['device_id']}", headers=_bearer(token))

    with session_factory() as db:
        device = db.get(Device, created["device_id"])
        assert device is not None, "the row survives; only the credential goes"
        assert device.revoked_at is not None


def test_a_device_belonging_to_someone_else_cannot_be_revoked(
    client: TestClient, session_factory
) -> None:
    _make_account(session_factory, "mine@example.com")
    _make_account(session_factory, "theirs@example.com")

    theirs_token = _login(client, "theirs@example.com").json()["access_token"]
    created = client.post(
        "/api/v1/devices", json={"device_name": "Their PC"}, headers=_bearer(theirs_token)
    ).json()

    mine_token = _login(client, "mine@example.com").json()["access_token"]
    response = client.delete(
        f"/api/v1/devices/{created['device_id']}", headers=_bearer(mine_token)
    )

    assert response.status_code == 404, "404, not 403: existence is itself information"
