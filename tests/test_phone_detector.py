"""Phone detection tests (spec section 16).

The pre- and post-processing are written by hand rather than taken from a
library, so they are tested directly. The coordinate mapping is the part most
likely to be subtly wrong: a box that is 20 pixels off still looks plausible in a
debug view but corrupts every downstream measurement.

No model file is needed - these exercise the maths around it.
"""

from __future__ import annotations

import numpy as np
import pytest

from focusguard_agent.vision.phone_detector import (
    CELL_PHONE_CLASS_ID,
    Detection,
    _letterbox,
    _nms,
    _postprocess,
    _preprocess,
)


def _frame(width: int = 640, height: int = 360) -> np.ndarray:
    return np.full((height, width, 3), 200, dtype=np.uint8)


# --- letterbox --------------------------------------------------------------


def test_letterbox_preserves_aspect_ratio() -> None:
    """Squashing 16:9 into a square would stretch a phone into a shape the model
    was never trained on."""
    canvas, scale, (left, top) = _letterbox(_frame(640, 360), 320)

    assert canvas.shape == (320, 320, 3)
    assert scale == pytest.approx(0.5)
    # 360 * 0.5 = 180 tall, leaving 140 to split top and bottom.
    assert top == 70
    assert left == 0


def test_letterbox_pads_a_tall_image_horizontally() -> None:
    canvas, scale, (left, top) = _letterbox(_frame(360, 640), 320)

    assert canvas.shape == (320, 320, 3)
    assert left == 70
    assert top == 0


def test_a_square_image_needs_no_padding() -> None:
    _, scale, (left, top) = _letterbox(_frame(480, 480), 320)

    assert scale == pytest.approx(320 / 480)
    assert (left, top) == (0, 0)


def test_padding_uses_grey_not_black() -> None:
    """Black padding reads as a hard object edge to the model."""
    canvas, _, (_, top) = _letterbox(_frame(640, 360), 320)
    assert tuple(canvas[0, 0]) == (114, 114, 114)
    assert top > 0


# --- preprocessing ----------------------------------------------------------


def test_preprocess_produces_the_tensor_the_model_expects() -> None:
    tensor, _, _ = _preprocess(_frame(), 320)

    assert tensor.shape == (1, 3, 320, 320), "batch, channels, height, width"
    assert tensor.dtype == np.float32
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0, "must be normalised to 0-1"


def test_preprocess_converts_bgr_to_rgb() -> None:
    """OpenCV gives BGR; the model was trained on RGB. Getting this wrong does
    not error, it just quietly detects less."""
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :, 0] = 255  # pure blue in BGR

    tensor, _, _ = _preprocess(frame, 320)

    # Blue must land in the LAST channel after conversion, not the first.
    assert tensor[0, 2].max() == pytest.approx(1.0)
    assert tensor[0, 0].max() < 0.5


# --- coordinate mapping -----------------------------------------------------


def _model_output(centre_x: float, centre_y: float, width: float, height: float,
                  score: float = 0.9, count: int = 1) -> np.ndarray:
    """Build a fake YOLOv8 output: (1, 84, N), 4 box rows then 80 class rows."""
    output = np.zeros((1, 84, count), dtype=np.float32)
    for index in range(count):
        output[0, 0, index] = centre_x
        output[0, 1, index] = centre_y
        output[0, 2, index] = width
        output[0, 3, index] = height
        output[0, 4 + CELL_PHONE_CLASS_ID, index] = score
    return output


def test_detections_map_back_to_original_frame_coordinates() -> None:
    """The round-trip that must not drift.

    A 640x360 frame letterboxes to 320x320 with scale 0.5 and 70 px of top
    padding. A box at the centre of the model input must come back at the centre
    of the original frame.
    """
    _, scale, padding = _preprocess(_frame(640, 360), 320)

    output = _model_output(centre_x=160, centre_y=160, width=40, height=60)
    detections = _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    )

    assert len(detections) == 1
    centre = detections[0].centre
    assert centre[0] == pytest.approx(320, abs=1)
    assert centre[1] == pytest.approx(180, abs=1)


