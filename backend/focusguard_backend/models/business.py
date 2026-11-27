"""Projects and tasks (spec sections 24, 28, 29).

WHAT IS STORED ON A TASK
------------------------
What the manager decided (title, category, priority, deadline, estimate, the
productive / neutral / distracting rules), who it is assigned to, and how it
ended (status, completion time, quality rating, rework count). The *monitoring*
of a task is not here: it is the focus sessions that point at it through
`focus_sessions.task_id`, so a task session gets the whole telemetry machinery -
events, scores, idempotent upload, retention - without a second copy of it.

The as-built simplifications (one assignee per task, rules as JSON lists rather
than a rules table) are explained in `ARCHITECTURE_PLAN.md` section 14.

ORGANIZATION_ID ON BOTH
-----------------------
As on every tenant table (see `models/base.py`): the isolation filter stays one
indexed predicate, and a repository method cannot be written without it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from focusguard_backend.models.base import Base, TimestampMixin
from focusguard_backend.models.identity import User


class Project(Base, TimestampMixin):
    """A group of related tasks within an organisation."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tasks: Mapped[list[Task]] = relationship(back_populates="project")


class Task(Base, TimestampMixin):
    """One piece of assigned work."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    #: A `TaskCategory`. Strength analytics only ever compares within one.
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="MEDIUM")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="TODO", index=True)

    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_minutes: Mapped[int | None] = mapped_column(Integer)

    assignee_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    #: Application names or domains, matched case-insensitively by the agent's
    #: `TaskContext`. Read whole, so a list column rather than a rules table.
    productive_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    neutral_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    distracting_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    #: A weekly work time: on these weekdays (0 = Monday) at `schedule_time`
    #: ("HH:MM", the employee's own clock) the employee's computer starts this
    #: task's session by itself for `schedule_minutes`. The employee can still
    #: close it. Nullable so the columns can be added to an existing database.
    schedule_days: Mapped[list | None] = mapped_column(JSON)
    schedule_time: Mapped[str | None] = mapped_column(String(5))
    schedule_minutes: Mapped[int | None] = mapped_column(Integer)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: 1-5, entered by the manager at or after completion. The heaviest single
    #: input to strength analytics (section 30), because it is the one measure of
    #: the work itself rather than of the circumstances it was done in.
    quality_rating: Mapped[float | None] = mapped_column(Float)
    rework_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])

    __table_args__ = (
        Index("ix_tasks_org_status", "organization_id", "status"),
        Index("ix_tasks_assignee_category", "assignee_id", "category"),
    )


class FocusAlert(Base, TimestampMixin):
    """Something about an employee's office-hours session the manager should see.

    Today one kind: CLOSED_EARLY - the employee closed the session that started
    by itself for their office hours, before the hours ended. The manager can
    dismiss it, or ask the employee's computer to start the session again
    (`restart_requested_at`); the computer picks that up and marks it resolved.
    The employee is asked on their computer as for any start, and may close it
    again.
    """

    __tablename__ = "focus_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    #: When the office hours that were cut short end.
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restart_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
