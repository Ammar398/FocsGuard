"""Capture-layer tests (spec sections 3, 11).

Everything here runs without a camera. The two behaviours under test are the ones
that would silently corrupt results if they were wrong:

* frames must be downscaled without distorting the image, because EAR and head
  pose are measured from pixel geometry;
* a camera that stops delivering frames must be reported as a technical fault,
  never as the user having left.
"""

from __future__ import annotations

import numpy as np
import pytest

from focusguard_agent.capture.camera import CameraSource, CameraState, fit_within
from focusguard_agent.capture.devices import CaptureDevice
from focusguard_agent.config import CameraSettings


def _image(width: int, height: int) -> np.ndarray:
    return np.zeros((height, width, 3), dtype=np.uint8)


# --- aspect-ratio preservation ---------------------------------------------


def test_widescreen_frame_keeps_its_aspect_ratio() -> None:
    """The real case: Camo delivers 1280x720 and ignores every other request.

    Forcing that into 640x480 would compress it horizontally by 25%, inflating
    EAR by roughly a third and skewing head-pose angles.
    """
    result = fit_within(_image(1280, 720), 640, 480)

    assert result.shape[:2] == (360, 640)

    source_aspect = 1280 / 720
    result_aspect = result.shape[1] / result.shape[0]
    assert result_aspect == pytest.approx(source_aspect, rel=1e-3)


def test_four_three_frame_fills_the_box_exactly() -> None:
    assert fit_within(_image(1600, 1200), 640, 480).shape[:2] == (480, 640)


def test_portrait_frame_is_bounded_by_height() -> None:
    """A phone mounted vertically produces a taller-than-wide frame."""
    result = fit_within(_image(720, 1280), 640, 480)
    assert result.shape[:2] == (480, 270)


def test_small_frame_is_not_upscaled() -> None:
    """Upscaling would invent detail and cost CPU for nothing."""
    original = _image(320, 240)
    result = fit_within(original, 640, 480)
    assert result.shape == original.shape


@pytest.mark.parametrize(("width", "height"), [(1280, 720), (1920, 1080), (800, 600), (1024, 768)])
def test_result_always_fits_inside_the_box(width: int, height: int) -> None:
    result = fit_within(_image(width, height), 640, 480)
    assert result.shape[1] <= 640
    assert result.shape[0] <= 480


# --- camera health ----------------------------------------------------------


def _camera(**overrides: object) -> CameraSource:
    settings = CameraSettings(unavailable_after_seconds=2.0, **overrides)  # type: ignore[arg-type]
    return CameraSource(settings)


def test_new_camera_is_stopped() -> None:
    assert _camera().state is CameraState.STOPPED


def test_camera_without_frames_is_unavailable_not_ok() -> None:
    camera = _camera()
    camera._state = CameraState.OK
    camera._last_frame_at = None

    assert camera.state is CameraState.UNAVAILABLE
    assert camera.is_available is False


def test_recent_frame_means_healthy() -> None:
    import time

    camera = _camera()
    camera._state = CameraState.OK
    camera._last_frame_at = time.monotonic()

    assert camera.state is CameraState.OK


def test_stale_frames_become_unavailable_after_the_grace_period() -> None:
    """The phone locked, Wi-Fi dropped, or Camo Studio was closed.

    This must surface as a camera fault. The event engine reads it before drawing
    any behavioural conclusion, so it can never be recorded as FACE_ABSENT and
    can never cost the user points.
    """
    import time

    camera = _camera()
    camera._state = CameraState.OK

    camera._last_frame_at = time.monotonic() - 1.0
    assert camera.state is CameraState.OK, "1s gap is within the 2s grace period"

    camera._last_frame_at = time.monotonic() - 5.0
    assert camera.state is CameraState.UNAVAILABLE


def test_starting_state_is_not_reported_as_a_failure() -> None:
    """Opening a wireless camera takes a moment; that is not a fault."""
    camera = _camera()
    camera._state = CameraState.STARTING
    assert camera.state is CameraState.STARTING


# --- drop accounting --------------------------------------------------------


def test_drop_rate_is_zero_before_any_frames() -> None:
    assert _camera().stats.drop_rate == 0.0


def test_drop_rate_reports_uncollected_frames() -> None:
    camera = _camera()
    camera.stats.frames_captured = 100
    camera.stats.frames_dropped = 40
    assert camera.stats.drop_rate == pytest.approx(0.4)


# --- device selection -------------------------------------------------------


def test_capture_device_renders_readably() -> None:
    assert str(CaptureDevice(index=0, name="Camo")) == "[0] Camo"


# --- frozen feed (the locked-phone case) ------------------------------------


def test_frame_signature_is_identical_for_identical_frames() -> None:
    image = np.full((360, 640, 3), 128, dtype=np.uint8)
    from focusguard_agent.capture.camera import frame_signature

    assert np.array_equal(frame_signature(image), frame_signature(image.copy()))


def test_frame_signature_differs_for_different_frames() -> None:
    from focusguard_agent.capture.camera import frame_signature

    flat = np.full((360, 640, 3), 128, dtype=np.uint8)
    noisy = flat.copy()
    noisy[100:200, 100:200] = 200

    assert not np.array_equal(frame_signature(flat), frame_signature(noisy))


def test_frozen_feed_becomes_no_signal_not_ok() -> None:
    """A locked phone keeps streaming a placeholder; that is not a healthy camera.

    Camo substitutes a static image rather than stopping, so frames keep arriving
    and a stopped-stream check alone reports OK. Measured on this machine, 199
    consecutive placeholder frames differed by exactly 0.0.
    """
    import time

    camera = _camera(static_feed_seconds=3.0)
    camera._state = CameraState.OK
    camera._last_frame_at = time.monotonic()

    camera._static_since = time.monotonic() - 1.0
    assert camera.state is CameraState.OK, "1s of stillness is not yet a frozen feed"

    camera._static_since = time.monotonic() - 5.0
    assert camera.state is CameraState.NO_SIGNAL
    assert camera.is_available is False


def test_a_live_feed_is_never_no_signal() -> None:
    """Real sensor noise means live frames are never bit-identical."""
    import time

    camera = _camera(static_feed_seconds=3.0)
    camera._state = CameraState.OK
    camera._last_frame_at = time.monotonic()
    camera._static_since = None

    assert camera.state is CameraState.OK
    assert camera.is_available is True


def test_stopped_stream_outranks_frozen_stream() -> None:
    """No frames at all is the more fundamental fault and must win."""
    import time

    camera = _camera(static_feed_seconds=3.0)
    camera._state = CameraState.OK
    camera._last_frame_at = time.monotonic() - 30.0
    camera._static_since = time.monotonic() - 30.0

    assert camera.state is CameraState.UNAVAILABLE
