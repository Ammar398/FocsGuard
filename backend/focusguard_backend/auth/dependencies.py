"""Authentication and authorisation dependencies (spec section 51).

This is the one place that answers *who is calling* and *what may they see*.
Every protected route depends on something here, so the rules live in one file
rather than being restated per endpoint.

THE SCOPE COMES FROM THE TOKEN, NEVER FROM THE REQUEST
------------------------------------------------------
In Phase 12 `user_id` arrived as a query parameter - a placeholder, and an
obvious hole. Now it comes from a **signed claim**. A caller can ask for another
user's data all they like; the scope they get is the one their token asserts.

That is what makes section 51 hold: tenant isolation is not a filter someone
remembered to write, it is derived from a credential the caller cannot forge.

ROLE CHECKS ARE EXPLICIT
------------------------
`require_role(UserRole.MANAGER)` reads as what it enforces. A route with no role
dependency is unauthenticated **by omission**, which is easy to miss in review -
so protected routers declare their dependency at the router level, not per route.

DEVICES ARE NOT PEOPLE
----------------------
A device credential authenticates an agent, and grants exactly one power: write
its own events. It cannot read anything about anybody. A stolen device secret
should let an attacker post nonsense telemetry, not browse an organisation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.auth.tokens import TokenError, decode_access_token, hash_token
from focusguard_backend.database.session import get_db
from focusguard_backend.models.identity import Device, User
from focusguard_backend.repositories.telemetry import Scope
from focusguard_shared.constants import UserRole
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.auth.dependencies")

#: auto_error=False so a missing header produces our own 401 with a useful
#: message, rather than FastAPI's bare 403.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Resolve the caller from their access token."""
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED

    try:
        claims = decode_access_token(credentials.credentials)
    except TokenError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
            headers={"WWW-Authenticate": "Bearer"},
        ) from error

    user = db.get(User, claims.user_id)
    if user is None or not user.is_active:
        # The token is valid but the account is gone or disabled. A deactivated
        # user must lose access immediately, not when their token expires.
        raise _UNAUTHENTICATED

    return user


def require_role(*roles: UserRole):
    """Dependency factory: allow only these roles."""
    allowed = {role.value for role in roles}

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            # 403, not 404: the caller is authenticated, so there is no existence
            # to hide - they simply may not do this.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This endpoint requires one of: {', '.join(sorted(allowed))}",
            )
        return user

    return dependency


def current_scope(user: User = Depends(get_current_user)) -> Scope:
    """What this caller may see, derived from their token.

    * A student or employee is scoped to themselves.
    * A manager is scoped to their organisation.

    A manager with no organisation would otherwise produce an unscoped query, so
    that is refused rather than silently widened.
    """
    if user.role == UserRole.MANAGER.value:
        if user.organization_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This manager account is not attached to an organisation.",
            )
        return Scope.for_organization(user.organization_id)

    return Scope.for_user(user.id)


def scope_for_employee(
    employee_id: int,
    manager: User,
    db: Session,
) -> Scope:
    """A manager's view of one employee, constrained to their organisation.

    Both filters are applied together: the organisation alone would show every
    colleague, and the user alone would work across organisations. Neither is
    sufficient by itself.
    """
    if manager.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No organisation"
        )

    employee = db.get(User, employee_id)
    if employee is None or employee.organization_id != manager.organization_id:
        # 404 rather than 403: confirming that an employee exists in another
        # company is itself a leak.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found"
        )

    return Scope(user_id=employee_id, organization_id=manager.organization_id)


# --- device authentication --------------------------------------------------


def get_current_device(
    request: Request,
    db: Session = Depends(get_db),
    x_device_secret: str | None = Header(default=None, alias="X-Device-Secret"),
) -> Device:
    """Authenticate an agent by its device secret.

    The secret is looked up by hash, so the plaintext is never stored and a
    leaked database yields no usable credentials.
    """
    secret = x_device_secret
    if not secret:
        # Also accept a bearer token, which is what most HTTP clients make easy.
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            secret = header[7:].strip()

    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint requires a device credential.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    device = db.scalar(select(Device).where(Device.secret_hash == hash_token(secret)))

    if device is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown device credential"
        )
    if device.revoked_at is not None or not device.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="This device has been revoked."
        )
    if device.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device is not linked to a user.",
        )

    device.last_seen_at = datetime.now(UTC)
    return device
