"""Gaze estimation (spec section 15).

THE HONEST POSITION
-------------------
The specification is explicit: *"Do not pretend webcam gaze tracking is perfectly
accurate. Expose confidence."*

Proper gaze tracking uses infrared illumination and corneal reflections. This has
a phone camera at 640x360, where the iris is roughly 15 pixels across. A single
pixel of landmark error is several degrees of gaze. The output is a coarse
direction with a confidence, never a screen coordinate, and `UNKNOWN` is a normal
result rather than a failure.

HOW IT WORKS
------------
Gaze is where the head points plus where the eyes point within it:

1. **Head direction** dominates. Turning to look at something moves the head far
   more than the eyes.
2. **Iris offset** within the eye opening. MediaPipe's 478-point model gives iris
   centres (468 and 473), and the offset from the eye's own centre, divided by the
   eye's half-width, gives a signed ratio that is independent of face size.
3. The two are combined, weighted, and compared against the blendshapes
   (`eyeLookIn/Out/Up/Down`) as an independent cross-check.

WHY CONFIDENCE COLLAPSES SO OFTEN
---------------------------------
* **Eyes closing** - a half-closed eye hides the iris, so its centre becomes a
  guess. Any closure makes gaze unusable.
* **A small face** - at distance the iris spans a handful of pixels.
* **Large head yaw** - one iris is foreshortened into an ellipse.
* **Signals disagreeing** - geometry and blendshapes reaching different answers.

Below `gaze_min_confidence` the result is reported `UNKNOWN` and no distraction
event can be raised from it. It is far better to say "we could not tell" than to
accuse a user of looking away on a 15-pixel measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from focusguard_agent.vision.eye_analysis import (
    LEFT_EYE_EAR_POINTS,
    RIGHT_EYE_EAR_POINTS,
)
from focusguard_agent.vision.head_pose import HeadPoseResult
from focusguard_agent.vision.landmarks import FaceResult
from focusguard_shared.constants import GazeDirection, HeadPose

#: Iris centres. Present only in the 478-landmark model.
LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473

#: Blendshapes describing where the eyes point, used as a cross-check.
_BLENDSHAPE_LOOK_DIRECTIONS = (
    "eyeLookInLeft",
    "eyeLookOutLeft",
    "eyeLookUpLeft",
    "eyeLookDownLeft",
)

#: How much head direction versus eye offset contributes to the final estimate.
#: Head dominates because looking away is mostly a head movement.
_HEAD_WEIGHT = 0.7
_EYE_WEIGHT = 0.3

#: Combined score beyond which the user is judged to be looking off-screen.
#: Dimensionless: 1.0 is roughly "fully turned away".
_AWAY_THRESHOLD = 0.35

#: Yaw at which the head alone is considered fully away.
_YAW_FULL_SCALE = 45.0
_PITCH_FULL_SCALE = 35.0

#: An eye narrower than this in pixels cannot give a usable iris position.
_MIN_EYE_WIDTH_PX = 12.0

_BASE_CONFIDENCE = 0.85


@dataclass(frozen=True)
class GazeResult:
    """Coarse gaze direction with an explicit confidence."""

    direction: GazeDirection
    confidence: float

    #: Signed, dimensionless. Negative left, positive right, from the user's view.
    horizontal: float = 0.0
    #: Negative down, positive up.
    vertical: float = 0.0

    #: Iris offset alone, before the head contribution.
    iris_horizontal: float = 0.0

    valid: bool = False

    #: Set when confidence fell below the configured minimum, in which case
    #: `direction` is UNKNOWN and nothing may be concluded from this frame.
    low_confidence: bool = False

    @property
    def looking_at_screen(self) -> bool:
        """True only for a confident on-screen reading.

        UNKNOWN deliberately returns False here but must not be treated as
        looking away either - callers check `valid` and `low_confidence` first.
        """
        return self.direction is GazeDirection.SCREEN


def _iris_offset(
    points_px: np.ndarray,
    iris_index: int,
    eye_indices: tuple[int, ...],
) -> float | None:
    """Signed iris offset within one eye, -1 (inner) to +1 (outer), or None.

    Divides by the eye's half-width so the value does not change with distance
    from the camera.
    """
    if len(points_px) <= max(iris_index, max(eye_indices)):
        return None

    outer = points_px[eye_indices[0]][:2]
    inner = points_px[eye_indices[3]][:2]

    width = float(abs(inner[0] - outer[0]))
    if width < _MIN_EYE_WIDTH_PX:
        return None

    centre_x = (outer[0] + inner[0]) / 2.0
    iris_x = points_px[iris_index][0]

    return float((iris_x - centre_x) / (width / 2.0))


def estimate_gaze(
    face: FaceResult,
    head_pose: HeadPoseResult,
    frame_width: int,
    frame_height: int,
    eyes_closed: bool = False,
    min_confidence: float = 0.55,
) -> GazeResult:
    """Estimate coarse gaze direction.

    Args:
        eyes_closed: when True the iris is hidden and gaze cannot be measured at
            all, so the result is UNKNOWN regardless of anything else.
    """
    if eyes_closed:
        return GazeResult(
            direction=GazeDirection.UNKNOWN,
            confidence=0.0,
            valid=False,
            low_confidence=True,
        )

    if not head_pose.valid:
        return GazeResult(
            direction=GazeDirection.UNKNOWN, confidence=0.0, valid=False, low_confidence=True
        )

    points = face.pixel_landmarks(frame_width, frame_height)
    confidence = _BASE_CONFIDENCE

    left = _iris_offset(points, LEFT_IRIS_CENTER, LEFT_EYE_EAR_POINTS)
    right = _iris_offset(points, RIGHT_IRIS_CENTER, RIGHT_EYE_EAR_POINTS)
    available = [value for value in (left, right) if value is not None]

    if not available:
        # No iris landmarks, or the face is too small. Head pose alone still says
        # something useful, but far less confidently.
        iris_horizontal = 0.0
        confidence *= 0.55
    else:
        iris_horizontal = float(sum(available) / len(available))
        if len(available) == 1:
            # One eye foreshortened or occluded.
            confidence *= 0.8

    # Head contribution, normalised so a full turn is about 1.0.
    head_horizontal = float(np.clip(head_pose.yaw / _YAW_FULL_SCALE, -1.0, 1.0))
    head_vertical = float(np.clip(head_pose.pitch / _PITCH_FULL_SCALE, -1.0, 1.0))

    horizontal = _HEAD_WEIGHT * head_horizontal + _EYE_WEIGHT * iris_horizontal
    vertical = head_vertical

    # A poor head-pose reading cannot yield a good gaze reading.
    confidence *= max(0.3, head_pose.confidence)

    # Cross-check against the blendshapes, which are produced independently.
    if any(name in face.blendshapes for name in _BLENDSHAPE_LOOK_DIRECTIONS):
        blend_horizontal = (
            face.blendshape("eyeLookOutLeft") - face.blendshape("eyeLookInLeft")
        )
        if abs(blend_horizontal) > 0.25 and abs(iris_horizontal) > 0.25:
            if np.sign(blend_horizontal) != np.sign(iris_horizontal):
                confidence *= 0.6

    if abs(head_pose.yaw) > _YAW_FULL_SCALE:
        confidence *= 0.7

    low_confidence = confidence < min_confidence
    if low_confidence:
        # Better to admit we could not tell than to accuse on weak evidence.
        return GazeResult(
            direction=GazeDirection.UNKNOWN,
            confidence=confidence,
            horizontal=horizontal,
            vertical=vertical,
            iris_horizontal=iris_horizontal,
            valid=True,
            low_confidence=True,
        )

    if head_pose.direction is HeadPose.LOOKING_DOWN or vertical < -_AWAY_THRESHOLD:
        direction = GazeDirection.DOWN
    elif vertical > _AWAY_THRESHOLD:
        direction = GazeDirection.UP
    elif horizontal > _AWAY_THRESHOLD:
        direction = GazeDirection.LEFT
    elif horizontal < -_AWAY_THRESHOLD:
        direction = GazeDirection.RIGHT
    else:
        direction = GazeDirection.SCREEN

    return GazeResult(
        direction=direction,
        confidence=min(confidence, 0.95),
        horizontal=horizontal,
        vertical=vertical,
        iris_horizontal=iris_horizontal,
        valid=True,
    )
