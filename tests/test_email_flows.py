"""Sign-up email verification and password reset.

The properties that matter:

* a self-registered account cannot sign in until it enters the emailed code;
* a code works once, for one purpose, within 15 minutes, and dies after five
  wrong guesses;
* "resend" and "forgot password" answer identically whether or not the account
  exists, so they cannot be used to find out who has one;
* a reset changes the password and signs every existing session out;
* the code never appears in the database or the log.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.auth import rate_limit
from focusguard_backend.auth.passwords import NO_LOGIN_HASH, hash_password
from focusguard_backend.database.session import (
    create_all,
    get_engine,
    get_session_factory,
    reset_engine,
)
from focusguard_backend.models import EmailCode, User
from focusguard_backend.services import email_codes
from focusguard_backend.services.email import MemoryMailer, OutboxMailer, set_mailer

PASSWORD = "correct horse battery"
NEW_PASSWORD = "an entirely new passphrase"
EMAIL = "zara@uni.example.com"


@pytest.fixture
def mailer():
    box = MemoryMailer()
    set_mailer(box)
    yield box
    set_mailer(None)


@pytest.fixture
def client(tmp_path, monkeypatch, mailer):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "email.db"))
    monkeypatch.delenv("FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION", raising=False)
    reset_engine()
    rate_limit.reset_all()
    from focusguard_backend.main import app

    create_all(get_engine())
    with TestClient(app) as test_client:
        yield test_client
    rate_limit.reset_all()
    reset_engine()


def code_for(mailer: MemoryMailer, email: str = EMAIL) -> str:
    message = [m for m in mailer.messages if m.to == email][-1]
    return re.search(r"\b(\d{6})\b", message.subject).group(1)


def register(client: TestClient, email: str = EMAIL):
    return client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})


def login(client: TestClient, password: str = PASSWORD, email: str = EMAIL):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def allow_resend() -> None:
    """Age every code past the 60-second resend cooldown."""
    with get_session_factory()() as db:
        for record in db.query(EmailCode):
            record.created_at = record.created_at - timedelta(minutes=2)
        db.commit()


# --- sign-up verification --------------------------------------------------------


def test_registration_emails_a_code_and_blocks_sign_in_until_it_is_entered(client, mailer):
    response = register(client)
    assert response.status_code == 201
    assert response.json()["email_verified"] is False

    message = mailer.messages[-1]
    assert message.to == EMAIL
    assert "verification code" in message.subject
    code = code_for(mailer)
    spaced = f"{code[:3]} {code[3:]}"  # shown in two groups, easier to copy by eye
    assert spaced in message.html and spaced in message.text

    blocked = login(client)
    assert blocked.status_code == 403
    assert "verify your email" in blocked.json()["detail"]


def test_the_right_code_verifies_and_signs_in(client, mailer):
    register(client)

    response = client.post(
        "/api/v1/auth/verify-email", json={"email": EMAIL, "code": code_for(mailer)}
    )

    assert response.status_code == 200
    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["email_verified"] is True
    assert login(client).status_code == 200


def test_a_code_with_spaces_still_works(client, mailer):
    """People copy "123 456" exactly as the email shows it."""
    register(client)
    code = code_for(mailer)
    spaced = f"{code[:3]} {code[3:]}"
    assert client.post(
        "/api/v1/auth/verify-email", json={"email": EMAIL, "code": spaced}
    ).status_code == 200


def test_a_code_dies_after_five_wrong_guesses(client, mailer):
    register(client)
    right = code_for(mailer)
    wrong = "000000" if right != "000000" else "111111"

    for _ in range(5):
        response = client.post("/api/v1/auth/verify-email", json={"email": EMAIL, "code": wrong})
        assert response.status_code == 400

    # Even the right code is refused now; a new one must be requested.
    assert client.post(
        "/api/v1/auth/verify-email", json={"email": EMAIL, "code": right}
    ).status_code == 400


def test_an_expired_code_is_refused(client, mailer):
    register(client)
    with get_session_factory()() as db:
        record = db.query(EmailCode).one()
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    assert client.post(
        "/api/v1/auth/verify-email", json={"email": EMAIL, "code": code_for(mailer)}
    ).status_code == 400


def test_a_resend_replaces_the_old_code(client, mailer):
    register(client)
    first = code_for(mailer)
    allow_resend()

    client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})
    second = code_for(mailer)

    assert len(mailer.messages) == 2
    if first != second:
        assert client.post(
            "/api/v1/auth/verify-email", json={"email": EMAIL, "code": first}
        ).status_code == 400
    assert client.post(
        "/api/v1/auth/verify-email", json={"email": EMAIL, "code": second}
    ).status_code == 200


def test_resends_are_rate_limited(client, mailer):
    register(client)
    for _ in range(3):
        client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})
    assert len(mailer.messages) == 1, "a second code within 60 seconds is not sent"


def test_resend_does_not_reveal_whether_an_account_exists(client, mailer):
    register(client)
    allow_resend()
    known = client.post("/api/v1/auth/verify-email/resend", json={"email": EMAIL})
    unknown = client.post(
        "/api/v1/auth/verify-email/resend", json={"email": "nobody@uni.example.com"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()


def test_organisation_sign_up_is_verified_too(client, mailer):
    response = client.post(
        "/api/v1/auth/register-organization",
        json={"organization_name": "Acme", "email": "boss@acme.example.com", "password": PASSWORD},
    )
    assert response.status_code == 201
    assert login(client, email="boss@acme.example.com").status_code == 403
    assert client.post(
        "/api/v1/auth/verify-email",
        json={"email": "boss@acme.example.com", "code": code_for(mailer, "boss@acme.example.com")},
    ).status_code == 200


def test_accounts_created_directly_count_as_verified(client):
    """Manager-created employees and seeded accounts sign in with no code."""
    with get_session_factory()() as db:
        db.add(User(email="emp@acme.example.com", password_hash=hash_password(PASSWORD),
                    role="STUDENT", is_active=True))
        db.commit()
    assert login(client, email="emp@acme.example.com").status_code == 200


def test_verification_can_be_switched_off(client, mailer, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION", "0")
    register(client)
    assert mailer.messages == []
    assert login(client).status_code == 200


# --- password reset -----------------------------------------------------------------


def _verified_account(email: str = EMAIL) -> None:
    with get_session_factory()() as db:
        db.add(User(email=email, password_hash=hash_password(PASSWORD), role="STUDENT",
                    is_active=True))
        db.commit()


def test_forgot_password_emails_a_reset_code_and_the_reset_works(client, mailer):
    _verified_account()
    old_session = login(client).json()["refresh_token"]

    response = client.post("/api/v1/auth/password/forgot", json={"email": EMAIL})
    assert response.status_code == 200
    assert "reset code" in mailer.messages[-1].subject

    reset = client.post(
        "/api/v1/auth/password/reset",
        json={"email": EMAIL, "code": code_for(mailer), "new_password": NEW_PASSWORD},
    )
    assert reset.status_code == 200

    assert login(client).status_code == 401, "the old password no longer works"
    assert login(client, NEW_PASSWORD).status_code == 200
    # Every earlier session was signed out.
    assert client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_session}
    ).status_code == 401


def test_forgot_password_does_not_reveal_whether_an_account_exists(client, mailer):
    _verified_account()
    known = client.post("/api/v1/auth/password/forgot", json={"email": EMAIL})
    unknown = client.post("/api/v1/auth/password/forgot", json={"email": "ghost@uni.example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert len(mailer.messages) == 1, "only the real account got an email"


def test_a_verification_code_cannot_reset_a_password(client, mailer):
    register(client)
    verify_code = code_for(mailer)
    response = client.post(
        "/api/v1/auth/password/reset",
        json={"email": EMAIL, "code": verify_code, "new_password": NEW_PASSWORD},
    )
    assert response.status_code == 400


def test_a_weak_new_password_is_refused_before_the_code_is_spent(client, mailer):
    _verified_account()
    client.post("/api/v1/auth/password/forgot", json={"email": EMAIL})
    code = code_for(mailer)

    weak = client.post(
        "/api/v1/auth/password/reset",
        json={"email": EMAIL, "code": code, "new_password": "        "},
    )
    assert weak.status_code == 422
    assert client.post(
        "/api/v1/auth/password/reset",
        json={"email": EMAIL, "code": code, "new_password": NEW_PASSWORD},
    ).status_code == 200


def test_an_agent_only_employee_cannot_reset_into_a_portal_login(client, mailer):
    with get_session_factory()() as db:
        db.add(User(email="agent@acme.example.com", password_hash=NO_LOGIN_HASH,
                    role="EMPLOYEE", is_active=True))
        db.commit()
    client.post("/api/v1/auth/password/forgot", json={"email": "agent@acme.example.com"})
    assert mailer.messages == []


def test_a_reset_also_verifies_the_email(client, mailer):
    register(client)
    allow_resend()
    client.post("/api/v1/auth/password/forgot", json={"email": EMAIL})
    reset_code = code_for(mailer)
    client.post(
        "/api/v1/auth/password/reset",
        json={"email": EMAIL, "code": reset_code, "new_password": NEW_PASSWORD},
    )
    assert login(client, NEW_PASSWORD).status_code == 200


# --- storage and delivery ---------------------------------------------------------


def test_codes_are_stored_hashed_and_never_logged(client, mailer, caplog):
    caplog.set_level(logging.DEBUG)
    register(client)
    code = code_for(mailer)

    with get_session_factory()() as db:
        stored = db.query(EmailCode).one()
    assert code not in stored.code_hash
    assert code not in caplog.text


def test_without_smtp_the_email_is_written_to_the_outbox(tmp_path):
    OutboxMailer(tmp_path).send(
        email_codes.code_email("a@b.example.com", "123456", "VERIFY_EMAIL", 15)
    )
    files = list(tmp_path.glob("*.eml"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "123 456" in body and "a@b.example.com" in body


def test_switching_verification_off_unlocks_accounts_still_waiting_for_a_code(
    client, mailer, monkeypatch
):
    register(client)  # signed up while verification was on; never entered the code
    assert login(client).status_code == 403

    monkeypatch.setenv("FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION", "0")
    assert login(client).status_code == 200
