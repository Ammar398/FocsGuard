"""Behavioural event vocabulary produced by the vision pipeline.

Design note (spec sections 17, 57, 61): detectors never emit these directly.
A detector reports a per-frame observation with a confidence; the event engine
applies a grace period and only then produces one of these types, carrying a
start time, an end time and a duration. That separation is what keeps a single
noisy frame from ever becoming a recorded distraction.
"""

from __future__ import annotations

from enum import StrEnum


class VisionEventType(StrEnum):
    """Discrete attention events derived from the webcam pipeline."""

    FACE_PRESENT = "FACE_PRESENT"
    FACE_ABSENT = "FACE_ABSENT"
    MULTIPLE_FACES = "MULTIPLE_FACES"

    BLINK = "BLINK"
    EYES_CLOSED = "EYES_CLOSED"
    DROWSINESS = "DROWSINESS"

    HEAD_DOWN = "HEAD_DOWN"
    GAZE_AWAY = "GAZE_AWAY"

    PHONE_VISIBLE = "PHONE_VISIBLE"

    FOCUSED = "FOCUSED"

    # Not a behaviour. Raised when the camera stops delivering frames - the phone
    # locked, Wi-Fi dropped, or Camo/DroidCam was closed. This MUST stay distinct
    # from FACE_ABSENT: a user is not distracted because their camera died, and
    # scoring must never penalise this state (see CURRENT_STATE.md section 2.4).
    CAMERA_UNAVAILABLE = "CAMERA_UNAVAILABLE"


#: Events that reduce the visual attention score. Everything else is neutral or
#: positive. Kept as an explicit set so scoring can never accidentally penalise
#: a diagnostic state such as CAMERA_UNAVAILABLE.
PENALISING_VISION_EVENTS: frozenset[VisionEventType] = frozenset(
    {
        VisionEventType.FACE_ABSENT,
        VisionEventType.MULTIPLE_FACES,
        VisionEventType.EYES_CLOSED,
        VisionEventType.DROWSINESS,
        VisionEventType.HEAD_DOWN,
        VisionEventType.GAZE_AWAY,
        VisionEventType.PHONE_VISIBLE,
    }
)


class EventState(StrEnum):
    """State of the generic event state machine (see ARCHITECTURE_PLAN.md 4.5).

    IDLE -> CANDIDATE -> ACTIVE. An event is only emitted if the triggering
    condition survives its configured grace period, which is what implements the
    "0.5 seconds of looking away must not hurt the score" requirement.
    """

    IDLE = "IDLE"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"


class HeadPose(StrEnum):
    """Coarse head orientation classification (spec section 14)."""

    CENTER = "CENTER"
    LOOKING_LEFT = "LOOKING_LEFT"
    LOOKING_RIGHT = "LOOKING_RIGHT"
    LOOKING_UP = "LOOKING_UP"
    LOOKING_DOWN = "LOOKING_DOWN"
    UNKNOWN = "UNKNOWN"


class GazeDirection(StrEnum):
    """Coarse gaze classification.

    Webcam gaze estimation is genuinely imprecise, so every gaze result carries a
    separate confidence value and UNKNOWN is a legitimate, frequently-used
    outcome rather than an error (spec sections 15 and 61).
    """

    SCREEN = "SCREEN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"
