"""Head pose tests (spec section 14).

Two things are tested separately, because they come from different estimators:
the rotation maths (synthetic matrices, exact), and the direction convention
(synthetic landmarks, image-space geometry).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from focusguard_agent.vision.head_pose import (
    FACE_SPAN_POINTS,
    NOSE_TIP,
    decompose_rotation,
    estimate_head_pose,
    landmark_orientation,
)
from focusguard_agent.vision.landmarks import FaceResult
from focusguard_shared.constants import HeadPose


def _rotation_matrix(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Build a 4x4 transform from XYZ Euler angles, matching the decomposition."""
    x, y, z = map(math.radians, (pitch_deg, yaw_deg, roll_deg))

    rx = np.array(
        [[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]]
    )
    ry = np.array(
        [[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]]
    )
    rz = np.array(
        [[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]]
    )

    matrix = np.eye(4)
    matrix[:3, :3] = rz @ ry @ rx
    return matrix


def _face(
    nose_offset_x: float = 0.0,
    nose_offset_y: float = 0.0,
    matrix: np.ndarray | None = None,
    face_width: float = 0.4,
) -> FaceResult:
    """Face whose nose sits at a given fraction of face width from centre."""
    landmarks = np.full((478, 3), 0.5, dtype=np.float32)

    half = face_width / 2.0
    # Spread the span points to define a face box of known extent.
    for index, point in enumerate(FACE_SPAN_POINTS):
        landmarks[point] = (
            0.5 + (half if index % 2 else -half),
            0.5 + (half if index >= 4 else -half),
            0.0,
        )

    landmarks[NOSE_TIP] = (
        0.5 + nose_offset_x * face_width,
        0.5 + nose_offset_y * face_width,
        0.0,
    )

    return FaceResult(landmarks=landmarks, transform_matrix=matrix)


# --- the rotation maths -----------------------------------------------------


@pytest.mark.parametrize(
    ("yaw", "pitch", "roll"),
    [(0, 0, 0), (30, 0, 0), (0, 20, 0), (0, 0, 15), (25, -15, 10), (-40, 10, -5)],
)
def test_decomposition_recovers_the_angles_it_was_built_from(
    yaw: float, pitch: float, roll: float
) -> None:
    """Round-trip: build a rotation from known angles, decompose, get them back."""
    recovered_yaw, recovered_pitch, recovered_roll = decompose_rotation(
        _rotation_matrix(yaw, pitch, roll)
    )

    assert recovered_yaw == pytest.approx(yaw, abs=0.5)
    assert recovered_pitch == pytest.approx(pitch, abs=0.5)
    assert recovered_roll == pytest.approx(roll, abs=0.5)


def test_scale_is_divided_out_before_decomposing() -> None:
    """MediaPipe's matrix carries head SIZE as well as rotation.

    Left in, the arcsine would receive a value greater than 1 and the angles
    would drift with how close the user is sitting.
    """
    matrix = _rotation_matrix(30, 0, 0)
    scaled = matrix.copy()
    scaled[:3, :3] *= 4.2

    assert decompose_rotation(scaled)[0] == pytest.approx(
        decompose_rotation(matrix)[0], abs=0.5
    )


def test_a_degenerate_matrix_returns_zeros_rather_than_nan() -> None:
    assert decompose_rotation(np.zeros((4, 4))) == (0.0, 0.0, 0.0)


def test_identity_matrix_is_centred() -> None:
    assert decompose_rotation(np.eye(4)) == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)


# --- the landmark direction proxy -------------------------------------------


def test_nose_right_of_centre_means_the_user_turned_left() -> None:
    """A non-mirrored camera faces the user, so their left is the image's right."""
    horizontal, _ = landmark_orientation(
        _face(nose_offset_x=0.2).pixel_landmarks(640, 360)
    )
    assert horizontal > 0


def test_nose_left_of_centre_means_the_user_turned_right() -> None:
    horizontal, _ = landmark_orientation(
        _face(nose_offset_x=-0.2).pixel_landmarks(640, 360)
    )
    assert horizontal < 0


