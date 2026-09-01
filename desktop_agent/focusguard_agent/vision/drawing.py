"""Landmark overlay drawing.

MediaPipe 1.x removed ``mp.solutions.drawing_utils`` along with the rest of the
Solutions API, so the debug view draws its own overlays. That is a small cost and
one real benefit: we control exactly what is shown, and can highlight the specific
landmark groups the pipeline actually uses rather than a generic 478-point mesh
that is unreadable at 640x360.

Purely presentational. Nothing here feeds detection or scoring.
"""

from __future__ import annotations

import cv2
import numpy as np

# Landmark indices from the MediaPipe canonical face model.
#
# The six-point eye contours below are the ones EAR is computed from in Phase 4,
# ordered as the EAR formula expects:
#   p1 = outer corner, p2/p3 = upper lid, p4 = inner corner, p5/p6 = lower lid
# Keeping the order here identical to the order used in the calculation means the
# debug view is drawing exactly what the maths reads.
LEFT_EYE_EAR_POINTS: tuple[int, ...] = (33, 160, 158, 133, 153, 144)
RIGHT_EYE_EAR_POINTS: tuple[int, ...] = (362, 385, 387, 263, 373, 380)

#: Iris centres, present only in the 478-landmark model.
LEFT_IRIS_CENTER = 468
RIGHT_IRIS_CENTER = 473

#: Sparse face outline, enough to show tracking is locked on without clutter.
FACE_OVAL: tuple[int, ...] = (
    10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
    397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
    172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109,
)

_COLOUR_MESH = (90, 90, 90)
_COLOUR_OVAL = (200, 200, 200)
_COLOUR_EYE = (120, 220, 120)
_COLOUR_IRIS = (255, 190, 60)


def draw_landmarks(
    image: np.ndarray,
    landmarks_px: np.ndarray,
    *,
    show_mesh: bool = False,
    show_eyes: bool = True,
    show_iris: bool = True,
) -> None:
    """Draw a face overlay in place.

    Args:
        image: BGR frame, modified in place.
        landmarks_px: (N, 2+) landmark array already in pixel coordinates.
    """
    if landmarks_px.size == 0:
        return

    points = landmarks_px[:, :2].astype(np.int32)
    count = len(points)

    if show_mesh:
        for x, y in points:
            cv2.circle(image, (int(x), int(y)), 1, _COLOUR_MESH, -1, cv2.LINE_AA)

    oval = [points[i] for i in FACE_OVAL if i < count]
    if len(oval) > 2:
        cv2.polylines(image, [np.array(oval, dtype=np.int32)], True, _COLOUR_OVAL, 1, cv2.LINE_AA)

    if show_eyes:
        for eye in (LEFT_EYE_EAR_POINTS, RIGHT_EYE_EAR_POINTS):
            contour = [points[i] for i in eye if i < count]
            if len(contour) == len(eye):
                cv2.polylines(
                    image, [np.array(contour, dtype=np.int32)], True, _COLOUR_EYE, 1, cv2.LINE_AA
                )
                for x, y in contour:
                    cv2.circle(image, (int(x), int(y)), 2, _COLOUR_EYE, -1, cv2.LINE_AA)

    if show_iris and count > RIGHT_IRIS_CENTER:
        for index in (LEFT_IRIS_CENTER, RIGHT_IRIS_CENTER):
            x, y = points[index]
            cv2.circle(image, (int(x), int(y)), 3, _COLOUR_IRIS, -1, cv2.LINE_AA)


def draw_bounding_box(
    image: np.ndarray,
    landmarks_px: np.ndarray,
    colour: tuple[int, int, int] = (200, 200, 200),
    label: str | None = None,
) -> None:
    """Draw a box around a face, derived from its landmark extent."""
    if landmarks_px.size == 0:
        return

    points = landmarks_px[:, :2]
    x1, y1 = points.min(axis=0).astype(int)
    x2, y2 = points.max(axis=0).astype(int)

    padding = 8
    x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
    x2 = min(image.shape[1] - 1, x2 + padding)
    y2 = min(image.shape[0] - 1, y2 + padding)

    cv2.rectangle(image, (x1, y1), (x2, y2), colour, 1, cv2.LINE_AA)

    if label:
        cv2.putText(
            image, label, (x1, max(12, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA,
        )


def draw_status_panel(
    image: np.ndarray,
    lines: list[tuple[str, tuple[int, int, int]]],
    *,
    origin: tuple[int, int] = (10, 10),
    line_height: int = 19,
    width: int = 300,
) -> None:
    """Draw a translucent panel of coloured status lines.

    The panel keeps text readable regardless of what the camera is pointing at -
    white text on a bright wall is otherwise invisible.
    """
    if not lines:
        return

    x, y = origin
    height = line_height * len(lines) + 12
    panel_x2 = min(image.shape[1], x + width)
    panel_y2 = min(image.shape[0], y + height)

    overlay = image.copy()
    cv2.rectangle(overlay, (x, y), (panel_x2, panel_y2), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, image, 0.5, 0, image)

    for row, (text, colour) in enumerate(lines):
        cv2.putText(
            image, text, (x + 10, y + 20 + row * line_height),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA,
        )
