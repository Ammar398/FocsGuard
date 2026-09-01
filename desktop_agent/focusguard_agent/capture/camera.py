"""Threaded webcam capture with health tracking and reconnection.

DESIGN: A LATEST-FRAME SLOT, NOT A QUEUE
----------------------------------------
A background thread reads frames continuously and stores each one in a single
slot, overwriting whatever was there. When the vision pipeline is slower than the
camera - which it always is - the frames it never collected are simply dropped.

A queue would be the obvious alternative and is the wrong choice. It would grow
whenever processing fell behind, so the pipeline would analyse older and older
frames and the focus score would describe the past rather than the present. That
matters more than usual here, because a phone camera streaming over Wi-Fi already
adds latency of its own. Dropping frames is not a compromise; it is the correct
behaviour for a real-time monitor.

DESIGN: CAMERA HEALTH IS NOT USER BEHAVIOUR
-------------------------------------------
A face detector handed no usable frames finds no face, and scoring that as
FACE_ABSENT would penalise a student for a technical failure they did not cause.
Camera health is therefore a first-class signal that the event engine consumes
before any behavioural conclusion is drawn (spec sections 11, 61).

There are **two** distinct failures, and only checking for the first is a trap
this project fell into:

* ``UNAVAILABLE`` - frames stopped arriving. Camo Studio closed, device unplugged.
* ``NO_SIGNAL``   - frames are still arriving, but they are not live video.

The second is what a **locked phone** actually produces. Camo does not stop
streaming when the handset locks; it substitutes a static placeholder image and
keeps sending it at full rate. Every naive health check reports a perfectly
healthy camera, the detector finds nobody, and FACE_ABSENT fires against a user
who did nothing wrong. Detecting a stopped stream is not enough; a frozen one has
to be caught too.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum

import cv2
import numpy as np

from focusguard_agent.capture.devices import CaptureDevice, find_device
from focusguard_agent.config import CameraSettings
from focusguard_shared.utilities import get_logger

logger = get_logger("capture.camera")

# OpenCV's C++ layer prints its own warnings straight to stderr, bypassing Python
# logging entirely. A camera that is registered but not streaming - a very normal
# state for a virtual camera whose app is closed - produces several lines per
# retry, which buries our own diagnostics. We handle and report those failures
# ourselves, so the native chatter is silenced.
try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except Exception:  # noqa: BLE001 - purely cosmetic, never worth failing over
    logger.debug("Could not set the OpenCV log level", exc_info=True)

#: OpenCV backend for each configured name. DirectShow is the default because
#: virtual cameras are considerably more reliable under it than under MSMF.
_BACKENDS: dict[str, int] = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "any": cv2.CAP_ANY,
}

_ROTATIONS: dict[int, int] = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


def fit_within(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    """Downscale to fit inside the box WITHOUT distorting the aspect ratio.

    This is a correctness requirement, not a cosmetic one. Camo delivers 1280x720
    (16:9) and ignores every request for anything smaller - measured on this
    machine, every mode from 320x180 to 960x540 comes back as 720p. Squeezing
    that into a 4:3 640x480 frame would compress the image horizontally by 25%,
    and the vision pipeline reads geometry directly off the pixels:

    * EAR is a ratio of eye height to eye WIDTH, so horizontal compression would
      inflate it by about a third and make the blink thresholds meaningless.
    * Head yaw is inferred from a face that would no longer have face proportions.

    Preserving the aspect ratio keeps 1280x720 to 640x360, which is smaller than
    the 640x480 budget and geometrically faithful. Images already inside the box
    are returned untouched.
    """
    height, width = image.shape[:2]
    if width <= max_width and height <= max_height:
        return image

    scale = min(max_width / width, max_height / height)
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA)


def frame_signature(image: np.ndarray) -> np.ndarray:
    """Cheap 64x36 greyscale fingerprint used to spot a frozen feed."""
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.resize(grey, (64, 36), interpolation=cv2.INTER_AREA).astype(np.int16)


class CameraState(StrEnum):
    """Health of the capture device."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    OK = "OK"
    #: Frames have stopped arriving. A technical fault, never a behavioural one.
    UNAVAILABLE = "UNAVAILABLE"
    #: Frames ARE arriving, but they are not live video - every one is identical.
    #:
    #: This is the state a locked phone actually produces. Camo does not stop
    #: streaming when the handset locks; it substitutes a static placeholder image
    #: and keeps sending it. To a naive health check the camera looks perfectly
    #: healthy, so the face detector finds nobody and FACE_ABSENT fires - which is
    #: exactly the false accusation the design exists to prevent.
    #:
    #: Measured on this machine: 199 consecutive placeholder frames had a mean
    #: absolute difference of exactly 0.0000. A real sensor never does that, because
    #: even a perfectly still scene carries noise. Bit-identical frames therefore
    #: mean a synthetic source, and this is treated like any other camera fault:
    #: monitoring suspends and nothing is attributed to the user.
    NO_SIGNAL = "NO_SIGNAL"


