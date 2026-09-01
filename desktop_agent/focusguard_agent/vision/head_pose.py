"""Head pose estimation (spec section 14).

TWO ESTIMATORS, DELIBERATELY
----------------------------
**Angles come from MediaPipe's 4x4 facial transformation matrix.** The usual
alternative is `cv2.solvePnP` against a hand-picked generic 3D face model, which
adds error nobody can quantify - the model is not the user's face - and is hard to
defend in a viva. The matrix is produced by the same model that placed the
landmarks, so it needs no assumed geometry.

**Direction comes from landmark geometry in image space.** The matrix gives
reliable magnitudes, but the sign convention of a rotation matrix depends on the
axis convention of whoever produced it, and getting that backwards would label a
left turn as a right one. Image-space geometry has no such ambiguity: the nose
tip moving toward the right of the frame means one thing only.

So: **magnitude from the matrix, direction from the landmarks.** They are
independent, which also gives the cross-check used everywhere else in this
pipeline - agreement raises confidence, disagreement lowers it (spec section 61).

THE LANDMARK PROXY
------------------
With a non-mirrored camera facing the user, turning the head toward their own
left swings the nose tip toward the RIGHT of the image. So:

    offset = (nose_x - face_centre_x) / face_width

    offset > 0  ->  nose right of centre  ->  user turned to their own LEFT
    offset < 0  ->  nose left of centre   ->  user turned to their own RIGHT

The vertical proxy works the same way: looking down moves the nose tip down
relative to the face's vertical centre.

SIGN CONVENTION FOR OUTPUT
--------------------------
Angles are reported from the **user's own perspective**:

* ``yaw``   positive = turned to their LEFT,  negative = to their RIGHT
* ``pitch`` positive = looking UP,            negative = looking DOWN
* ``roll``  positive = head tilted to their LEFT

With ``flip_horizontal = false`` a left turn appears on the right of the preview,
which is what a non-mirrored camera does and is worth remembering when checking
the debug view.

LIMITATIONS
-----------
Beyond roughly 45 degrees of yaw one side of the face is barely visible, the
landmarks on it become guesses, and both estimators degrade. That is reported as
falling confidence rather than hidden behind a precise-looking number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from focusguard_agent.vision.landmarks import FaceResult
from focusguard_shared.constants import HeadPose

#: Nose tip. One of the most stable landmarks in the mesh.
NOSE_TIP = 1

#: Points spanning the face used to measure its extent. Chosen from the outline
#: so the span reflects the whole head rather than a feature that moves.
FACE_SPAN_POINTS: tuple[int, ...] = (234, 454, 10, 152, 33, 263, 61, 291)

#: Scale factors turning the dimensionless landmark offsets into rough degrees.
#: Only used when the transformation matrix is unavailable; the matrix is far
#: more accurate and is preferred whenever it is present.
_PROXY_YAW_DEGREES = 90.0
_PROXY_PITCH_DEGREES = 70.0

#: Below this the offset is within landmark noise and no direction is claimed.
_PROXY_DEADZONE = 0.02

_CONFIDENCE_AGREE = 0.9
_CONFIDENCE_DISAGREE = 0.45
_CONFIDENCE_PROXY_ONLY = 0.6

#: Yaw beyond this hides one side of the face and both estimators degrade.
_EXTREME_YAW_DEGREES = 45.0


@dataclass(frozen=True)
class HeadPoseResult:
    """Head orientation for one frame, in degrees from centre."""

    yaw: float
    pitch: float
    roll: float

    direction: HeadPose
    confidence: float
    valid: bool = True

    #: True when the matrix and the landmark proxy agreed on direction.
    signals_agree: bool = True

    #: True when the reading came from landmarks alone.
    proxy_only: bool = False

    @property
    def looking_down(self) -> bool:
        return self.direction is HeadPose.LOOKING_DOWN

    @property
    def is_centred(self) -> bool:
        return self.direction is HeadPose.CENTER


def decompose_rotation(matrix: np.ndarray) -> tuple[float, float, float]:
    """Extract (yaw, pitch, roll) in degrees from a 4x4 transformation matrix.

    Scale is divided out of the upper-left 3x3 first: MediaPipe's matrix carries
    the size of the head as well as its rotation, and feeding a scaled matrix
    into the arcsine below produces angles that drift with how close the user is
    sitting.

    Uses the standard XYZ Euler decomposition. Returned angles follow the
    matrix's own axis convention; the caller applies the direction sign from the
    landmark proxy rather than trusting it.
    """
    rotation = np.array(matrix, dtype=np.float64)[:3, :3]

    norms = np.linalg.norm(rotation, axis=0)
    if np.any(norms < 1e-9):
        return 0.0, 0.0, 0.0
    rotation = rotation / norms

    sy = math.sqrt(rotation[0, 0] ** 2 + rotation[1, 0] ** 2)

    if sy > 1e-6:
        x = math.atan2(rotation[2, 1], rotation[2, 2])
        y = math.atan2(-rotation[2, 0], sy)
        z = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        # Gimbal lock: the head is pitched so far that yaw and roll describe the
        # same rotation. Roll is set to zero and the other two remain meaningful.
        x = math.atan2(-rotation[1, 2], rotation[1, 1])
        y = math.atan2(-rotation[2, 0], sy)
        z = 0.0

    return math.degrees(y), math.degrees(x), math.degrees(z)


def landmark_orientation(points_px: np.ndarray) -> tuple[float, float] | None:
    """Dimensionless (horizontal, vertical) nose offsets, or None if unusable.

    Positive horizontal means the nose sits right of the face centre, which for a
    non-mirrored camera means the user turned to their own left.
    """
    if len(points_px) <= max(max(FACE_SPAN_POINTS), NOSE_TIP):
        return None

    span = points_px[list(FACE_SPAN_POINTS)][:, :2]
    min_x, min_y = span.min(axis=0)
    max_x, max_y = span.max(axis=0)

    width = float(max_x - min_x)
    height = float(max_y - min_y)
    if width < 10.0 or height < 10.0:
        # Too small for the offsets to mean anything but landmark jitter.
        return None

    nose = points_px[NOSE_TIP][:2]
    horizontal = float(nose[0] - (min_x + max_x) / 2.0) / width
    vertical = float(nose[1] - (min_y + max_y) / 2.0) / height

    return horizontal, vertical


def _classify(
    yaw: float,
    pitch: float,
    yaw_threshold: float,
    pitch_up_threshold: float,
    pitch_down_threshold: float,
) -> HeadPose:
    """Map angles to a direction, with yaw taking precedence over pitch.

    Yaw wins ties because turning away from the screen is the stronger signal of
    attention leaving it; a downward glance often still faces the monitor.
    """
    if abs(yaw) >= yaw_threshold:
        return HeadPose.LOOKING_LEFT if yaw > 0 else HeadPose.LOOKING_RIGHT
    if pitch <= -pitch_down_threshold:
        return HeadPose.LOOKING_DOWN
    if pitch >= pitch_up_threshold:
        return HeadPose.LOOKING_UP
    return HeadPose.CENTER


def estimate_head_pose(
    face: FaceResult,
    frame_width: int,
    frame_height: int,
    yaw_threshold: float = 25.0,
    pitch_up_threshold: float = 20.0,
    pitch_down_threshold: float = 20.0,
) -> HeadPoseResult:
    """Estimate head orientation, combining the matrix and the landmark proxy."""
    points = face.pixel_landmarks(frame_width, frame_height)
    proxy = landmark_orientation(points)

    if proxy is None:
        return HeadPoseResult(
            yaw=0.0,
            pitch=0.0,
            roll=0.0,
            direction=HeadPose.UNKNOWN,
            confidence=0.0,
            valid=False,
        )

    horizontal_offset, vertical_offset = proxy

    # Direction from the proxy: unambiguous in image space.
    proxy_yaw_sign = 0 if abs(horizontal_offset) < _PROXY_DEADZONE else (
        1 if horizontal_offset > 0 else -1
    )
    proxy_pitch_sign = 0 if abs(vertical_offset) < _PROXY_DEADZONE else (
        -1 if vertical_offset > 0 else 1
    )

    if face.transform_matrix is None:
        # Landmarks alone. Usable, but the angles are approximations, so this is
        # reported at reduced confidence rather than as a precise measurement.
        yaw = horizontal_offset * _PROXY_YAW_DEGREES
        pitch = -vertical_offset * _PROXY_PITCH_DEGREES
        direction = _classify(
            yaw, pitch, yaw_threshold, pitch_up_threshold, pitch_down_threshold
        )
        return HeadPoseResult(
            yaw=yaw,
            pitch=pitch,
            roll=0.0,
            direction=direction,
            confidence=_CONFIDENCE_PROXY_ONLY,
            valid=True,
            proxy_only=True,
        )

    matrix_yaw, matrix_pitch, matrix_roll = decompose_rotation(face.transform_matrix)

    # Magnitude from the matrix, direction from the proxy. Where the proxy is
    # inside its dead zone the matrix sign is kept, since near centre the sign
    # barely matters and the proxy is at its noisiest.
    yaw = abs(matrix_yaw) * proxy_yaw_sign if proxy_yaw_sign else matrix_yaw
    pitch = abs(matrix_pitch) * proxy_pitch_sign if proxy_pitch_sign else matrix_pitch

    agree = proxy_yaw_sign == 0 or math.copysign(1, matrix_yaw) == proxy_yaw_sign
    confidence = _CONFIDENCE_AGREE if agree else _CONFIDENCE_DISAGREE

    # Past roughly 45 degrees one side of the face is barely visible and its
    # landmarks are extrapolated, so both estimators lose accuracy.
    if abs(yaw) > _EXTREME_YAW_DEGREES:
        confidence *= 0.7

    direction = _classify(
        yaw, pitch, yaw_threshold, pitch_up_threshold, pitch_down_threshold
    )

    return HeadPoseResult(
        yaw=yaw,
        pitch=pitch,
        roll=matrix_roll,
        direction=direction,
        confidence=confidence,
        valid=True,
        signals_agree=agree,
    )
