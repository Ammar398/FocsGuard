"""Moving a connected computer to a different account.

The bug this covers: a computer connected to one account could never be
connected to another - enrolment refused with 409 because the computer's
identifier was taken. A person who signed up after the demo account had used
their machine was simply stuck.

The fix must not open a hole: knowing a machine's identifier (it sits in a plain
file) must not be enough to take it over. Only the machine's current device
secret, which never leaves it, proves the request comes from that computer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.auth.tokens import create_access_token, hash_token
from focusguard_backend.database.session import (
    create_all,
    get_engine,
    get_session_factory,
    reset_engine,
)
from focusguard_backend.models import Device, User

MACHINE = "machine-uuid-0001"
OLD_SECRET = "old-device-secret"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "move.db"))
    reset_engine()
    from focusguard_backend.main import app

    create_all(get_engine())
    with get_session_factory()() as db:
        old_owner = User(email="demo@uni.example.com", password_hash="x", role="STUDENT")
        new_owner = User(email="me@uni.example.com", password_hash="x", role="STUDENT")
        db.add_all([old_owner, new_owner])
        db.flush()
        db.add(Device(device_uuid=MACHINE, device_name="Laptop", user_id=old_owner.id,
                      secret_hash=hash_token(OLD_SECRET), is_active=True))
        db.commit()
        new_id = new_owner.id
        token = create_access_token(user_id=new_id, email=new_owner.email, role="STUDENT")

    with TestClient(app) as client:
        yield {"client": client, "headers": {"Authorization": f"Bearer {token}"}, "new_id": new_id}
    reset_engine()


def _enrolment_token(world) -> str:
    return world["client"].post(
        "/api/v1/devices", json={"device_name": "My laptop"}, headers=world["headers"]
    ).json()["enrollment_token"]


def _enrol(world, **extra):
    return world["client"].post(
        "/api/v1/devices/enroll",
        json={"enrollment_token": _enrolment_token(world), "device_uuid": MACHINE, **extra},
    )


def test_a_connected_computer_can_move_with_proof(world):
    response = _enrol(world, replace_device_secret=OLD_SECRET)

    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == world["new_id"]
    new_secret = response.json()["device_secret"]

    with get_session_factory()() as db:
        devices = db.query(Device).order_by(Device.id).all()
        old, new = devices[0], devices[-1]
        assert old.revoked_at is not None and old.secret_hash is None, "old link retired"
        assert old.device_uuid != MACHINE
        assert new.device_uuid == MACHINE and new.user_id == world["new_id"]

    # The old credential no longer uploads; the new one does authenticate.
    old_call = world["client"].post(
        "/api/v1/agent/heartbeat", json={"device_uuid": MACHINE},
        headers={"X-Device-Secret": OLD_SECRET},
    )
    assert old_call.status_code == 401
    new_call = world["client"].post(
        "/api/v1/agent/heartbeat", json={"device_uuid": MACHINE},
        headers={"X-Device-Secret": new_secret},
    )
    assert new_call.status_code == 204


def test_knowing_the_identifier_alone_cannot_take_a_computer_over(world):
    assert _enrol(world).status_code == 409
    assert _enrol(world, replace_device_secret="a-guess").status_code == 409

    with get_session_factory()() as db:
        old = db.query(Device).filter(Device.device_uuid == MACHINE).one()
        assert old.revoked_at is None, "the existing connection is untouched"


def test_a_revoked_computer_can_be_connected_again_without_proof(world):
    with get_session_factory()() as db:
        old = db.query(Device).filter(Device.device_uuid == MACHINE).one()
        old.revoked_at = old.created_at
        old.secret_hash = None
        db.commit()

    assert _enrol(world).status_code == 200
