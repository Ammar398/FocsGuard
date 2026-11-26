"""Issuing and checking emailed six-digit codes.

    issue_and_send(db, user, purpose)   -> sends a fresh code, unless rate-limited
    consume(db, user, purpose, code)    -> True once, for the right code, in time

THE RULES, AND WHY
------------------
* **15 minutes, single use.** Long enough to switch to an inbox, short enough
  that an old email is worthless.
* **5 guesses per code.** A wrong guess counts against the code, and the code
  dies at five. With a million possibilities that is 1 in 200,000 per code.
* **A new code replaces the old one.** Only the latest code works, so "resend"
  never leaves several valid codes lying around.
* **60 seconds between sends, 5 per hour.** Stops the endpoints being used to
  flood someone's inbox. The caller gets the same reply either way.
* **Hashed, bound to user and purpose.** The database holds SHA-256 of
  "user:purpose:code", so a leaked table yields no codes, and a password-reset
  code cannot verify an email address or vice versa.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from focusguard_backend.auth.tokens import hash_token
from focusguard_backend.models.auth import EmailCode
from focusguard_backend.models.base import as_utc
from focusguard_backend.models.identity import User
from focusguard_backend.services.email import code_email, get_mailer
from focusguard_shared.utilities import get_logger

logger = get_logger("backend.services.email_codes")

VERIFY_EMAIL = "VERIFY_EMAIL"
RESET_PASSWORD = "RESET_PASSWORD"

CODE_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=60)
MAX_PER_HOUR = 5


def verification_required() -> bool:
    """Self-registered accounts must confirm their email before signing in.

    On by default. `FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION=0` turns it off for a
    deployment with no mail at all - not recommended outside a classroom demo.
    """
    return os.environ.get("FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION", "1") != "0"


def _hash(user_id: int, purpose: str, code: str) -> str:
    return hash_token(f"{user_id}:{purpose}:{code}")


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_and_send(db: Session, user: User, purpose: str, now: datetime | None = None) -> bool:
    """Send a fresh code. Returns False when rate-limited (nothing was sent).

    Commits, because the code row must exist before the email that refers to it
    is sent - otherwise a fast reader could enter a code the server does not
    know about yet.
    """
    now = now or datetime.now(UTC)

    recent = list(
        db.scalars(
            select(EmailCode.created_at).where(
                EmailCode.user_id == user.id,
                EmailCode.purpose == purpose,
                EmailCode.created_at >= now - timedelta(hours=1),
            )
        )
    )
    latest = max((as_utc(moment) for moment in recent if moment is not None), default=None)
    if len(recent) >= MAX_PER_HOUR or (latest is not None and now - latest < RESEND_COOLDOWN):
        logger.info("Code for user %d (%s) not sent: rate-limited", user.id, purpose)
        return False

    # Only the newest code works: retire any still-open ones.
    db.execute(
        update(EmailCode)
        .where(
            EmailCode.user_id == user.id,
            EmailCode.purpose == purpose,
            EmailCode.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )

    code = _new_code()
    db.add(
        EmailCode(
            user_id=user.id,
            purpose=purpose,
            code_hash=_hash(user.id, purpose, code),
            expires_at=now + CODE_TTL,
            attempts=0,
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()

    try:
        get_mailer().send(
            code_email(user.email, code, purpose, int(CODE_TTL.total_seconds() // 60))
        )
    except Exception:  # noqa: BLE001
        # The account and the code exist; the person can press "send again".
        # Failing the whole request would leave them with neither.
        logger.exception("Could not send the %s email to user %d", purpose, user.id)
        return False

    logger.info("Sent a %s code to user %d", purpose, user.id)
    return True


def consume(
    db: Session, user: User, purpose: str, code: str, now: datetime | None = None
) -> bool:
    """True exactly once for the right, unexpired code. Counts wrong guesses."""
    now = now or datetime.now(UTC)
    record = db.scalar(
        select(EmailCode)
        .where(
            EmailCode.user_id == user.id,
            EmailCode.purpose == purpose,
            EmailCode.consumed_at.is_(None),
        )
        .order_by(EmailCode.created_at.desc(), EmailCode.id.desc())
        .limit(1)
    )
    if record is None:
        return False

    expires_at = as_utc(record.expires_at)
    if expires_at is None or expires_at <= now or record.attempts >= MAX_ATTEMPTS:
        return False

    cleaned = "".join(ch for ch in code if ch.isdigit())
    if not secrets.compare_digest(record.code_hash, _hash(user.id, purpose, cleaned)):
        record.attempts += 1
        db.commit()
        return False

    record.consumed_at = now
    db.flush()
    return True


def codes_sent_recently(db: Session, user: User, purpose: str) -> int:
    """For tests and diagnostics."""
    return int(
        db.scalar(
            select(func.count(EmailCode.id)).where(
                EmailCode.user_id == user.id, EmailCode.purpose == purpose
            )
        )
        or 0
    )
