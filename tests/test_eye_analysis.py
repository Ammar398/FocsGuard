"""EAR and eye-state tests (spec section 12).

Built on synthetic landmarks, so a known eye shape produces a known EAR with no
camera involved. The important cases are the ones that would be silently wrong
rather than visibly broken.
"""

from __future__ import annotations

import numpy as np
import pytest

from focusguard_agent.vision.eye_analysis import (
    LEFT_EYE_EAR_POINTS,
    RIGHT_EYE_EAR_POINTS,
    analyse_eyes,
)
from focusguard_agent.vision.landmarks import FaceResult


def _face(
    eye_height_norm: float,
    eye_width_norm: float = 0.08,
    blink_score: float = 0.0,
    centre: tuple[float, float] = (0.5, 0.5),
) -> FaceResult:
    """Build a face whose eyes have a given NORMALISED width and height.

    Landmarks are normalised the way MediaPipe emits them, so tests exercise the
    same coordinate conversion the real pipeline does.
    """
    landmarks = np.full((478, 3), 0.5, dtype=np.float32)
    cx, cy = centre
    half_w = eye_width_norm / 2.0
    half_h = eye_height_norm / 2.0

    for indices, offset in ((LEFT_EYE_EAR_POINTS, -0.1), (RIGHT_EYE_EAR_POINTS, 0.1)):
        p1, p2, p3, p4, p5, p6 = indices
        x = cx + offset
        landmarks[p1] = (x - half_w, cy, 0.0)  # outer corner
        landmarks[p4] = (x + half_w, cy, 0.0)  # inner corner
        landmarks[p2] = (x - half_w / 3, cy - half_h, 0.0)  # upper lid
        landmarks[p3] = (x + half_w / 3, cy - half_h, 0.0)
        landmarks[p6] = (x - half_w / 3, cy + half_h, 0.0)  # lower lid
        landmarks[p5] = (x + half_w / 3, cy + half_h, 0.0)

    return FaceResult(
        landmarks=landmarks,
        blendshapes={"eyeBlinkLeft": blink_score, "eyeBlinkRight": blink_score},
    )


# --- the aspect-ratio trap --------------------------------------------------


def test_ear_is_identical_on_differently_shaped_frames() -> None:
    """The bug this test exists to prevent.

    MediaPipe normalises x against width and y against height, so on a 16:9 frame
    the two axes have different pixel scales. Computing EAR from normalised
    coordinates would multiply it by width/height - 1.78 on this camera - making
    every published threshold and every saved calibration wrong.

    The same physical eye must score the same EAR whatever the frame shape.
    """
    face = _face(eye_height_norm=0.03)

    widescreen = analyse_eyes(face, 640, 360, ear_threshold=0.21)
    four_three = analyse_eyes(face, 640, 480, ear_threshold=0.21)
    square = analyse_eyes(face, 480, 480, ear_threshold=0.21)

    # A normalised height of 0.03 is a different number of pixels on each frame,
    # so a correct implementation must scale to pixels before taking the ratio.
    assert widescreen.ear_average != pytest.approx(four_three.ear_average, abs=1e-6)
    assert square.ear_average > 0


def test_ear_does_not_change_with_distance_from_the_camera() -> None:
    """The reason EAR divides by eye width: scale invariance.

    Sitting closer makes the eye taller AND wider, so the ratio is unchanged.
    """
    near = _face(eye_height_norm=0.06, eye_width_norm=0.16)
    far = _face(eye_height_norm=0.03, eye_width_norm=0.08)

    near_state = analyse_eyes(near, 640, 480, ear_threshold=0.21)
    far_state = analyse_eyes(far, 640, 480, ear_threshold=0.21)

    assert near_state.ear_average == pytest.approx(far_state.ear_average, rel=1e-6)


# --- open and closed --------------------------------------------------------


def test_open_eye_scores_above_the_threshold() -> None:
    state = analyse_eyes(_face(eye_height_norm=0.045), 640, 480, ear_threshold=0.21)
    assert state.eyes_closed is False
    assert state.ear_average > 0.21


def test_closed_eye_scores_below_the_threshold() -> None:
    state = analyse_eyes(
        _face(eye_height_norm=0.006, blink_score=0.9), 640, 480, ear_threshold=0.21
    )
    assert state.eyes_closed is True
    assert state.ear_average < 0.21


def test_threshold_is_applied_per_user() -> None:
    """A calibrated user with naturally narrow eyes must not read as asleep."""
    face = _face(eye_height_norm=0.022)

    with_default = analyse_eyes(face, 640, 480, ear_threshold=0.21)
    with_personal = analyse_eyes(face, 640, 480, ear_threshold=0.14)

    assert with_default.eyes_closed is True
    assert with_personal.eyes_closed is False, "calibration must be able to rescue this user"


# --- the two independent signals -------------------------------------------


def test_agreement_between_ear_and_blendshapes_raises_confidence() -> None:
    closed = analyse_eyes(
        _face(eye_height_norm=0.005, blink_score=0.95), 640, 480, ear_threshold=0.21
    )
    assert closed.eyes_closed is True
    assert closed.signals_agree is True
    assert closed.confidence > 0.9


def test_disagreement_lowers_confidence_but_ear_still_decides() -> None:
    """EAR is the documented algorithm; blendshapes only qualify how sure we are.

    Disagreement is expected at steep head angles, where EAR foreshortens.
    """
    state = analyse_eyes(
        _face(eye_height_norm=0.005, blink_score=0.05), 640, 480, ear_threshold=0.21
    )

    assert state.eyes_closed is True, "EAR decides"
    assert state.signals_agree is False
    assert state.confidence < 0.6, "but we are much less sure"


def test_open_eyes_with_agreeing_signals_are_confident() -> None:
    state = analyse_eyes(
        _face(eye_height_norm=0.05, blink_score=0.02), 640, 480, ear_threshold=0.21
    )
    assert state.eyes_closed is False
    assert state.signals_agree is True


# --- unmeasurable eyes ------------------------------------------------------


def test_a_tiny_face_is_reported_as_invalid_not_as_closed() -> None:
    """Far from the camera the ratio is landmark noise, not eyelid position.

    Reporting that as closed eyes would penalise a user for sitting back.
    """
    tiny = _face(eye_height_norm=0.0005, eye_width_norm=0.002)
    state = analyse_eyes(tiny, 640, 360, ear_threshold=0.21)

    assert state.valid is False
    assert state.eyes_closed is False
    assert state.confidence == 0.0


def test_missing_landmarks_are_handled() -> None:
    face = FaceResult(landmarks=np.full((10, 3), 0.5, dtype=np.float32))
    state = analyse_eyes(face, 640, 480, ear_threshold=0.21)

    assert state.valid is False
    assert state.eyes_closed is False


def test_missing_blendshapes_default_to_zero() -> None:
    """Blendshape output can be disabled; EAR must still work alone."""
    face = FaceResult(landmarks=_face(eye_height_norm=0.045).landmarks, blendshapes={})
    state = analyse_eyes(face, 640, 480, ear_threshold=0.21)

    assert state.valid is True
    assert state.blink_left == 0.0
