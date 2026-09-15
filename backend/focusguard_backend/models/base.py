"""SQLAlchemy base and shared column patterns (spec sections 32, 51).

TENANT ISOLATION IS BUILT IN, NOT ADDED LATER
---------------------------------------------
Section 51 is blunt: *a manager from Company A must NEVER access Company B data*.

That guarantee is far easier to keep if it is structural. Every table that belongs
to an organisation carries `organization_id` **directly**, even where it could be
reached through a join, because:

* the isolation filter becomes one indexed predicate on every query, rather than
  a join the author has to remember to write;
* a repository method can take `organization_id` as a required argument, so
  forgetting it is a TypeError rather than a data leak;
* the database itself can enforce it, instead of trusting application code.

Retrofitting this later means auditing every query ever written. Doing it now
costs one column.

STUDENTS HAVE NO ORGANISATION
-----------------------------
A student account is its own island: `organization_id` is nullable, and student
rows are scoped by `user_id` instead. The repository layer requires one or the
other, never neither.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

#: Explicit naming so Alembic generates stable, readable migration names rather
#: than database-assigned ones that differ between SQLite and PostgreSQL.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    """Timezone-aware UTC. Naive datetimes cause silent hour-scale errors."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a value that came back from the database without it.

    `DateTime(timezone=True)` is a promise PostgreSQL keeps and **SQLite does
    not**: SQLite has no timezone type, so it stores what it is given and returns
    a naive datetime. Comparing that with an aware `datetime.now(UTC)` raises
    `TypeError: can't compare offset-naive and offset-aware datetimes` - which is
    exactly the kind of bug that passes on the developer's PostgreSQL and takes
    down the SQLite deployment.

    Everything written here is UTC, so re-attaching it is a restatement of what
    was stored, not a guess. Aware values pass through untouched, so this is safe
    on either database.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """created_at / updated_at on every table (spec section 32)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
