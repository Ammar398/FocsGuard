"""Refresh tokens (spec section 51).

WHY THESE ARE STORED AT ALL
---------------------------
A JWT cannot be revoked - that is the point of it being stateless. So the
long-lived half of the session lives in a table, where signing out, a password
change, or detected theft can end it immediately.

STORED HASHED
-------------
Only a SHA-256 of the token. A leaked database therefore yields no usable
sessions, for the same reason password hashes are stored rather than passwords.

TOKEN FAMILIES
--------------
Every token records the `family_id` it descends from. Rotation issues a child;
if a token that was already used arrives again, the entire family is revoked -
see `auth/tokens.py` for why that trade is the right one.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from focusguard_backend.models.base import Base, TimestampMixin, as_utc


class RefreshToken(Base, TimestampMixin):
    """One issued refresh token."""

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: SHA-256 of the token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: Shared by every token descended from one login, so a detected replay can
    #: revoke the whole chain rather than only the token that was replayed.
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: Set when the token is rotated away or revoked. A used token is kept rather
    #: than deleted precisely so its reuse can be detected.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(60))

    #: Recorded for the user's own session list, and to make a suspicious refresh
    #: visible. Never used to make an authorisation decision - both are trivially
    #: forged and must not be treated as identity.
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(String(45))

    __table_args__ = (Index("ix_refresh_user_family", "user_id", "family_id"),)

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, now: datetime) -> bool:
        # `as_utc` because SQLite returns this column naive; see models/base.py.
        # Without it every refresh raises TypeError on a SQLite deployment.
        expires_at = as_utc(self.expires_at)
        assert expires_at is not None  # the column is NOT NULL
        return expires_at <= now

    def is_usable(self, now: datetime) -> bool:
        return not self.is_revoked and not self.is_expired(now)
