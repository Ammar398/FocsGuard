"""Authentication endpoints (spec section 51).

    POST /api/v1/auth/register   create a student account
    POST /api/v1/auth/login      exchange a password for a token pair
    POST /api/v1/auth/refresh    rotate the refresh token, get a new access token
    POST /api/v1/auth/logout     revoke one session
    GET  /api/v1/auth/me         who the caller is

WHAT THE ERROR MESSAGES DO NOT SAY
----------------------------------
Registration and login both refuse with a message that does not distinguish
"no such account" from "wrong password", and registration does not confirm that
an email is already taken. Both are account-enumeration defences: an attacker who
can ask "does alice@company.com have an account here?" learns something about a
person even without ever logging in.

The timing is equalised too - `verify_password` hashes against a dummy value when
no user is found - because a reply that is fast for unknown accounts and slow for
real ones answers the same question without needing the message.

WHY REGISTRATION ONLY MAKES STUDENTS
------------------------------------
An employee belongs to an organisation, and a manager can see that organisation's
data. If self-registration could choose either role, anyone could create a manager
account - and section 51's isolation would be protecting a tenant an attacker
owns. Business accounts are created from inside an organisation instead.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from focusguard_backend.api.v1.schemas import (
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from focusguard_backend.auth.dependencies import get_current_user
from focusguard_backend.auth.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from focusguard_backend.auth.rate_limit import account_limiter, ip_limiter
from focusguard_backend.auth.tokens import (
    ACCESS_TOKEN_MINUTES,
    create_access_token,
    generate_refresh_token,
    hash_token,
    refresh_token_expiry,
)
from focusguard_backend.database.session import get_db
from focusguard_backend.models.auth import RefreshToken
from focusguard_backend.models.identity import User
from focusguard_backend.services.email_codes import (
    VERIFY_EMAIL,
    issue_and_send,
    verification_required,
)
from focusguard_shared.constants import UserRole
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.auth")

router = APIRouter(prefix="/auth", tags=["auth"])

#: One message for every failed login. See the module docstring.
_BAD_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


def _client_ip(request: Request) -> str:
    """The caller's address, for rate limiting only.

    Never used for authorisation: behind a proxy this is the proxy, and
    `X-Forwarded-For` is trivially forged. Good enough to slow a guessing run
    down, not good enough to decide who anyone is.
    """
    return request.client.host if request.client else "unknown"


def _issue_tokens(
    db: Session,
    user: User,
    request: Request,
    family_id: str | None = None,
) -> TokenPair:
    """Mint an access token and a stored refresh token."""
    refresh_value = generate_refresh_token()
    token_hash = hash_token(refresh_value)

    db.add(
        RefreshToken(
            token_hash=token_hash,
            user_id=user.id,
            # A fresh login starts its own family; a rotation stays in the family
            # it descended from, so a replay can revoke the whole chain.
            family_id=family_id or token_hash[:32],
            expires_at=refresh_token_expiry(),
            user_agent=request.headers.get("user-agent"),
            ip_address=_client_ip(request),
        )
    )

    access = create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
        organization_id=user.organization_id,
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh_value,
        expires_in=ACCESS_TOKEN_MINUTES * 60,
    )


def _revoke_family(db: Session, user_id: int, family_id: str, reason: str) -> int:
    """Revoke every live token descended from one login. Returns how many."""
    now = datetime.now(UTC)
    count = 0
    for token in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),
        )
    ):
        token.revoked_at = now
        token.revoked_reason = reason
        count += 1
    return count


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> UserOut:
    """Create a student account."""
    problem = validate_password_strength(payload.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    email = payload.email.strip().lower()
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        # 409 with a message that does not confirm the address is registered.
        # "That address cannot be used" is true either way.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That email address cannot be registered.",
        )

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.STUDENT.value,
        organization_id=None,
        is_active=True,
        # Confirmed by the six-digit code emailed below (services/email_codes).
        email_verified=not verification_required(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    if not user.email_verified:
        issue_and_send(db, user, VERIFY_EMAIL)

    logger.info("Registered a new student account (id=%d)", user.id)
    return UserOut.model_validate(user)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    """Exchange a password for a token pair."""
    email = payload.email.strip().lower()
    ip = _client_ip(request)

    # Checked before any hashing, so a blocked attempt costs no CPU. That matters
    # twice over: Argon2id is 64 MB per attempt, so unthrottled guessing is also
    # a denial-of-service against this machine.
    for limiter, key in ((ip_limiter, ip), (account_limiter, email)):
        retry_after = limiter.check(key)
        if retry_after is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed sign-in attempts. Try again shortly.",
                headers={"Retry-After": str(int(retry_after))},
            )

    user = db.scalar(select(User).where(User.email == email))

    # Called even when the user is None - it hashes a dummy value so an unknown
    # address takes the same time as a real one.
    if not verify_password(payload.password, user.password_hash if user else None):
        ip_limiter.record_failure(ip)
        account_limiter.record_failure(email)
        raise _BAD_CREDENTIALS

    if not user.is_active:
        # Same message as a wrong password: whether an account is disabled is
        # still information about whether it exists.
        raise _BAD_CREDENTIALS

    # With verification switched off, accounts that signed up while it was on
    # (and never entered their code) must not stay locked out.
    if not user.email_verified and verification_required():
        # Only reachable with the right password, so saying so leaks nothing.
        # A fresh code is sent (rate-limited) so the person can finish now.
        issue_and_send(db, user, VERIFY_EMAIL)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please verify your email address first. We have sent a code to it.",
        )

    # A correct password proves this is the owner, so their counters are cleared -
    # two typos then a success should not leave them near a lockout.
    ip_limiter.clear(ip)
    account_limiter.clear(email)

    if needs_rehash(user.password_hash):
        # The only moment the plaintext is available, so the only moment an old
        # hash can be upgraded without asking anyone to reset their password.
        user.password_hash = hash_password(payload.password)

    user.last_login_at = datetime.now(UTC)
    tokens = _issue_tokens(db, user, request)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)) -> TokenPair:
    """Rotate a refresh token.

    The old token is consumed. If a token that was already consumed arrives
    again, the whole family is revoked - see `auth/tokens.py` for why we assume
    theft rather than a retry.
    """
    token_hash = hash_token(payload.refresh_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if record is None:
        raise invalid

    now = datetime.now(UTC)

    if record.revoked_at is not None:
        # Reuse of a consumed token. Either a client retried, or a stolen token is
        # being replayed - and we cannot tell which, so we assume the worse.
        revoked = _revoke_family(db, record.user_id, record.family_id, "reuse_detected")
        db.commit()
        logger.warning(
            "Refresh token reuse detected; revoked %d token(s) in the family", revoked
        )
        raise invalid

    if not record.is_usable(now):
        raise invalid

    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise invalid

    record.revoked_at = now
    record.revoked_reason = "rotated"

    tokens = _issue_tokens(db, user, request, family_id=record.family_id)
    db.commit()
    return tokens


@router.post("/logout", response_model=MessageResponse)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> MessageResponse:
    """End one session by revoking its refresh token.

    Deliberately unauthenticated beyond the token itself: a user whose access
    token has already expired must still be able to sign out, and holding the
    refresh token is the only thing logging out needs to prove.

    The whole family goes, not just the one token - otherwise a session that had
    already rotated would survive its own logout.

    Always reports success. A logout that reported "no such token" would be a
    free oracle for testing stolen values.
    """
    token_hash = hash_token(payload.refresh_token)
    record = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if record is not None:
        _revoke_family(db, record.user_id, record.family_id, "logout")
        db.commit()

    return MessageResponse(detail="Signed out.")


@router.post("/logout-all", response_model=MessageResponse)
def logout_everywhere(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> MessageResponse:
    """Revoke every session for the caller (spec section 62).

    What a user reaches for when they think they have been compromised. Access
    tokens already issued stay valid for up to their 15-minute lifetime, which is
    the cost of stateless verification and is stated rather than hidden.
    """
    count = 0
    for record in db.scalars(
        select(RefreshToken).where(
            RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
        )
    ):
        record.revoked_at = datetime.now(UTC)
        record.revoked_reason = "logout_all"
        count += 1
    db.commit()
    return MessageResponse(detail=f"Signed out of {count} session(s).")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    """The caller's own account. The first thing a front end calls after login."""
    return UserOut.model_validate(user)
