"""Pydantic contracts for everything the desktop agent uploads.

Two properties of these models matter more than the field lists:

1. Every event carries a client-generated ``event_uuid``. The agent creates it
   before the event is written to the local queue, so an upload that times out
   ambiguously can always be retried safely - the server's unique constraint
   rejects the duplicate. This is the whole of the section 37 idempotency design.

2. No model here can carry a video frame, a screenshot or keystroke text. The
   agent uploads *derived observations* only (spec sections 33, 34). The schema is
   therefore itself a privacy control, not merely a data-transfer format.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from focusguard_shared.constants import (
    ActivityCategory,
    Classification,
    GazeDirection,
    HeadPose,
    RuleSource,
    VisionEventType,
)


class _StrictModel(BaseModel):
    """Base model that rejects unknown fields.

    Ingest endpoints accept data from agents that may be running an older or
    newer version. Failing loudly on unexpected fields is preferable to silently
    discarding data that a future version believes it is sending.
    """

    model_config = ConfigDict(extra="forbid", frozen=False)


class TimeSpan(_StrictModel):
    """A closed time interval, validated so it can never be negative."""

    start_time: datetime
    end_time: datetime

    @model_validator(mode="after")
    def _check_order(self) -> TimeSpan:
        if self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_seconds(self) -> float:
        return (self.end_time - self.start_time).total_seconds()


class VisionEvent(_StrictModel):
    """A completed behavioural event from the webcam pipeline (spec section 17).

    Emitted only after the triggering condition has survived its grace period, so
    an instance of this class always represents sustained evidence rather than a
    single frame.
    """

    event_uuid: uuid_module.UUID = Field(default_factory=uuid_module.uuid4)
    session_id: str
    device_id: str | None = None

    event_type: VisionEventType
    start_time: datetime
    end_time: datetime
    duration_seconds: float = Field(ge=0.0)

    #: Mean confidence across the event's active window - deliberately the mean
    #: rather than the peak, because peaks overstate how certain we were.
    confidence: float = Field(ge=0.0, le=1.0)

    metadata: dict[str, float | int | str | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_span(self) -> VisionEvent:
        if self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")
        return self


class VisionFrameObservation(_StrictModel):
    """Per-frame detector output, consumed by the event engine.

    This is an in-process structure and is never uploaded: it exists at camera
    frame rate and would violate section 33 if transmitted. It is defined here
    only so the agent's detectors and event engine agree on a shape, and so the
    scoring tests can construct realistic input without a camera.
    """

    timestamp: float  # monotonic seconds

    camera_ok: bool = True
    face_count: int = 0

    ear_left: float | None = None
    ear_right: float | None = None
    ear_average: float | None = None
    eyes_closed: bool = False

    yaw: float | None = None
    pitch: float | None = None
    roll: float | None = None
    head_pose: HeadPose = HeadPose.UNKNOWN

    gaze_direction: GazeDirection = GazeDirection.UNKNOWN
    gaze_confidence: float = 0.0

    phone_detected: bool = False
    phone_confidence: float = 0.0

    @property
    def face_present(self) -> bool:
        """True only when the camera is healthy AND a face was found.

        A dead camera reports zero faces, which must not be read as an absent
        user. Every consumer should use this property rather than testing
        ``face_count`` directly.
        """
        return self.camera_ok and self.face_count > 0


class ActivityEvent(_StrictModel):
    """A span of time spent in one foreground application (spec sections 19, 35).

    Stores the application identity and duration. It does not and cannot store
    screen contents.
    """

    event_uuid: uuid_module.UUID = Field(default_factory=uuid_module.uuid4)
    session_id: str
    device_id: str | None = None

    process_name: str  # e.g. "Code.exe"
    application_name: str  # normalised, e.g. "Visual Studio Code"

    #: Optional and disableable by the user. Window titles can leak document
    #: names, so this is opt-in rather than collected by default.
    window_title: str | None = None

    category: ActivityCategory = ActivityCategory.OTHER
    classification: Classification = Classification.NEUTRAL
    rule_source: RuleSource = RuleSource.UNCLASSIFIED

    start_time: datetime
    end_time: datetime
    duration_seconds: float = Field(ge=0.0)


class WebsiteUsage(_StrictModel):
    """A span of time spent on one domain (spec sections 20, 21).

    The URL is minimised inside the browser extension *before* it is sent to the
    agent, so a full URL with its query string never crosses a process boundary.
    ``path_prefix`` holds at most a coarse first segment, e.g. "/watch".
    """

    event_uuid: uuid_module.UUID = Field(default_factory=uuid_module.uuid4)
    session_id: str
    device_id: str | None = None

    domain: str = Field(min_length=1, max_length=253)
    path_prefix: str | None = Field(default=None, max_length=64)
    page_title: str | None = None

    category: ActivityCategory = ActivityCategory.OTHER
    classification: Classification = Classification.NEUTRAL
    rule_source: RuleSource = RuleSource.UNCLASSIFIED

    start_time: datetime
    end_time: datetime
    duration_seconds: float = Field(ge=0.0)


class FocusScoreSample(_StrictModel):
    """One point on the focus timeline (spec sections 18, 41).

    Written at roughly 1 Hz, not per frame: a frame-rate score series would add
    millions of rows for no analytical gain, which section 45 explicitly rules out.
    Attention and productivity are stored separately as well as combined, because
    a user can be visually attentive while doing entirely the wrong thing
    (spec section 59).
    """

    session_id: str
    timestamp: datetime

    visual_attention_score: float = Field(ge=0.0, le=100.0)
    digital_productivity_score: float = Field(ge=0.0, le=100.0)
    combined_focus_score: float = Field(ge=0.0, le=100.0)

    #: Event types active at this instant, so the UI can explain a dip without
    #: re-deriving it by joining against the event tables.
    active_events: list[VisionEventType] = Field(default_factory=list)


class EventBatch(_StrictModel):
    """A batch upload from the agent (spec sections 33, 37).

    Batching is what keeps the design honest: the agent transmits at most a few
    times a minute, and never streams.
    """

    device_id: str
    session_id: str
    agent_version: str

    vision_events: list[VisionEvent] = Field(default_factory=list)
    activity_events: list[ActivityEvent] = Field(default_factory=list)
    website_usage: list[WebsiteUsage] = Field(default_factory=list)
    focus_scores: list[FocusScoreSample] = Field(default_factory=list)

    @property
    def total_records(self) -> int:
        return (
            len(self.vision_events)
            + len(self.activity_events)
            + len(self.website_usage)
            + len(self.focus_scores)
        )
