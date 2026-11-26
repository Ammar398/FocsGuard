"""Request and response models for the v1 API (spec section 36).

These are the wire contract. They deliberately do **not** reuse the ORM models:
an API shaped by its database leaks schema decisions to clients and makes every
migration a breaking change.

`extra="forbid"` on uploads is deliberate. An agent running a newer version might
send a field this server does not know, and silently dropping it would lose data
nobody realises is missing. Failing loudly is the better error.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


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

    #: The assigned task this session was started against (spec section 29).
    #: Checked server-side: it must belong to this device's user and organisation,
    #: or the session is stored without a task rather than refused.
    task_id: int | None = Field(default=None, ge=1)

    duration_seconds: float = Field(default=0, ge=0)
    focused_seconds: float = Field(default=0, ge=0)
    distracted_seconds: float = Field(default=0, ge=0)
    average_focus_score: float = Field(default=0, ge=0, le=100)
    longest_focus_block_seconds: float = Field(default=0, ge=0)


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
    task_id: int | None = None
    task_name: str | None = None
    task_category: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: float
    focused_seconds: float
    distracted_seconds: float
    average_focus_score: float
    longest_focus_block_seconds: float = 0.0

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


# --- authentication (Phase 13) ----------------------------------------------
#
# The password fields below are write-only by construction: no response model in
# this file contains one, so there is no route that could return a hash even by
# accident.


class RegisterRequest(_Upload):
    """Create an account.

    `role` accepts STUDENT only. Employee accounts are created by a manager
    inside an organisation, so allowing self-registration as an EMPLOYEE - or as
    a MANAGER - would let anyone mint themselves a business account and, with it,
    a tenant to be scoped to.
    """

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class LoginRequest(_Upload):
    email: EmailStr
    password: str = Field(max_length=256)


class TokenPair(BaseModel):
    """What a successful login returns."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="access token lifetime in seconds")


class RefreshRequest(_Upload):
    refresh_token: str = Field(max_length=200)


class UserOut(BaseModel):
    """A user as the API describes them. Deliberately has no password field."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None = None
    role: str
    organization_id: int | None = None
    is_active: bool
    #: False until the emailed code is entered (self-registered accounts only).
    email_verified: bool = True


class MessageResponse(BaseModel):
    detail: str


# --- device enrolment (spec section 25) -------------------------------------


class EnrollmentRequest(_Upload):
    """A manager (or a student, for their own machine) prepares a device."""

    device_name: str = Field(min_length=1, max_length=200)

    #: Which user the agent will report for. Omitted means the caller themselves,
    #: which is the student case.
    user_id: int | None = None


class EnrollmentTokenOut(BaseModel):
    """Shown exactly once. The server keeps only a hash of the token."""

    device_id: int
    device_name: str
    enrollment_token: str
    expires_at: datetime
    instructions: str = (
        "Run the agent once with this token to enrol it. "
        "The token works a single time and cannot be shown again."
    )


class EnrollDeviceRequest(_Upload):
    """Sent by the agent itself, unauthenticated except for the token."""

    enrollment_token: str = Field(max_length=100)

    #: Generated by the agent on first run and stable across restarts.
    device_uuid: str = Field(min_length=8, max_length=64)
    platform: str | None = Field(default=None, max_length=60)
    agent_version: str | None = Field(default=None, max_length=40)

    #: Version of the consent notice the person accepted on this machine
    #: (spec sections 25, 62). Recorded as a `privacy_consents` row so the
    #: employee - and an auditor - can see what was agreed to and when.
    consent_version: str | None = Field(default=None, max_length=20)


class DeviceCredentialOut(BaseModel):
    """The long-lived credential an enrolled agent stores locally.

    Returned once, at enrolment. Losing it means re-enrolling, which is the right
    trade: a credential the server can re-issue on demand is a credential an
    attacker can ask for too.
    """

    device_id: int
    device_uuid: str
    device_secret: str
    user_id: int
    organization_id: int | None = None


class DeviceOut(BaseModel):
    """A device as a manager sees it. Contains no secret and no token."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_uuid: str
    device_name: str
    user_id: int | None = None
    organization_id: int | None = None
    platform: str | None = None
    agent_version: str | None = None
    last_seen_at: datetime | None = None
    enrolled_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool


# --- reports (Phase 15) -----------------------------------------------------


class DayTotalsOut(BaseModel):
    """One day on the report chart."""

    day: date
    sessions: int
    monitored_seconds: float
    focused_seconds: float
    distracted_seconds: float
    average_focus_score: float


class ReportSummary(BaseModel):
    """A window of days, with its totals (spec section 42)."""

    since: datetime
    until: datetime
    days: int

    sessions: int
    monitored_seconds: float
    focused_seconds: float
    distracted_seconds: float
    average_focus_score: float

    #: Every day in the window, including empty ones. A chart that skips them
    #: draws a flat line across a week off and makes it look like continuous work.
    daily: list[DayTotalsOut] = Field(default_factory=list)


class EvidenceOut(BaseModel):
    label: str
    value: str


class RecommendationOut(BaseModel):
    """A suggestion and the data behind it (spec section 9).

    `evidence` is required rather than optional, so there is no shape of this
    response that carries advice with its reasoning omitted.
    """

    rule_id: str
    kind: str
    priority: str
    title: str
    detail: str
    evidence: list[EvidenceOut]
