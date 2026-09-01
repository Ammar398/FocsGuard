"""Phone detection with YOLOv8n through ONNX Runtime (spec section 16).

WHY ONNX RUNTIME AND NOT THE ULTRALYTICS RUNTIME
------------------------------------------------
`YOLO("yolov8n.pt").predict()` is one line, and the wrong choice here:

* it needs PyTorch, and on this machine's **AMD Radeon RX 580** PyTorch on
  Windows offers only its CPU path - so the heaviest dependency in the project
  would run YOLO on the same four threads MediaPipe needs;
* ONNX Runtime with the **DirectML** provider genuinely uses the RX 580, moving
  detection off the CPU;
* it removes ~2.5 GB of dependency for a 12 MB model.

The cost is that pre- and post-processing must be written by hand. That is about
a hundred lines, and it is a benefit at defence: every step from pixels to a
bounding box can be explained, rather than pointing at a library call.

THE PIPELINE
------------
    frame (640x360 BGR)
      -> letterbox to 320x320, preserving aspect        [_letterbox]
      -> BGR->RGB, HWC->CHW, /255, add batch dim        [_preprocess]
      -> ONNX Runtime, DirectML or CPU                  [session.run]
      -> output (1, 84, 2100): 4 box + 80 class scores
      -> filter to class 67 above threshold             [_postprocess]
      -> non-maximum suppression                        [_nms]
      -> boxes mapped back to original frame coordinates

WHY LETTERBOX RATHER THAN A PLAIN RESIZE
----------------------------------------
Squashing a 16:9 frame into a square would distort every object in it. A phone -
a strongly rectangular object - would be stretched into a shape the model was
never trained on. Letterboxing scales by a single factor and pads the remainder,
so geometry is preserved. This is the same aspect-ratio discipline as Phase 2,
and for the same reason.

WHY CLASS 67
------------
The model is COCO-pretrained; class 67 is `cell phone`. No training required
(spec section 16).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from focusguard_shared.constants import InferenceProvider
from focusguard_shared.utilities import get_logger

logger = get_logger("vision.phone_detector")

#: COCO class index for a mobile phone.
CELL_PHONE_CLASS_ID = 67

#: Padding colour for letterboxing. Mid-grey is the usual YOLO convention and
#: is less likely than black to be read as an object edge.
_PAD_COLOUR = (114, 114, 114)

#: IoU above which two boxes are treated as the same object during NMS.
_NMS_IOU_THRESHOLD = 0.45

#: Safety cap: a frame should never contain hundreds of phones, and this bounds
#: the cost of NMS if the model produces noise.
_MAX_DETECTIONS = 20


@dataclass(frozen=True)
class Detection:
    """One detected object, in original frame coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int = CELL_PHONE_CLASS_ID

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0


@dataclass
class PhoneDetectionResult:
    """Everything the phone stage produced for one frame."""

    detections: list[Detection]
    inference_ms: float
    timestamp: float

    @property
    def detected(self) -> bool:
        return bool(self.detections)

    @property
    def best_confidence(self) -> float:
        """Highest confidence in the frame, 0.0 when nothing was found."""
        return max((d.confidence for d in self.detections), default=0.0)


