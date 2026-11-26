"""Email verification and password reset.

    POST /api/v1/auth/verify-email          {email, code}               -> signs you in
    POST /api/v1/auth/verify-email/resend   {email}                     -> sends a new code
    POST /api/v1/auth/password/forgot       {email}                     -> sends a reset code
    POST /api/v1/auth/password/reset        {email, code, new_password} -> sets the password

NO ACCOUNT ENUMERATION
----------------------
"Resend" and "forgot password" always give the same reply, whether or not the
address has an account, and whether or not a code was actually sent (it may be
rate-limited). Otherwise these two endpoints would answer "does this person
have an account here?" for anyone who asks.

A wrong, expired, used or unknown code all get one message, for the same reason.

WHY A RESET SIGNS EVERYONE OUT
------------------------------
Someone resetting a password may be doing it because they think it was stolen.
So every refresh token for the account is revoked: any session an attacker
holds ends within the 15-minute life of an access token.

A successful reset also marks the email verified - entering a code sent to the
address proves the same thing verification does.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.auth import _issue_tokens
from focusguard_backend.api.v1.schemas import MessageResponse, TokenPair
from focusguard_backend.auth.passwords import (
    NO_LOGIN_HASH,
    hash_password,
    validate_password_strength,
)
from focusguard_backend.database.session import get_db
from focusguard_backend.models.auth import RefreshToken
from focusguard_backend.models.identity import User
from focusguard_backend.services.email_codes import (
    RESET_PASSWORD,
    VERIFY_EMAIL,
    consume,
    issue_and_send,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.account")

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CODE = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail="That code is not valid or has expired. Request a new one.",
)


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmailOnly(_Request):
    email: EmailStr


class VerifyEmailRequest(_Request):
    email: EmailStr
    code: str = Field(min_length=6, max_length=12)


class ResetPasswordRequest(_Request):
    email: EmailStr
    code: str = Field(min_length=6, max_length=12)
    new_password: str = Field(min_length=8, max_length=256)


def _user(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))


@router.post("/verify-email", response_model=TokenPair)
def verify_email(
    payload: VerifyEmailRequest, request: Request, db: Session = Depends(get_db)
) -> TokenPair:
    """Confirm the address with the emailed code, and sign in."""
    user = _user(db, payload.email)
    if user is None or not user.is_active:
        raise _INVALID_CODE
    if user.email_verified:
        # Already done (a second tab, a double click). Not an error worth
        # showing; but no tokens either - signing in is what login is for.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email address is already verified. Please sign in.",
        )
    if not consume(db, user, VERIFY_EMAIL, payload.code):
        raise _INVALID_CODE

    user.email_verified = True
    user.last_login_at = datetime.now(UTC)
    tokens = _issue_tokens(db, user, request)
    db.commit()
    logger.info("User %d verified their email address", user.id)
    return tokens


@router.post("/verify-email/resend", response_model=MessageResponse)
def resend_verification(payload: EmailOnly, db: Session = Depends(get_db)) -> MessageResponse:
    user = _user(db, payload.email)
    if user is not None and user.is_active and not user.email_verified:
        issue_and_send(db, user, VERIFY_EMAIL)
    return MessageResponse(
        detail="If that address is waiting for verification, a new code is on its way. "
        "Codes can be sent once a minute."
    )


@router.post("/password/forgot", response_model=MessageResponse)
def forgot_password(payload: EmailOnly, db: Session = Depends(get_db)) -> MessageResponse:
    user = _user(db, payload.email)
    # An agent-only employee has no portal password by their manager's choice;
    # a reset must not quietly give them one.
    if user is not None and user.is_active and user.password_hash != NO_LOGIN_HASH:
        issue_and_send(db, user, RESET_PASSWORD)
    return MessageResponse(
        detail="If an account uses that address, we have sent it a code to reset the password."
    )


@router.post("/password/reset", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> MessageResponse:
    problem = validate_password_strength(payload.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    user = _user(db, payload.email)
    if user is None or not user.is_active or user.password_hash == NO_LOGIN_HASH:
        raise _INVALID_CODE
    if not consume(db, user, RESET_PASSWORD, payload.code):
        raise _INVALID_CODE

    now = datetime.now(UTC)
    user.password_hash = hash_password(payload.new_password)
    user.email_verified = True
    for token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ):
        token.revoked_at = now
        token.revoked_reason = "password_reset"
    db.commit()

    logger.info("User %d reset their password; all sessions revoked", user.id)
    return MessageResponse(detail="Your password has been changed. Sign in with the new one.")
