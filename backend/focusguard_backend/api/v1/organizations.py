"""Organisation sign-up (spec sections 6, 24).

    POST /api/v1/auth/register-organization

WHY THIS IS SAFE WHEN MANAGER SELF-REGISTRATION IS NOT
------------------------------------------------------
`auth.py` refuses to let anyone register *as a manager*, because a manager role
attached to an existing organisation would hand a stranger that company's data.

This route is different in the way that matters: it creates a **new, empty
organisation** and makes the caller its first manager. The only tenant they gain
access to is the one they just made, which contains nothing but themselves.
They cannot name or join an existing organisation here; adding people to one is
done from inside it, by its manager.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.business_schemas import OrganizationRegisterRequest
from focusguard_backend.api.v1.schemas import UserOut
from focusguard_backend.auth.passwords import hash_password, validate_password_strength
from focusguard_backend.database.session import get_db
from focusguard_backend.models.identity import Department, Organization, User
from focusguard_backend.services.email_codes import (
    VERIFY_EMAIL,
    issue_and_send,
    verification_required,
)
from focusguard_shared.constants import UserRole
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.organizations")

router = APIRouter(prefix="/auth", tags=["auth"])

#: Starter departments from spec section 24, so a new manager has somewhere to
#: put people immediately. They can be renamed or deleted.
DEFAULT_DEPARTMENTS = ("Development", "Design", "Marketing", "Support")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60] or "organisation"


def unique_slug(db: Session, name: str) -> str:
    base = slugify(name)
    candidate, suffix = base, 2
    while db.scalar(select(Organization.id).where(Organization.slug == candidate)) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@router.post(
    "/register-organization", response_model=UserOut, status_code=status.HTTP_201_CREATED
)
def register_organization(
    payload: OrganizationRegisterRequest, db: Session = Depends(get_db)
) -> UserOut:
    """Create an organisation and its first manager."""
    problem = validate_password_strength(payload.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    email = payload.email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        # The same non-committal message as student registration.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address cannot be registered.",
        )

    organization = Organization(
        name=payload.organization_name.strip(),
        slug=unique_slug(db, payload.organization_name),
        is_active=True,
    )
    db.add(organization)
    db.flush()

    manager = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.MANAGER.value,
        organization_id=organization.id,
        is_active=True,
        email_verified=not verification_required(),
    )
    db.add(manager)
    db.add_all(
        Department(organization_id=organization.id, name=name) for name in DEFAULT_DEPARTMENTS
    )
    db.commit()
    db.refresh(manager)

    if not manager.email_verified:
        issue_and_send(db, manager, VERIFY_EMAIL)

    logger.info("Organisation %d created with manager %d", organization.id, manager.id)
    return UserOut.model_validate(manager)