def test_a_centred_face_has_no_offset() -> None:
    horizontal, vertical = landmark_orientation(_face().pixel_landmarks(640, 360))
    assert horizontal == pytest.approx(0.0, abs=0.01)
    assert vertical == pytest.approx(0.0, abs=0.01)


def test_a_tiny_face_gives_no_reading() -> None:
    """Below a few pixels the offsets are landmark jitter, not orientation."""
    assert landmark_orientation(_face(face_width=0.005).pixel_landmarks(640, 360)) is None


# --- combining the two ------------------------------------------------------


def test_direction_follows_the_landmark_proxy_not_the_matrix_sign() -> None:
    """Magnitude from the matrix, direction from the landmarks.

    A rotation matrix's sign convention depends on whoever produced it, and
    getting it backwards would label a left turn as a right one. Image-space
    geometry has no such ambiguity.
    """
    face = _face(nose_offset_x=0.25, matrix=_rotation_matrix(-35, 0, 0))
    result = estimate_head_pose(face, 640, 360, yaw_threshold=25.0)

    assert result.yaw > 0, "the proxy says left, so the reported yaw must be positive"
    assert result.direction is HeadPose.LOOKING_LEFT
    assert abs(result.yaw) == pytest.approx(35, abs=1.0), "magnitude comes from the matrix"


def test_disagreement_between_estimators_lowers_confidence() -> None:
    agreeing = estimate_head_pose(
        _face(nose_offset_x=0.25, matrix=_rotation_matrix(35, 0, 0)), 640, 360
    )
    disagreeing = estimate_head_pose(
        _face(nose_offset_x=0.25, matrix=_rotation_matrix(-35, 0, 0)), 640, 360
    )

    assert disagreeing.confidence < agreeing.confidence
    assert disagreeing.signals_agree is False


def test_a_centred_head_is_classified_centre() -> None:
    result = estimate_head_pose(_face(matrix=_rotation_matrix(0, 0, 0)), 640, 360)
    assert result.direction is HeadPose.CENTER
    assert result.is_centred is True


def test_looking_down_is_detected() -> None:
    face = _face(nose_offset_y=0.25, matrix=_rotation_matrix(0, -30, 0))
    result = estimate_head_pose(face, 640, 360, pitch_down_threshold=20.0)

    assert result.direction is HeadPose.LOOKING_DOWN
    assert result.looking_down is True
    assert result.pitch < 0


def test_a_small_turn_stays_centred() -> None:
    face = _face(nose_offset_x=0.05, matrix=_rotation_matrix(10, 0, 0))
    assert estimate_head_pose(face, 640, 360, yaw_threshold=25.0).direction is HeadPose.CENTER


def test_yaw_takes_precedence_over_pitch() -> None:
    """Turning away from the screen is the stronger signal; a downward glance
    often still faces the monitor."""
    face = _face(nose_offset_x=0.3, nose_offset_y=0.3, matrix=_rotation_matrix(40, -30, 0))
    result = estimate_head_pose(face, 640, 360, yaw_threshold=25.0, pitch_down_threshold=20.0)

    assert result.direction is HeadPose.LOOKING_LEFT


def test_extreme_yaw_reduces_confidence() -> None:
    """Past ~45 degrees one side of the face is barely visible."""
    moderate = estimate_head_pose(
        _face(nose_offset_x=0.2, matrix=_rotation_matrix(30, 0, 0)), 640, 360
    )
    extreme = estimate_head_pose(
        _face(nose_offset_x=0.45, matrix=_rotation_matrix(65, 0, 0)), 640, 360
    )

    assert extreme.confidence < moderate.confidence


def test_missing_matrix_falls_back_to_landmarks_at_lower_confidence() -> None:
    result = estimate_head_pose(_face(nose_offset_x=0.3, matrix=None), 640, 360)

    assert result.valid is True
    assert result.proxy_only is True
    assert result.confidence < 0.85
    assert result.direction is HeadPose.LOOKING_LEFT


def test_an_unusable_face_is_reported_unknown() -> None:
    result = estimate_head_pose(_face(face_width=0.002), 640, 360)

    assert result.valid is False
    assert result.direction is HeadPose.UNKNOWN
    assert result.confidence == 0.0
