"""Device enrolment and management (spec section 25).

    POST   /api/v1/devices                 create an enrolment token (authenticated)
    POST   /api/v1/devices/enroll          redeem it for a credential (agent, unauthenticated)
    GET    /api/v1/devices                 list the caller's devices
    DELETE /api/v1/devices/{id}            revoke a device

THE THREE-STEP FLOW, AND WHY IT HAS THREE STEPS
-----------------------------------------------
1. A manager (or a student, for their own machine) creates a device row and gets
   a **short-lived, single-use enrolment token**.
2. That token is typed into the agent on the machine being enrolled.
3. The agent exchanges it for a **long-lived device secret** it stores locally.

The obvious shortcut is to hand out the long-lived secret directly. It is the
wrong shape: the enrolment token travels through a chat message, an email, or a
sticky note, and those are not confidential channels. So the thing that travels
expires in an hour and works once, and the thing that lasts never leaves the
machine it was issued to.

WHAT AN ENROLMENT TOKEN CANNOT DO
---------------------------------
Nothing but enrol. It cannot read a session, list an organisation, or authenticate
a request. Someone who intercepts one can register a machine that uploads junk
telemetry for one user - which is worth preventing, and is nothing like the
compromise a readable API credential would be.

REVOCATION IS A COLUMN, NOT A DELETION
--------------------------------------
Revoking sets `revoked_at` and leaves the row. The sessions that device already
uploaded are real history and must not vanish because a laptop was replaced, and
an audit that cannot say *when* access ended is not an audit.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import (
    DeviceCredentialOut,
    DeviceOut,
    EnrollDeviceRequest,
    EnrollmentRequest,
    EnrollmentTokenOut,
    MessageResponse,
)
from focusguard_backend.auth.dependencies import get_current_user
from focusguard_backend.auth.tokens import (
    generate_device_secret,
    generate_enrollment_token,
    hash_token,
)
from focusguard_backend.database.session import get_db
from focusguard_backend.models.base import as_utc
from focusguard_backend.models.identity import Device, User
from focusguard_shared.constants import UserRole
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.devices")

router = APIRouter(prefix="/devices", tags=["devices"])

#: An hour is long enough to walk to the machine and type it in, short enough that
#: a token left in a chat log is dead by the time anyone finds it.
ENROLLMENT_TTL = timedelta(hours=1)


def _target_user(payload_user_id: int | None, caller: User, db: Session) -> User:
    """Whose device this will be.

    A student may only enrol for themselves. A manager may enrol for anyone in
    their own organisation - and the organisation check is what stops a manager
    at Company A attaching a device to an employee at Company B (section 51).
    """
    if payload_user_id is None or payload_user_id == caller.id:
        return caller

    if caller.role != UserRole.MANAGER.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a manager can enrol a device for someone else.",
        )
    if caller.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This manager account is not attached to an organisation.",
        )

    target = db.get(User, payload_user_id)
    if target is None or target.organization_id != caller.organization_id:
        # 404, not 403: confirming that a user exists in another organisation is
        # itself a cross-tenant leak.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return target


@router.post("", response_model=EnrollmentTokenOut, status_code=status.HTTP_201_CREATED)
def create_enrollment(
    payload: EnrollmentRequest,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EnrollmentTokenOut:
    """Register a device and return its one-time enrolment token."""
    owner = _target_user(payload.user_id, caller, db)

    token = generate_enrollment_token()
    expires_at = datetime.now(UTC) + ENROLLMENT_TTL

    device = Device(
        # A placeholder until the agent presents the uuid it generated. Unique so
        # two pending enrolments cannot collide on it.
        device_uuid=f"pending-{hash_token(token)[:24]}",
        device_name=payload.device_name,
        user_id=owner.id,
        organization_id=owner.organization_id,
        enrollment_token_hash=hash_token(token),
        enrollment_expires_at=expires_at,
        is_active=True,
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    # The token is deliberately absent from this log line. A secret in a log file
    # is a secret in every backup of that log file.
    logger.info("Enrolment prepared for device %d (user %d)", device.id, owner.id)

    return EnrollmentTokenOut(
        device_id=device.id,
        device_name=device.device_name,
        enrollment_token=token,
        expires_at=expires_at,
    )


@router.post("/enroll", response_model=DeviceCredentialOut)
def enroll(payload: EnrollDeviceRequest, db: Session = Depends(get_db)) -> DeviceCredentialOut:
    """Redeem an enrolment token for a device credential.

    Unauthenticated by design: the agent has no account and no credential yet -
    obtaining one is the whole point of this call. The token is the proof.
    """
    token_hash = hash_token(payload.enrollment_token)
    device = db.scalar(select(Device).where(Device.enrollment_token_hash == token_hash))

    # One message for every failure below, so a caller cannot tell an unknown
    # token from an expired or already-used one by the reply they get.
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="This enrolment token is not valid. Ask for a new one.",
    )

    if device is None or device.enrolled_at is not None or device.revoked_at is not None:
        raise invalid

    # `as_utc` because SQLite returns this column naive; see models/base.py.
    expires_at = as_utc(device.enrollment_expires_at)
    if expires_at is None or expires_at <= datetime.now(UTC):
        raise invalid

    # Reject a uuid already claimed by a different device rather than letting two
    # installations overwrite each other's identity.
    clash = db.scalar(
        select(Device).where(
            Device.device_uuid == payload.device_uuid, Device.id != device.id
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That device identifier is already enrolled.",
        )

    secret = generate_device_secret()

    device.device_uuid = payload.device_uuid
    device.secret_hash = hash_token(secret)
    device.platform = payload.platform
    device.agent_version = payload.agent_version
    device.enrolled_at = datetime.now(UTC)

    # Single use: the token is spent the moment it works, so an intercepted copy
    # is worthless afterwards.
    device.enrollment_token_hash = None
    device.enrollment_expires_at = None

    db.commit()
    db.refresh(device)

    logger.info("Device %d enrolled for user %s", device.id, device.user_id)

    return DeviceCredentialOut(
        device_id=device.id,
        device_uuid=device.device_uuid,
        # The only time this value ever leaves the server.
        device_secret=secret,
        user_id=device.user_id,
        organization_id=device.organization_id,
    )


@router.get("", response_model=list[DeviceOut])
def list_devices(
    caller: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[DeviceOut]:
    """List devices the caller may see.

    A manager sees their organisation's; everyone else sees their own. The filter
    comes from the caller's signed role, not from a parameter they chose.
    """
    statement = select(Device)
    if caller.role == UserRole.MANAGER.value:
        if caller.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This manager account is not attached to an organisation.",
            )
        statement = statement.where(Device.organization_id == caller.organization_id)
    else:
        statement = statement.where(Device.user_id == caller.id)

    return [DeviceOut.model_validate(device) for device in db.scalars(statement)]


@router.delete("/{device_id}", response_model=MessageResponse)
def revoke_device(
    device_id: int,
    caller: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    """Revoke a device. Its uploaded history is kept."""
    device = db.get(Device, device_id)

    permitted = device is not None and (
        device.user_id == caller.id
        or (
            caller.role == UserRole.MANAGER.value
            and caller.organization_id is not None
            and device.organization_id == caller.organization_id
        )
    )
    if not permitted:
        # 404 for both "no such device" and "not yours", for the usual reason.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")

    assert device is not None  # narrowed by `permitted`

    device.revoked_at = datetime.now(UTC)
    device.is_active = False

    # The credential is destroyed, not merely flagged. A revoked device whose
    # secret still matches a row is one forgotten check away from working again.
    device.secret_hash = None
    device.enrollment_token_hash = None

    db.commit()
    logger.info("Device %d revoked by user %d", device_id, caller.id)
    return MessageResponse(detail="Device revoked. Its recorded history is kept.")
