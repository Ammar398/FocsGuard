"""JWT access tokens and refresh-token handling (spec section 51).

THE TWO-TOKEN DESIGN
--------------------
* **Access token** - a JWT, short-lived (15 minutes), never stored server-side.
  It is checked by signature alone, so every request costs no database lookup.
* **Refresh token** - a long random string, stored **hashed**, exchanged for a
  new access token.

The split exists because a stateless JWT cannot be revoked. Fifteen minutes is
the longest a stolen access token stays useful; the refresh token *can* be
revoked, because it lives in a table.

ROTATION AND REUSE DETECTION
----------------------------
Each refresh consumes the old token and issues a new one. If a token that was
already used arrives again, one of two things happened: a client retried, or an
attacker is replaying a stolen token. We cannot tell which, so we assume the
worse and **revoke the user's whole token family**. The legitimate user is logged
out and must sign in again; the attacker gains nothing.

That trade is deliberate: an occasional forced re-login is a much smaller harm
than a silently persistent session hijack.

THE SIGNING KEY
---------------
Read from `JWT_SECRET_KEY`. In development a random key is generated at start-up,
which invalidates tokens on restart - inconvenient, but far better than shipping a
default secret that ends up in production, which is exactly how real systems get
compromised.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.auth.tokens")

ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.environ.get("JWT_ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.environ.get("JWT_REFRESH_TOKEN_DAYS", "30"))

_TOKEN_TYPE_ACCESS = "access"

_secret_key: str | None = None


class TokenError(Exception):
    """A token was missing, malformed, expired or not ours."""


def get_secret_key() -> str:
    """The signing key, from the environment or generated for development."""
    global _secret_key
    if _secret_key is not None:
        return _secret_key

    configured = os.environ.get("JWT_SECRET_KEY")
    if configured:
        _secret_key = configured
    else:
        # Never a hardcoded fallback. A default secret in source is how systems
        # get compromised; a random one only costs a re-login after a restart.
        _secret_key = secrets.token_urlsafe(48)
        logger.warning(
            "JWT_SECRET_KEY is not set; using a random key. "
            "Tokens will not survive a restart. Set it in production."
        )
    return _secret_key


def reset_secret_key() -> None:
    """Clear the cached key. Used by tests."""
    global _secret_key
    _secret_key = None


@dataclass(frozen=True)
class TokenClaims:
    """What an access token asserts."""

    user_id: int
    email: str
    role: str
    organization_id: int | None = None
    expires_at: datetime | None = None


def create_access_token(
    user_id: int,
    email: str,
    role: str,
    organization_id: int | None = None,
    expires_in_minutes: int | None = None,
) -> str:
    """Mint a short-lived access token.

    The organisation is carried in the token so tenant scope is resolved without
    a database round trip on every request - and, more importantly, so it comes
    from a signed claim rather than a client-supplied parameter.
    """
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=expires_in_minutes or ACCESS_TOKEN_MINUTES)

    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "org": organization_id,
        "type": _TOKEN_TYPE_ACCESS,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> TokenClaims:
    """Verify and decode an access token, or raise TokenError."""
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as error:
        raise TokenError("Token has expired") from error
    except jwt.InvalidTokenError as error:
        raise TokenError("Token is invalid") from error

    # A refresh token must never be accepted where an access token is expected:
    # it lives far longer, so confusing the two would extend a session for weeks.
    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        raise TokenError("Wrong token type")

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as error:
        raise TokenError("Token is missing a subject") from error

    return TokenClaims(
        user_id=user_id,
        email=payload.get("email", ""),
        role=payload.get("role", ""),
        organization_id=payload.get("org"),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC) if "exp" in payload else None,
    )


# --- refresh tokens ---------------------------------------------------------


def generate_refresh_token() -> str:
    """A long random string. Not a JWT: it carries no claims, only identity."""
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Hash a refresh or device token for storage.

    SHA-256 rather than Argon2 here, deliberately. These are 48 bytes of
    cryptographic randomness, not human-chosen passwords - there is no dictionary
    to attack, so the slow hash buys nothing and would make every API call
    expensive.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_token_expiry(days: int | None = None) -> datetime:
    return datetime.now(UTC) + timedelta(days=days or REFRESH_TOKEN_DAYS)


def generate_device_secret() -> str:
    """A long-lived credential for an enrolled agent.

    Not a JWT: an agent runs unattended for months, so a token it cannot refresh
    on its own must be revocable server-side. A row in a table can be revoked; a
    signed claim cannot.
    """
    return secrets.token_urlsafe(48)


def generate_enrollment_token() -> str:
    """A short, single-use token a manager hands to an employee (spec section 25)."""
    return secrets.token_urlsafe(24)
