"""Sessions, events and scores as uploaded by agents (spec sections 32, 33, 37).

IDEMPOTENCY, AGAIN
------------------
Every event table has a UNIQUE `event_uuid`, exactly as the agent's local store
does. The agent generates the UUID before writing locally, uploads with the same
value, and a retry after an ambiguous timeout is therefore harmless: the second
insert violates the constraint and is discarded.

This is the *server* half of the section 37 design. Without it, every dropped
connection would inflate a user's distraction totals.

NO FRAMES, NO PIXELS, NO KEYSTROKES
-----------------------------------
There is deliberately no column anywhere here for image data, screen contents or
input text. The schema is itself a privacy control (sections 33, 34): even a
compromised agent could not upload a screenshot, because there is nowhere to put
one.

ORGANIZATION_ID ON EVERY ROW
----------------------------
Denormalised from the session on purpose - see models/base.py. It makes the
section 51 isolation filter a single indexed predicate rather than a join the
query author has to remember.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from focusguard_backend.models.base import Base, TimestampMixin


class FocusSession(Base, TimestampMixin):
    """One monitoring session (spec section 7)."""

    __tablename__ = "focus_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)

    #: Set when the session was started against an assigned task (spec section
    #: 29). A task session is simply a focus session that knows its task; see
    #: models/business.py. Students' sessions leave it NULL.
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    task_name: Mapped[str | None] = mapped_column(String(200))
    task_category: Mapped[str | None] = mapped_column(String(40))

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agent_version: Mapped[str | None] = mapped_column(String(40))

    #: Session totals, written when the agent reports the session complete. Kept
    #: alongside the raw events so reports stay fast, and so they survive the
    #: retention prune that eventually removes those events (section 45).
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    focused_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    distracted_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    average_focus_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    #: The longest unbroken stretch with no attention event. Stored rather than
    #: derived for the same reason as the totals above: the score samples it is
    #: computed from are pruned after 30 days, and recomputing it per request
    #: would mean scanning thousands of rows to draw one card.
    longest_focus_block_seconds: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )

    #: Classified activity totals, written by the retention job just before it
    #: deletes this session's raw activity rows (section 45). NULL while the raw
    #: rows still exist - they are then the source of truth. Without these, a
    #: task finished three months ago would lose its productivity score, and with
    #: it part of the employee's strength history.
    productive_seconds: Mapped[float | None] = mapped_column(Float)
    neutral_seconds: Mapped[float | None] = mapped_column(Float)
    distracting_seconds: Mapped[float | None] = mapped_column(Float)
    raw_events_pruned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    vision_events: Mapped[list[VisionEvent]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    activity_events: Mapped[list[ActivityEvent]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_sessions_user_started", "user_id", "started_at"),
        Index("ix_sessions_org_started", "organization_id", "started_at"),
    )


class VisionEvent(Base):
    """A completed attention event (spec section 17)."""

    __tablename__ = "vision_events"

    id: Mapped[int] = mapped_column(primary_key=True)

    #: Generated by the agent. The UNIQUE constraint is what makes retries safe.
    event_uuid: Mapped[str] = mapped_column(String(64), nullable=False)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    event_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    #: Mean across the event, not the peak - a detector briefly certain should not
    #: produce a certain-looking event (spec section 61).
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    event_metadata: Mapped[dict | None] = mapped_column(JSON)

    session: Mapped[FocusSession] = relationship(back_populates="vision_events")

    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_vision_events_event_uuid"),
        Index("ix_vision_session_start", "session_id", "start_time"),
    )


class ActivityEvent(Base):
    """A span in one application or on one domain (spec sections 19, 20, 35).

    Note what is stored: an application, a domain, a category and a duration.
    Not a URL, not a query string, not a screenshot.
    """

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_uuid: Mapped[str] = mapped_column(String(64), nullable=False)

    session_id: Mapped[int] = mapped_column(
        ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    process_name: Mapped[str | None] = mapped_column(String(120))
    application_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)

    #: Opt-in and off by default; a title can name a document where an
    #: application name cannot.
    window_title: Mapped[str | None] = mapped_column(Text)

    domain: Mapped[str | None] = mapped_column(String(253), index=True)
    #: At most a coarse first path segment, e.g. "/watch". Never a full path.
    path_prefix: Mapped[str | None] = mapped_column(String(64))

    category: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    classification: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    #: WHY this verdict was reached, so the UI can explain itself later even if
    #: the rules have since changed (spec sections 9, 41).
    rule_source: Mapped[str] = mapped_column(String(30), nullable=False)

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)

    session: Mapped[FocusSession] = relationship(back_populates="activity_events")

    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_activity_events_event_uuid"),
        Index("ix_activity_session_start", "session_id", "start_time"),
    )


class FocusScore(Base):
    """One point on the focus timeline (spec sections 18, 41).

    Written at roughly 1 Hz by the agent, never per frame: a frame-rate series
    would add millions of rows for no analytical gain (section 45).
    """

    __tablename__ = "focus_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    visual_attention_score: Mapped[float] = mapped_column(Float, nullable=False)
    #: Nullable on purpose: no activity data is NOT zero productivity, and storing
    #: 0 would read as terrible work when nothing was measured at all.
    digital_productivity_score: Mapped[float | None] = mapped_column(Float)
    combined_focus_score: Mapped[float] = mapped_column(Float, nullable=False)

    active_events: Mapped[list | None] = mapped_column(JSON)

    __table_args__ = (Index("ix_scores_session_time", "session_id", "recorded_at"),)


class DeviceHeartbeat(Base):
    """Periodic liveness ping (spec section 36).

    Feeds the manager board's "working / offline" split without needing a
    WebSocket held open to every machine.
    """

    __tablename__ = "device_heartbeats"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("focus_sessions.id", ondelete="SET NULL")
    )
    pending_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
