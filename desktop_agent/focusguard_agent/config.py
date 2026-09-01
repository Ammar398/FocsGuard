"""Central configuration for the desktop agent (spec section 47).

Every threshold in FocusGuard is declared here and nowhere else. Scattering
constants such as "0.21" or "3 seconds" through detector code makes them
impossible to tune, impossible to document and impossible to defend in a viva -
so detectors receive their thresholds and never define them.

All values can be overridden from the environment or a ``.env`` file using a
double underscore for nesting::

    FOCUSGUARD_VISION__DROWSY_DURATION_SECONDS=2.0
    FOCUSGUARD_CAMERA__DEVICE_NAME=Camo

The defaults below are *starting points chosen from the spec*, not tuned values.
Phase 2's benchmark and Phase 8's testing will revise them, and any revision
should be recorded in docs/focus_scoring.md.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from focusguard_shared.constants import MonitorProfile, VisionEventType
from focusguard_shared.utilities.paths import app_data_dir, log_dir, model_cache_dir


class CameraSettings(BaseModel):
    """Webcam capture (spec section 3).

    Defaults target a phone used as a webcam through Camo, which is this
    machine's only camera. Virtual cameras register and de-register as their host
    application starts and stops, so ``device_name`` is matched first and
    ``device_index`` is only a fallback.
    """

    #: Substring matched case-insensitively against enumerated device names,
    #: e.g. "Camo", "DroidCam", "Pixel". Empty means "use device_index".
    device_name: str = "Camo"
    device_index: int = 0

    #: DirectShow is markedly more reliable than MSMF for virtual cameras on
    #: Windows. "dshow", "msmf" or "any".
    backend: str = "dshow"

    #: Processing size, treated as a BOUNDING BOX rather than an exact shape:
    #: frames are scaled to fit inside it with their aspect ratio preserved.
    #: Distorting the image would corrupt EAR and head-pose geometry, which are
    #: measured directly from pixel distances.
    #:
    #: Many virtual cameras ignore resolution requests outright - Camo delivers
    #: 1280x720 whatever is asked for - so the downscale usually happens in
    #: software. 1280x720 fits this box as 640x360.
    frame_width: int = 640
    frame_height: int = 480
    target_fps: int = 25

    #: Requesting a one-frame buffer keeps latency from accumulating over Wi-Fi.
    buffer_size: int = 1

    #: Phone cameras may deliver rotated or mirrored frames depending on how the
    #: handset is mounted. Both are configuration, never hardcoded.
    rotate_degrees: int = 0  # 0, 90, 180 or 270
    flip_horizontal: bool = False

    #: Frames missing for longer than this raise CAMERA_UNAVAILABLE - which is
    #: explicitly NOT the same as FACE_ABSENT and is never penalised.
    unavailable_after_seconds: float = 2.0

    #: A locked phone does not stop the stream. Camo substitutes a static
    #: placeholder image and keeps sending it, so the camera looks healthy while
    #: showing nobody. Frames that stay identical for this long are treated as
    #: NO_SIGNAL and monitoring suspends, rather than being scored as absence.
    static_feed_seconds: float = 3.0

    #: Mean absolute pixel difference at or below which two frames count as
    #: identical. Zero means bit-identical, which is the conservative choice: a
    #: real sensor always carries noise, so a live feed cannot trip this, and
    #: missing a placeholder is a far better failure than wrongly discarding a
    #: genuine absence.
    static_feed_tolerance: float = 0.0

    #: A frozen feed does not always heal itself. Measured here: after the phone
    #: locked, Camo dropped to all-black frames and stopped delivering entirely -
    #: unlocking the phone did not bring the video back. Reopening the DirectShow
    #: device is what recovers it, so a feed frozen for this long is reconnected
    #: automatically rather than left blank until the user restarts the agent.
    no_signal_reconnect_seconds: float = 10.0
    reconnect_interval_seconds: float = 3.0
    open_timeout_seconds: float = 10.0


class VisionSettings(BaseModel):
    """Detection rates, thresholds and grace periods (spec sections 11-16)."""

    # -- Processing rates. Detection deliberately runs slower than capture
    # (spec section 3): landmarks at ~12 Hz and phone detection at ~3 Hz keep
    # total CPU within budget on 4 cores. Phase 2's benchmark validates these.
    landmark_fps: float = 12.0
    phone_detection_fps: float = 3.0

    # -- Face presence (spec section 11)
    face_absent_grace_seconds: float = 2.0
    multiple_faces_grace_seconds: float = 2.0

    # -- Eye aspect ratio (spec section 12)
    #: Fallback threshold used only when the user has skipped calibration.
    #: A single hardcoded value does not fit every face, which is why
    #: calibration exists and why this is clamped on both sides.
    default_ear_threshold: float = 0.21
    ear_threshold_min: float = 0.15
    ear_threshold_max: float = 0.30
    #: Personal threshold = mean_open_EAR - (k * std_open_EAR).
    ear_calibration_k: float = 2.5
    #: A closure shorter than this is a blink, not an EYES_CLOSED event.
    blink_max_duration_seconds: float = 0.4
    eyes_closed_grace_seconds: float = 1.0

    # -- Drowsiness (spec section 13). Distinct from EYES_CLOSED: it needs a
    # longer closure, or repeated slow closures, optionally with a downward head.
    drowsy_duration_seconds: float = 2.5
    drowsy_slow_blink_count: int = 3
    drowsy_slow_blink_window_seconds: float = 60.0

    #: PERCLOS - the percentage of time the eyes are closed over a rolling
    #: window. It is the most widely validated drowsiness measure in the driver
    #: fatigue literature, and it catches the pattern a single long closure
    #: misses: someone fighting sleep with many half-closures rather than one
    #: obvious one. Used to corroborate a closure, never to accuse on its own.
    perclos_window_seconds: float = 60.0
    perclos_threshold: float = 0.15

    #: Samples further apart than this are not counted towards PERCLOS. Without
    #: it, a gap while the camera was down would be scored as eyes-closed time.
    perclos_max_sample_gap_seconds: float = 1.0

    # -- Head pose (spec section 14), degrees from centre
    yaw_threshold_degrees: float = 25.0
    pitch_up_threshold_degrees: float = 20.0
    pitch_down_threshold_degrees: float = 20.0
    head_down_grace_seconds: float = 3.0

    # -- Gaze (spec section 15)
    gaze_away_grace_seconds: float = 4.0
    #: Gaze results below this confidence are reported as UNKNOWN and never
    #: produce a distraction event. Webcam gaze is imprecise and section 61
    #: forbids acting on weak evidence.
    gaze_min_confidence: float = 0.55

    # -- Phone detection (spec section 16)
    phone_confidence_threshold: float = 0.50
    phone_grace_seconds: float = 1.5
    #: Detection may flicker between frames. A gap shorter than this keeps the
    #: same event open, so one continuous appearance yields ONE event.
    phone_merge_gap_seconds: float = 2.0
    yolo_input_size: int = 320

    #: Hysteresis applied when an event ends, preventing a single recovered
    #: frame from splitting one event into several.
    event_exit_hysteresis_seconds: float = 0.5


class ScoringSettings(BaseModel):
    """Focus scoring weights and smoothing (spec sections 18, 59, 60)."""

    #: Penalty subtracted from 100 while each event is active, scaled by the
    #: event's confidence. Configurable because these are untested starting
    #: points, exactly as section 18 requires.
    event_weights: dict[VisionEventType, float] = Field(
        default_factory=lambda: {
            VisionEventType.DROWSINESS: 40.0,
            VisionEventType.PHONE_VISIBLE: 35.0,
            VisionEventType.FACE_ABSENT: 25.0,
            VisionEventType.GAZE_AWAY: 15.0,
            VisionEventType.HEAD_DOWN: 15.0,
            VisionEventType.EYES_CLOSED: 10.0,
            VisionEventType.MULTIPLE_FACES: 10.0,
        }
    )

    #: Asymmetric exponential smoothing. Falling faster than it recovers is what
    #: stops the 100 -> 20 -> 100 oscillation section 18 forbids: a distraction
    #: registers promptly, but the score has to be earned back.
    smoothing_alpha_down: float = 0.15
    smoothing_alpha_up: float = 0.04

    #: Neutral activity counts as half-productive. Slack and email are real work,
    #: and scoring them as distraction would be unfair and would push users
    #: toward gaming the metric.
    neutral_activity_credit: float = 0.5

    #: Final combination of the two independent axes (spec section 59).
    visual_attention_weight: float = 0.5
    digital_productivity_weight: float = 0.5

    rolling_window_seconds: float = 60.0
    score_sample_interval_seconds: float = 1.0

    #: Multi-monitor handling (spec section 60). With a second screen, looking
    #: away from the webcam is often still work, so the gaze-away penalty is
    #: scaled down and suppressed entirely while the foreground app is productive.
    monitor_profile: MonitorProfile = MonitorProfile.SINGLE
    multi_monitor_gaze_penalty_scale: float = 0.4
    suppress_gaze_penalty_when_productive: bool = True


class ActivitySettings(BaseModel):
    """Foreground application and website monitoring (spec sections 19, 20)."""

    poll_interval_seconds: float = 1.0

    #: Spans shorter than this are discarded: alt-tabbing through windows would
    #: otherwise fill the database with meaningless one-second rows.
    min_span_seconds: float = 3.0

    #: Window titles can leak document and file names, so they are opt-in.
    #: Application names and durations are collected either way.
    collect_window_titles: bool = False

    #: The browser extension posts here. Bound to loopback only - it must never
    #: be reachable from another machine.
    ipc_host: str = "127.0.0.1"
    ipc_port: int = 48213

    #: Keep only the first path segment of a URL ("/watch"), never the full path.
    keep_url_path_prefix: bool = True

    #: Treat the user as idle after this long with no input activity, so time
    #: spent away from the desk is not counted as application usage.
    idle_threshold_seconds: float = 120.0


class PrivacySettings(BaseModel):
    """Privacy controls (spec sections 34, 35, 62).

    These are switches over behaviour that already does not exist. The agent
    imports no keyboard, clipboard or screen-capture library at all, so there is
    no code path any setting could enable - tests/test_privacy_imports.py asserts
    that and fails the build if such an import is ever added.
    """

    #: Frames are held in memory and discarded. Enabling this writes preview
    #: frames to disk, and it exists only for debugging the pipeline.
    store_webcam_frames: bool = False
    require_consent_before_monitoring: bool = True
    show_monitoring_indicator: bool = True

    #: Retention for fine-grained events, after which they roll up into daily
    #: summaries and the raw rows are deleted (spec section 45).
    raw_event_retention_days: int = 30
    summary_retention_days: int = 3650


class SyncSettings(BaseModel):
    """Offline queue and batched upload (spec sections 33, 37)."""

    backend_url: str = "http://127.0.0.1:8000"
    api_prefix: str = "/api/v1"

    enabled: bool = False  # the backend does not exist until Phase 12
    batch_size: int = 50
    upload_interval_seconds: float = 30.0
    max_retries: int = 5
    retry_backoff_seconds: float = 5.0
    request_timeout_seconds: float = 15.0


class AgentSettings(BaseSettings):
    """Root settings object for the desktop agent."""

    model_config = SettingsConfigDict(
        env_prefix="FOCUSGUARD_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_version: str = "0.1.0"
    log_level: str = "INFO"
    debug_overlay: bool = False

    camera: CameraSettings = Field(default_factory=CameraSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    activity: ActivitySettings = Field(default_factory=ActivitySettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)

    #: Forces a specific ONNX Runtime provider, bypassing auto-selection. Used to
    #: verify the CPU fallback path works on a machine that has a GPU.
    force_provider: str | None = None

    @property
    def data_dir(self) -> Path:
        return app_data_dir()

    @property
    def model_dir(self) -> Path:
        return model_cache_dir()

    @property
    def log_dir(self) -> Path:
        return log_dir()


_settings: AgentSettings | None = None


def get_settings(reload: bool = False) -> AgentSettings:
    """Return the process-wide settings object, loading it once."""
    global _settings
    if _settings is None or reload:
        _settings = AgentSettings()
    return _settings