def test_box_dimensions_are_scaled_back_correctly() -> None:
    _, scale, padding = _preprocess(_frame(640, 360), 320)

    output = _model_output(centre_x=160, centre_y=160, width=40, height=60)
    detection = _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    )[0]

    # Scale 0.5, so a 40x60 box in model space is 80x120 in the frame.
    assert detection.width == pytest.approx(80, abs=1)
    assert detection.height == pytest.approx(120, abs=1)


def test_padding_is_removed_before_the_scale_not_after() -> None:
    """Doing it the other way round shifts every box by the padding times the
    scale factor - a plausible-looking but wrong result."""
    _, scale, padding = _preprocess(_frame(640, 360), 320)

    # A box at the very top of the padded content area.
    output = _model_output(centre_x=160, centre_y=80, width=20, height=20)
    detection = _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    )[0]

    # (80 - 70) / 0.5 = 20. The wrong order would give 80/0.5 - 70 = 90.
    assert detection.centre[1] == pytest.approx(20, abs=1)


def test_boxes_are_clipped_to_the_frame() -> None:
    """A partially visible phone must not produce negative coordinates."""
    _, scale, padding = _preprocess(_frame(640, 360), 320)

    output = _model_output(centre_x=5, centre_y=80, width=60, height=40)
    detection = _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    )[0]

    assert detection.x1 >= 0
    assert detection.x2 <= 640


# --- filtering --------------------------------------------------------------


def test_detections_below_the_threshold_are_discarded() -> None:
    _, scale, padding = _preprocess(_frame(), 320)
    output = _model_output(160, 160, 40, 40, score=0.3)

    assert _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    ) == []


def test_other_object_classes_are_ignored() -> None:
    """A laptop or a cup must not be reported as a phone."""
    _, scale, padding = _preprocess(_frame(), 320)

    output = np.zeros((1, 84, 1), dtype=np.float32)
    output[0, 0:4, 0] = (160, 160, 40, 40)
    output[0, 4 + 63, 0] = 0.95  # class 63 is 'laptop'

    assert _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    ) == []


def test_an_empty_output_yields_no_detections() -> None:
    _, scale, padding = _preprocess(_frame(), 320)
    empty = np.zeros((1, 84, 0), dtype=np.float32)

    assert _postprocess(
        empty, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    ) == []


# --- non-maximum suppression ------------------------------------------------


def test_nms_collapses_overlapping_boxes_into_one() -> None:
    """YOLO proposes many boxes per object. Without NMS one phone would look
    like a dozen."""
    boxes = np.array(
        [[100, 100, 200, 200], [105, 105, 205, 205], [98, 102, 198, 202]],
        dtype=np.float32,
    )
    scores = np.array([0.9, 0.85, 0.8], dtype=np.float32)

    assert _nms(boxes, scores, 0.45) == [0], "the highest-scoring box survives"


def test_nms_keeps_genuinely_separate_objects() -> None:
    boxes = np.array([[10, 10, 60, 60], [300, 200, 360, 260]], dtype=np.float32)
    scores = np.array([0.9, 0.8], dtype=np.float32)

    assert len(_nms(boxes, scores, 0.45)) == 2


def test_nms_returns_boxes_in_confidence_order() -> None:
    boxes = np.array([[10, 10, 60, 60], [300, 200, 360, 260]], dtype=np.float32)
    scores = np.array([0.5, 0.95], dtype=np.float32)

    assert _nms(boxes, scores, 0.45) == [1, 0]


def test_nms_handles_an_empty_input() -> None:
    assert _nms(np.zeros((0, 4)), np.zeros(0), 0.45) == []


def test_duplicate_proposals_produce_one_detection_end_to_end() -> None:
    _, scale, padding = _preprocess(_frame(), 320)
    output = _model_output(160, 160, 40, 40, score=0.9, count=8)

    detections = _postprocess(
        output, scale=scale, padding=padding, frame_shape=(360, 640),
        confidence_threshold=0.5, class_id=CELL_PHONE_CLASS_ID,
    )

    assert len(detections) == 1


# --- detection helpers ------------------------------------------------------


def test_detection_geometry_helpers() -> None:
    detection = Detection(x1=10, y1=20, x2=110, y2=220, confidence=0.8)

    assert detection.width == 100
    assert detection.height == 200
    assert detection.area == 20000
    assert detection.centre == (60, 120)