@dataclass(frozen=True)
class Frame:
    """One captured frame with the monotonic time it was taken."""

    image: np.ndarray
    timestamp: float
    index: int

    @property
    def size(self) -> tuple[int, int]:
        height, width = self.image.shape[:2]
        return width, height


@dataclass
class CameraStats:
    """Counters for the debug screen and the benchmark."""

    frames_captured: int = 0
    frames_delivered: int = 0
    frames_dropped: int = 0
    reconnect_attempts: int = 0
    open_failures: int = 0
    actual_width: int = 0
    actual_height: int = 0
    actual_fps: float = 0.0
    _capture_times: list[float] = field(default_factory=list, repr=False)

    @property
    def drop_rate(self) -> float:
        """Fraction of captured frames the consumer never collected.

        A high drop rate is expected and healthy - it means the camera is faster
        than the pipeline, which is the intended design.
        """
        if self.frames_captured == 0:
            return 0.0
        return self.frames_dropped / self.frames_captured

    def measured_fps(self) -> float:
        """Capture rate over the recent window, independent of the requested FPS."""
        if len(self._capture_times) < 2:
            return 0.0
        span = self._capture_times[-1] - self._capture_times[0]
        return (len(self._capture_times) - 1) / span if span > 0 else 0.0


class CameraSource:
    """Background webcam reader exposing the most recent frame.

    Typical use::

        with CameraSource(settings.camera) as camera:
            frame = camera.read()
            if frame is not None:
                ...
    """

    #: Rolling window used to measure the real capture rate.
    _FPS_WINDOW = 60

    def __init__(self, settings: CameraSettings) -> None:
        self._settings = settings
        self._capture: cv2.VideoCapture | None = None
        self._device: CaptureDevice | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self._latest: Frame | None = None
        self._latest_collected = False
        self._frame_index = 0
        self._last_frame_at: float | None = None

        self._signature: np.ndarray | None = None
        self._static_since: float | None = None

        self._state = CameraState.STOPPED
        self.stats = CameraStats()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Open the camera and begin reading. Returns True if the device opened.

        A False return is not fatal: the reader thread keeps retrying, so a
        camera plugged in later will still be picked up.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.debug("CameraSource already running")
            return self._state is CameraState.OK

        self._stop_event.clear()
        self._state = CameraState.STARTING
        opened = self._open()

        self._thread = threading.Thread(target=self._run, name="camera-capture", daemon=True)
        self._thread.start()
        return opened

    def stop(self) -> None:
        """Stop reading and release the device."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._close()
        self._state = CameraState.STOPPED

    def __enter__(self) -> CameraSource:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- reading -----------------------------------------------------------

    def read(self) -> Frame | None:
        """Return the newest frame, or None if none is currently available.

        Returning the same frame twice is deliberate: a consumer running faster
        than the camera should see the current image rather than block. Only
        genuinely new frames count towards ``frames_delivered``.
        """
        with self._lock:
            frame = self._latest
            if frame is not None and not self._latest_collected:
                self._latest_collected = True
                self.stats.frames_delivered += 1
            return frame

    def read_new(self) -> Frame | None:
        """Return a frame only if it has not been returned before."""
        with self._lock:
            if self._latest is None or self._latest_collected:
                return None
            self._latest_collected = True
            self.stats.frames_delivered += 1
            return self._latest

    # -- health ------------------------------------------------------------

    @property
    def state(self) -> CameraState:
        """Current health, recomputed from how long ago the last frame arrived."""
        with self._lock:
            if self._state in (CameraState.STOPPED, CameraState.STARTING):
                return self._state

            if self._last_frame_at is None:
                return CameraState.UNAVAILABLE

            now = time.monotonic()
            if now - self._last_frame_at > self._settings.unavailable_after_seconds:
                return CameraState.UNAVAILABLE

            # Frames are arriving, but are they live? A frozen feed is checked
            # second, because a stopped feed is the more fundamental fault.
            if (
                self._static_since is not None
                and now - self._static_since > self._settings.static_feed_seconds
            ):
                return CameraState.NO_SIGNAL

            return CameraState.OK

    @property
    def is_available(self) -> bool:
        return self.state is CameraState.OK

    @property
    def device(self) -> CaptureDevice | None:
        return self._device

    # -- internals ---------------------------------------------------------

    def _open(self) -> bool:
        """Resolve the configured device and open it with the requested format."""
        self._close()

        device = find_device(self._settings.device_name, self._settings.device_index)
        if device is None:
            self.stats.open_failures += 1
            return False

        backend = _BACKENDS.get(self._settings.backend.lower(), cv2.CAP_DSHOW)
        capture = cv2.VideoCapture(device.index, backend)

        if not capture.isOpened():
            logger.warning("Failed to open camera %s", device)
            capture.release()
            self.stats.open_failures += 1
            return False

        # Request a small frame before reading anything. Phone cameras default to
        # 1080p or higher, and downscaling every frame would waste the CPU budget
        # that the vision pipeline needs.
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._settings.frame_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._settings.frame_height)
        capture.set(cv2.CAP_PROP_FPS, self._settings.target_fps)

        # A one-frame buffer keeps latency from accumulating over a Wi-Fi link.
        # Not every driver honours it, so failure here is logged, not fatal.
        if not capture.set(cv2.CAP_PROP_BUFFERSIZE, self._settings.buffer_size):
            logger.debug("Driver ignored CAP_PROP_BUFFERSIZE")

        self._capture = capture
        self._device = device

        self.stats.actual_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.stats.actual_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.stats.actual_fps = float(capture.get(cv2.CAP_PROP_FPS))

        requested = (self._settings.frame_width, self._settings.frame_height)
        actual = (self.stats.actual_width, self.stats.actual_height)
        if actual != requested and all(actual):
            # Camo and similar virtual cameras expose a single fixed mode and
            # ignore the request entirely, so this is expected rather than a
            # fault. The frames are downscaled in software, preserving aspect.
            scale = min(requested[0] / actual[0], requested[1] / actual[1])
            processed = (round(actual[0] * scale), round(actual[1] * scale))
            logger.info(
                "Camera %s only offers %dx%d (requested %dx%d); "
                "downscaling to %dx%d in software, aspect preserved",
                device.name,
                *actual,
                *requested,
                *processed,
            )

        logger.info(
            "Opened camera %s at %dx%d, driver reports %.1f FPS",
            device,
            *actual,
            self.stats.actual_fps,
        )
        return True

    def _close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        # A reopened device starts a fresh comparison; the last frame from the
        # previous session must not be compared against the first of the next.
        self._signature = None
        self._static_since = None

    def _transform(self, image: np.ndarray) -> np.ndarray:
        """Apply configured rotation, flip and downscale.

        Phone cameras may be mounted rotated and are often mirrored, so both are
        configuration rather than assumptions baked into the pipeline.
        """
        rotation = _ROTATIONS.get(self._settings.rotate_degrees % 360)
        if rotation is not None:
            image = cv2.rotate(image, rotation)

        if self._settings.flip_horizontal:
            image = cv2.flip(image, 1)

        return fit_within(image, self._settings.frame_width, self._settings.frame_height)

    def _record_capture_time(self, now: float) -> None:
        times = self.stats._capture_times
        times.append(now)
        if len(times) > self._FPS_WINDOW:
            del times[: len(times) - self._FPS_WINDOW]

    def _run(self) -> None:
        """Reader loop. Reconnects on failure and never raises."""
        consecutive_failures = 0

        while not self._stop_event.is_set():
            if self._capture is None:
                self.stats.reconnect_attempts += 1
                if not self._open():
                    # Wait before retrying so a missing camera does not spin the CPU.
                    self._stop_event.wait(self._settings.reconnect_interval_seconds)
                    continue
                consecutive_failures = 0

            try:
                ok, image = self._capture.read()  # type: ignore[union-attr]
            except cv2.error:
                logger.warning("OpenCV error while reading frame", exc_info=True)
                ok, image = False, None

            if not ok or image is None or image.size == 0:
                consecutive_failures += 1
                # A few dropped reads are normal on a wireless camera. A sustained
                # run of them means the device is genuinely gone, so reopen it.
                if consecutive_failures >= 10:
                    logger.warning("Camera stopped delivering frames; reconnecting")
                    self._close()
                    consecutive_failures = 0
                    self._stop_event.wait(self._settings.reconnect_interval_seconds)
                else:
                    self._stop_event.wait(0.02)
                continue

            consecutive_failures = 0
            now = time.monotonic()

            # A frozen feed is a soft failure: frames keep arriving, so the read
            # loop above never notices anything wrong and would sit on a dead
            # picture indefinitely. Reopening the device is what actually revives
            # a virtual camera whose phone dropped out.
            if (
                self._static_since is not None
                and now - self._static_since > self._settings.no_signal_reconnect_seconds
            ):
                logger.warning(
                    "Feed frozen for %.0fs; reopening the camera",
                    now - self._static_since,
                )
                self._close()
                self._stop_event.wait(self._settings.reconnect_interval_seconds)
                continue

            try:
                image = self._transform(image)
            except cv2.error:
                logger.warning("Frame transform failed", exc_info=True)
                continue

            # Compare against the previous frame to detect a frozen feed. The
            # 64x36 fingerprint makes this cost a few microseconds per frame.
            try:
                signature = frame_signature(image)
            except cv2.error:
                signature = None

            if signature is not None:
                if self._signature is not None:
                    difference = float(np.abs(signature - self._signature).mean())
                    if difference <= self._settings.static_feed_tolerance:
                        if self._static_since is None:
                            self._static_since = now
                    else:
                        self._static_since = None
                self._signature = signature

            with self._lock:
                # An uncollected frame being overwritten is a drop. This is the
                # expected steady state, not an error.
                if self._latest is not None and not self._latest_collected:
                    self.stats.frames_dropped += 1

                self._frame_index += 1
                self._latest = Frame(image=image, timestamp=now, index=self._frame_index)
                self._latest_collected = False
                self._last_frame_at = now
                self._state = CameraState.OK
                self.stats.frames_captured += 1
                self._record_capture_time(now)

        self._close()