def _letterbox(
    image: np.ndarray, target: int
) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize into a square canvas without distorting the image.

    Returns the canvas, the scale applied, and the (left, top) padding, which are
    needed to map detections back to original coordinates.
    """
    height, width = image.shape[:2]
    scale = min(target / width, target / height)

    new_width = int(round(width * scale))
    new_height = int(round(height * scale))
    resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_LINEAR)

    canvas = np.full((target, target, 3), _PAD_COLOUR, dtype=np.uint8)
    left = (target - new_width) // 2
    top = (target - new_height) // 2
    canvas[top : top + new_height, left : left + new_width] = resized

    return canvas, scale, (left, top)


def _preprocess(image: np.ndarray, target: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Frame to model input: letterbox, RGB, CHW, normalised, batched."""
    canvas, scale, padding = _letterbox(image, target)

    # OpenCV gives BGR; the model was trained on RGB. Getting this wrong does not
    # error, it just quietly detects less.
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)

    tensor = rgb.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))  # HWC -> CHW
    return np.expand_dims(tensor, axis=0), scale, padding


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy non-maximum suppression. Returns kept indices, best first.

    YOLO proposes many overlapping boxes for one object. Without this, a single
    phone would produce a dozen detections and the event engine would see a crowd
    of objects rather than one.
    """
    if len(boxes) == 0:
        return []

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    keep: list[int] = []
    while order.size > 0 and len(keep) < _MAX_DETECTIONS:
        best = order[0]
        keep.append(int(best))

        if order.size == 1:
            break

        rest = order[1:]
        # Intersection of the best box with every remaining box.
        inter_x1 = np.maximum(x1[best], x1[rest])
        inter_y1 = np.maximum(y1[best], y1[rest])
        inter_x2 = np.minimum(x2[best], x2[rest])
        inter_y2 = np.minimum(y2[best], y2[rest])

        inter = np.maximum(0.0, inter_x2 - inter_x1) * np.maximum(0.0, inter_y2 - inter_y1)
        union = areas[best] + areas[rest] - inter
        iou = np.where(union > 0, inter / union, 0.0)

        order = rest[iou <= iou_threshold]

    return keep


def _postprocess(
    output: np.ndarray,
    scale: float,
    padding: tuple[int, int],
    frame_shape: tuple[int, int],
    confidence_threshold: float,
    class_id: int,
) -> list[Detection]:
    """Turn raw model output into detections in original frame coordinates.

    YOLOv8 output is (1, 84, N): rows 0-3 are the box as centre-x, centre-y,
    width, height in model-input pixels; rows 4-83 are per-class scores. There is
    no separate objectness score, unlike YOLOv5.
    """
    predictions = np.squeeze(output, axis=0).T  # (N, 84)
    if predictions.size == 0:
        return []

    scores = predictions[:, 4 + class_id]
    mask = scores >= confidence_threshold
    if not np.any(mask):
        return []

    candidates = predictions[mask]
    scores = scores[mask]

    centre_x, centre_y = candidates[:, 0], candidates[:, 1]
    width, height = candidates[:, 2], candidates[:, 3]

    # Centre form -> corner form, then undo the letterbox: remove the padding
    # first, then the scale. Doing it the other way round shifts every box.
    left, top = padding
    x1 = (centre_x - width / 2.0 - left) / scale
    y1 = (centre_y - height / 2.0 - top) / scale
    x2 = (centre_x + width / 2.0 - left) / scale
    y2 = (centre_y + height / 2.0 - top) / scale

    frame_height, frame_width = frame_shape
    x1 = np.clip(x1, 0, frame_width)
    y1 = np.clip(y1, 0, frame_height)
    x2 = np.clip(x2, 0, frame_width)
    y2 = np.clip(y2, 0, frame_height)

    boxes = np.stack([x1, y1, x2, y2], axis=1)
    keep = _nms(boxes, scores, _NMS_IOU_THRESHOLD)

    return [
        Detection(
            x1=float(boxes[i][0]),
            y1=float(boxes[i][1]),
            x2=float(boxes[i][2]),
            y2=float(boxes[i][3]),
            confidence=float(scores[i]),
            class_id=class_id,
        )
        for i in keep
    ]


class PhoneDetector:
    """YOLOv8n phone detection through ONNX Runtime."""

    def __init__(
        self,
        model_path: Path,
        input_size: int = 320,
        confidence_threshold: float = 0.5,
        provider: InferenceProvider | None = None,
    ) -> None:
        import onnxruntime as ort

        if not model_path.exists():
            raise FileNotFoundError(
                f"YOLO model not found at {model_path}. "
                "Run scripts/export_yolo.ps1 to create it."
            )

        self._input_size = input_size
        self._confidence_threshold = confidence_threshold

        providers = self._choose_providers(ort, provider)
        options = ort.SessionOptions()
        # The agent already runs its own threads; letting ORT spawn more on a
        # 4-core CPU costs more in contention than it gains.
        options.intra_op_num_threads = 2
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=providers
        )
        self._input_name = self._session.get_inputs()[0].name
        self.active_provider = self._session.get_providers()[0]

        logger.info(
            "PhoneDetector ready (%dx%d, provider=%s)",
            input_size,
            input_size,
            self.active_provider,
        )

    @staticmethod
    def _choose_providers(ort_module, provider: InferenceProvider | None) -> list[str]:
        """Pick execution providers, always keeping CPU as the final fallback."""
        available = ort_module.get_available_providers()

        if provider is not None and provider.value in available:
            chosen = [provider.value]
        elif InferenceProvider.DIRECTML.value in available:
            chosen = [InferenceProvider.DIRECTML.value]
        elif InferenceProvider.CUDA.value in available:
            chosen = [InferenceProvider.CUDA.value]
        else:
            chosen = []

        if InferenceProvider.CPU.value not in chosen:
            chosen.append(InferenceProvider.CPU.value)
        return chosen

    def detect(self, image_bgr: np.ndarray, timestamp: float | None = None) -> PhoneDetectionResult:
        """Detect phones in one frame."""
        now = time.monotonic() if timestamp is None else timestamp

        tensor, scale, padding = _preprocess(image_bgr, self._input_size)

        started = time.perf_counter()
        outputs = self._session.run(None, {self._input_name: tensor})
        inference_ms = (time.perf_counter() - started) * 1000.0

        detections = _postprocess(
            outputs[0],
            scale=scale,
            padding=padding,
            frame_shape=image_bgr.shape[:2],
            confidence_threshold=self._confidence_threshold,
            class_id=CELL_PHONE_CLASS_ID,
        )

        return PhoneDetectionResult(
            detections=detections, inference_ms=inference_ms, timestamp=now
        )

    def close(self) -> None:
        self._session = None  # type: ignore[assignment]

    def __enter__(self) -> PhoneDetector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
