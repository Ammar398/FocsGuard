"""Face landmark detection using the MediaPipe Tasks API.

WHY THE TASKS API AND NOT mp.solutions.face_mesh
------------------------------------------------
Almost every FocusGuard-style tutorial uses
``mp.solutions.face_mesh.FaceMesh(refine_landmarks=True)``. That API was **removed
in MediaPipe 1.x**, which is what is installed here - ``mp.solutions`` does not
exist and that code raises AttributeError.

The replacement is better for this project, because FaceLandmarker returns three
things the old API did not expose together:

1. **478 landmarks including the iris** (indices 468-477), which Phase 6 needs for
   gaze estimation.
2. **Blendshapes** - ``eyeBlinkLeft``, ``eyeBlinkRight``, ``eyeLookIn/Out/Up/Down``
   and 46 others. These are pose-normalised, so they give an independent
   cross-check on the geometric EAR calculation. Where the two agree, confidence
   is high; where they disagree, it should be low (spec section 61).
3. **A 4x4 facial transformation matrix**, from which head yaw, pitch and roll
   decompose directly. The usual alternative is ``cv2.solvePnP`` against a
   hand-picked generic 3D face model, which introduces error that is hard to
   quantify and harder to defend in a viva.

RUNNING MODE
------------
VIDEO mode is used rather than IMAGE. It carries tracking state between frames, so
landmarks are steadier and cheaper than re-detecting from scratch each time. It
requires strictly increasing millisecond timestamps, which this class enforces -
MediaPipe raises if a timestamp repeats or goes backwards, and a naive conversion
from a fast clock does produce duplicates.

THREADING
---------
A FaceLandmarker instance is not thread-safe. One instance belongs to one thread.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python import vision as mp_vision

from focusguard_agent.hardware.model_store import get_model_path
from focusguard_shared.utilities import get_logger

logger = get_logger("vision.landmarks")

#: Iris landmarks exist only in the 478-point model; the base mesh has 468.
IRIS_LANDMARK_START = 468
FULL_LANDMARK_COUNT = 478

#: Blendshape names used elsewhere in the pipeline, named here so a typo becomes
#: an import error rather than a silently-zero signal.
BLENDSHAPE_EYE_BLINK_LEFT = "eyeBlinkLeft"
BLENDSHAPE_EYE_BLINK_RIGHT = "eyeBlinkRight"


@dataclass
class FaceResult:
    """Landmarks and derived signals for one detected face."""

    #: (N, 3) array of landmarks normalised to 0-1 in x and y. z is roughly in the
    #: same scale as x, relative to the head centre - useful for ordering depth,
    #: not for absolute distance.
    landmarks: np.ndarray

    #: Blendshape name -> activation 0-1. Empty when blendshape output is off.
    blendshapes: dict[str, float] = field(default_factory=dict)

    #: 4x4 facial transformation matrix, or None when unavailable.
    transform_matrix: np.ndarray | None = None

    @property
    def has_iris(self) -> bool:
        return len(self.landmarks) >= FULL_LANDMARK_COUNT

    def blendshape(self, name: str) -> float:
        """Look up one blendshape, returning 0.0 when it is not present."""
        return self.blendshapes.get(name, 0.0)

    def pixel_landmarks(self, width: int, height: int) -> np.ndarray:
        """Landmarks scaled to pixel coordinates, for drawing and measurement."""
        points = self.landmarks[:, :2].copy()
        points[:, 0] *= width
        points[:, 1] *= height
        return points


@dataclass
class LandmarkFrame:
    """Everything the landmark stage produced for one frame."""

    faces: list[FaceResult]
    timestamp: float
    inference_ms: float

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def primary(self) -> FaceResult | None:
        """The first detected face.

        MediaPipe orders faces by detection confidence, so the first is the one to
        analyse when several people are visible. MULTIPLE_FACES is reported
        separately as an event; it does not stop us tracking the main user.
        """
        return self.faces[0] if self.faces else None


class FaceLandmarkDetector:
    """Wraps MediaPipe FaceLandmarker for a single processing thread."""

    def __init__(
        self,
        max_faces: int = 2,
        min_detection_confidence: float = 0.5,
        min_presence_confidence: float = 0.5,
        min_tracking_confidence: float = 0.5,
        output_blendshapes: bool = True,
        output_transform_matrix: bool = True,
    ) -> None:
        """
        Args:
            max_faces: 2 by default - enough to detect that more than one person
                is present (spec section 11) without paying for more.
        """
        self._max_faces = max_faces
        self._landmarker: mp_vision.FaceLandmarker | None = None
        self._last_timestamp_ms = -1

        model_path = get_model_path("face_landmarker")
        options = mp_vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_faces=max_faces,
            min_face_detection_confidence=min_detection_confidence,
            min_face_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
            output_face_blendshapes=output_blendshapes,
            output_facial_transformation_matrixes=output_transform_matrix,
        )
        self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        logger.info("FaceLandmarker ready (max_faces=%d, model=%s)", max_faces, model_path.name)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def __enter__(self) -> FaceLandmarkDetector:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- detection ---------------------------------------------------------

    def detect(self, image_bgr: np.ndarray, timestamp: float | None = None) -> LandmarkFrame:
        """Detect faces in one BGR frame.

        Args:
            image_bgr: frame as delivered by OpenCV (BGR channel order).
            timestamp: monotonic seconds. Defaults to now.

        Returns a LandmarkFrame with an empty ``faces`` list when nothing is found -
        never None, so callers do not have to special-case absence.
        """
        if self._landmarker is None:
            raise RuntimeError("FaceLandmarkDetector has been closed")

        now = time.monotonic() if timestamp is None else timestamp

        # MediaPipe expects RGB; OpenCV delivers BGR. Getting this wrong does not
        # error - it silently degrades detection on skin tones, which is exactly
        # the kind of bug that hides until a demo.
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)

        # VIDEO mode requires strictly increasing timestamps. At 25 FPS a
        # millisecond conversion can repeat, so the counter is forced forward.
        timestamp_ms = int(now * 1000)
        if timestamp_ms <= self._last_timestamp_ms:
            timestamp_ms = self._last_timestamp_ms + 1
        self._last_timestamp_ms = timestamp_ms

        started = time.perf_counter()
        result = self._landmarker.detect_for_video(mp_image, timestamp_ms)
        inference_ms = (time.perf_counter() - started) * 1000.0

        return LandmarkFrame(
            faces=self._parse(result),
            timestamp=now,
            inference_ms=inference_ms,
        )

    def _parse(self, result: object) -> list[FaceResult]:
        """Convert a FaceLandmarkerResult into our own structures."""
        landmark_sets = getattr(result, "face_landmarks", None) or []
        blendshape_sets = getattr(result, "face_blendshapes", None) or []
        matrices = getattr(result, "facial_transformation_matrixes", None) or []

        faces: list[FaceResult] = []
        for index, landmarks in enumerate(landmark_sets):
            points = np.array(
                [(point.x, point.y, point.z) for point in landmarks],
                dtype=np.float32,
            )

            blendshapes: dict[str, float] = {}
            if index < len(blendshape_sets):
                blendshapes = {
                    category.category_name: float(category.score)
                    for category in blendshape_sets[index]
                }

            matrix: np.ndarray | None = None
            if index < len(matrices):
                matrix = np.array(matrices[index], dtype=np.float32).reshape(4, 4)

            faces.append(
                FaceResult(
                    landmarks=points,
                    blendshapes=blendshapes,
                    transform_matrix=matrix,
                )
            )

        return faces
