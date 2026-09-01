"""Eye Aspect Ratio and eye state (spec section 12).

THE ALGORITHM
-------------
EAR (Soukupova & Cech, 2016) reduces an eye to a single number using six
landmarks: two corners and two points on each lid.

    EAR = (‖p2-p6‖ + ‖p3-p5‖) / (2 * ‖p1-p4‖)

              p2   p3
         p1 .--.---.--. p4          numerator   = eye HEIGHT (two vertical spans)
              p6   p5               denominator = eye WIDTH  (corner to corner)

An open eye is tall relative to its width, giving roughly 0.25-0.35. A closed eye
collapses vertically while the corners stay put, so EAR falls towards 0.05-0.10.

Dividing by the width is the point of the formula: it makes the value independent
of how far the user sits from the camera. Move closer and both the height and the
width grow, so the ratio holds.

WHY IT MUST BE COMPUTED IN PIXELS, NOT NORMALISED COORDINATES
-------------------------------------------------------------
MediaPipe returns landmarks normalised to 0-1 against image *width* for x and
image *height* for y. On a 640x360 frame those two axes have different scales, so
a vertical distance of 0.01 is 3.6 px while a horizontal distance of 0.01 is
6.4 px.

Computing EAR straight from normalised coordinates therefore multiplies it by
width/height - a factor of 1.78 on this camera. Every published threshold, and
any calibration taken on a differently-shaped frame, would be wrong. This module
takes pixel coordinates for exactly that reason, and `test_eye_analysis.py` pins
the behaviour with a test that the same eye scores the same EAR at 640x360 and
640x480.

WHY A SECOND, INDEPENDENT SIGNAL
--------------------------------
EAR is geometric, so it degrades when the head turns: a face at 40 degrees of yaw
has one eye foreshortened, and the width in the denominator shrinks faster than
the height. MediaPipe's `eyeBlinkLeft` / `eyeBlinkRight` blendshapes are produced
by a model that is largely pose-normalised, so they fail differently.

EAR decides whether the eye is closed - it is the documented, explainable
algorithm. The blendshapes decide *how confident* that answer is. When the two
agree, confidence is high; when they disagree, it drops, and the scoring engine
multiplies penalties by confidence (spec section 61: no strong claims from weak
evidence).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from focusguard_agent.vision.landmarks import (
    BLENDSHAPE_EYE_BLINK_LEFT,
    BLENDSHAPE_EYE_BLINK_RIGHT,
    FaceResult,
)

#: Six landmarks per eye, in the order the EAR formula expects:
#: p1 outer corner, p2/p3 upper lid, p4 inner corner, p5/p6 lower lid.
LEFT_EYE_EAR_POINTS: tuple[int, ...] = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_EAR_POINTS: tuple[int, ...] = (362, 385, 387, 263, 373, 380)

#: Blendshape activation above which the model considers the eye closed.
BLENDSHAPE_CLOSED_THRESHOLD = 0.5

#: Confidence when EAR and the blendshapes agree, and when they do not.
_CONFIDENCE_AGREE = 0.95
_CONFIDENCE_DISAGREE = 0.5

#: Below this the eye is too small in frame for the ratio to mean anything.
_MIN_EYE_WIDTH_PX = 4.0


@dataclass(frozen=True)
class EyeState:
    """Eye measurements for one frame."""

    ear_left: float
    ear_right: float
    ear_average: float

    #: Blendshape activations, 0.0 open to 1.0 closed.
    blink_left: float
    blink_right: float

    #: EAR's verdict - the primary decision.
    eyes_closed: bool

    #: Whether the blendshapes agreed with EAR this frame.
    signals_agree: bool

    #: 0-1. High when both signals agree, halved when they conflict.
    confidence: float

    #: False when the eye was too small or landmarks were missing, in which case
    #: no conclusion may be drawn from this frame at all.
    valid: bool = True

    @property
    def blink_average(self) -> float:
        return (self.blink_left + self.blink_right) / 2.0


def _eye_aspect_ratio(points_px: np.ndarray, indices: tuple[int, ...]) -> float | None:
    """EAR for one eye from pixel-space landmarks. None if not measurable."""
    if len(points_px) <= max(indices):
        return None

    p1, p2, p3, p4, p5, p6 = (points_px[i][:2].astype(np.float64) for i in indices)

    width = float(np.linalg.norm(p1 - p4))
    if width < _MIN_EYE_WIDTH_PX:
        # The face is too far away or too steeply angled; the ratio would be
        # dominated by landmark noise rather than by the eyelid.
        return None

    height = float(np.linalg.norm(p2 - p6)) + float(np.linalg.norm(p3 - p5))
    return height / (2.0 * width)


def analyse_eyes(
    face: FaceResult,
    frame_width: int,
    frame_height: int,
    ear_threshold: float,
    blendshape_threshold: float = BLENDSHAPE_CLOSED_THRESHOLD,
) -> EyeState:
    """Compute EAR and eye state for one detected face.

    Args:
        ear_threshold: personal threshold from calibration, or the configured
            default when the user has not calibrated.
    """
    points = face.pixel_landmarks(frame_width, frame_height)

    ear_left = _eye_aspect_ratio(points, LEFT_EYE_EAR_POINTS)
    ear_right = _eye_aspect_ratio(points, RIGHT_EYE_EAR_POINTS)

    blink_left = face.blendshape(BLENDSHAPE_EYE_BLINK_LEFT)
    blink_right = face.blendshape(BLENDSHAPE_EYE_BLINK_RIGHT)

    if ear_left is None and ear_right is None:
        return EyeState(
            ear_left=0.0,
            ear_right=0.0,
            ear_average=0.0,
            blink_left=blink_left,
            blink_right=blink_right,
            eyes_closed=False,
            signals_agree=False,
            confidence=0.0,
            valid=False,
        )

    # One eye may be unmeasurable at a steep angle; the other still carries the
    # signal, so fall back to whichever is available rather than discarding both.
    available = [value for value in (ear_left, ear_right) if value is not None]
    ear_average = float(sum(available) / len(available))

    ear_closed = ear_average < ear_threshold
    blend_average = (blink_left + blink_right) / 2.0
    blend_closed = blend_average > blendshape_threshold

    agree = ear_closed == blend_closed
    confidence = _CONFIDENCE_AGREE if agree else _CONFIDENCE_DISAGREE

    return EyeState(
        ear_left=ear_left if ear_left is not None else ear_average,
        ear_right=ear_right if ear_right is not None else ear_average,
        ear_average=ear_average,
        blink_left=blink_left,
        blink_right=blink_right,
        eyes_closed=ear_closed,
        signals_agree=agree,
        confidence=confidence,
        valid=True,
    )
