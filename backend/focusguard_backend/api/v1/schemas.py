"""Request and response models for the v1 API (spec section 36).

These are the wire contract. They deliberately do **not** reuse the ORM models:
an API shaped by its database leaks schema decisions to clients and makes every
migration a breaking change.

`extra="forbid"` on uploads is deliberate. An agent running a newer version might
send a field this server does not know, and silently dropping it would lose data
nobody realises is missing. Failing loudly is the better error.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Upload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VisionEventUpload(_Upload):
    """One completed attention event."""

    event_uuid: str = Field(max_length=64)
    event_type: str = Field(max_length=30)
    start_time: datetime
    end_time: datetime
    duration_seconds: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    metadata: dict | None = None


class ActivityEventUpload(_Upload):
    """One application or website span.

    There is no `url` field, and there never will be: the extension minimises to
    a domain before transmission, and `path_prefix` holds at most a coarse first
    segment (spec sections 20, 35).
    """

    event_uuid: str = Field(max_length=64)
    application_name: str = Field(max_length=200)
    process_name: str | None = Field(default=None, max_length=120)
    window_title: str | None = None
    domain: str | None = Field(default=None, max_length=253)
    path_prefix: str | None = Field(default=None, max_length=64)
    category: str = Field(max_length=30)
    classification: str = Field(max_length=20)
    rule_source: str = Field(max_length=30)
    start_time: datetime
    end_time: datetime
    duration_seconds: float = Field(ge=0)


class FocusScoreUpload(_Upload):
    """One point on the focus timeline."""

    recorded_at: datetime
    visual_attention_score: float = Field(ge=0, le=100)
    #: None when activity monitoring produced no data. Not zero - see the model.
    digital_productivity_score: float | None = Field(default=None, ge=0, le=100)
    combined_focus_score: float = Field(ge=0, le=100)
    active_events: list[str] | None = None


class SessionUpload(_Upload):
    """Session identity, sent with every batch.

    The batch carries the session rather than referencing one created earlier, so
    an agent that started offline can upload a whole finished session in one go
    without a prior round trip (spec section 37).
    """

    session_uuid: str = Field(max_length=64)
    name: str = Field(max_length=200)
    started_at: datetime
    ended_at: datetime | None = None
    task_name: str | None = Field(default=None, max_length=200)
    task_category: str | None = Field(default=None, max_length=40)

    duration_seconds: float = Field(default=0, ge=0)
    focused_seconds: float = Field(default=0, ge=0)
    distracted_seconds: float = Field(default=0, ge=0)
    average_focus_score: float = Field(default=0, ge=0, le=100)


class EventBatchUpload(_Upload):
    """What an agent POSTs to /agent/events."""

    device_uuid: str = Field(max_length=64)
    agent_version: str = Field(max_length=40)
    session: SessionUpload

    vision_events: list[VisionEventUpload] = Field(default_factory=list, max_length=500)
    activity_events: list[ActivityEventUpload] = Field(default_factory=list, max_length=500)
    focus_scores: list[FocusScoreUpload] = Field(default_factory=list, max_length=2000)


class IngestResult(BaseModel):
    """What was actually stored.

    `duplicates` is reported rather than hidden: a healthy agent retrying after a
    dropped connection will see a non-zero count, and that is success, not an
    error worth alarming anyone about.
    """

    session_id: int
    accepted: dict[str, int]
    duplicates: dict[str, int]


class HeartbeatUpload(_Upload):
    device_uuid: str = Field(max_length=64)
    agent_version: str | None = Field(default=None, max_length=40)
    session_uuid: str | None = Field(default=None, max_length=64)
    pending_events: int = Field(default=0, ge=0)


class SessionSummary(BaseModel):
    """A session as the dashboard lists it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_uuid: str
    name: str
    task_name: str | None = None
    task_category: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float
    focused_seconds: float
    distracted_seconds: float
    average_focus_score: float

    @property
    def completed(self) -> bool:
        return self.ended_at is not None


class VisionEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float
    confidence: float


class ActivityEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_name: str
    domain: str | None = None
    category: str
    classification: str
    rule_source: str
    start_time: datetime
    end_time: datetime
    duration_seconds: float


class FocusScoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_at: datetime
    visual_attention_score: float
    digital_productivity_score: float | None = None
    combined_focus_score: float
    active_events: list[str] | None = None


class SessionDetail(SessionSummary):
    """A session with its full timeline, for the detail view."""

    vision_events: list[VisionEventOut] = Field(default_factory=list)
    activity_events: list[ActivityEventOut] = Field(default_factory=list)
    focus_scores: list[FocusScoreOut] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str
