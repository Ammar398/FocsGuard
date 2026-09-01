"""Live camera preview for verifying capture setup.

Run it to confirm the right camera opens, the image is the right way up, and
frames keep flowing::

    python -m focusguard_agent.tools.camera_test
    python -m focusguard_agent.tools.camera_test --list
    python -m focusguard_agent.tools.camera_test --device DroidCam --rotate 90

This is the first thing to reach for when the phone camera misbehaves. Lock the
phone or close Camo Studio while it runs and the overlay should turn red and read
UNAVAILABLE within about two seconds, then recover on its own when the camera
comes back. That behaviour is what stops a dropped camera being recorded as the
user leaving their desk.

Press q or Esc to quit.
"""

from __future__ import annotations

import argparse
import time

import cv2

from focusguard_agent.capture.camera import CameraSource, CameraState
from focusguard_agent.capture.devices import enumerate_devices
from focusguard_agent.config import get_settings
from focusguard_shared.utilities import configure_logging, get_logger

logger = get_logger("tools.camera_test")

_STATE_COLOURS: dict[CameraState, tuple[int, int, int]] = {
    CameraState.OK: (80, 220, 100),
    CameraState.STARTING: (0, 200, 255),
    CameraState.UNAVAILABLE: (60, 60, 240),
    CameraState.NO_SIGNAL: (60, 60, 240),
    CameraState.STOPPED: (160, 160, 160),
}


def list_devices() -> int:
    devices = enumerate_devices()
    if not devices:
        print("No video capture devices found.")
        print("If you are using a phone camera, start Camo Studio or DroidCam first.")
        return 1

    print("Video capture devices (index order matches OpenCV CAP_DSHOW):")
    for device in devices:
        print(f"  {device}")
    return 0


def _draw_overlay(image, camera: CameraSource, new_frame_fps: float) -> None:
    """Draw status text. MediaPipe 1.x removed drawing_utils, so this is ours."""
    state = camera.state
    colour = _STATE_COLOURS.get(state, (200, 200, 200))
    stats = camera.stats

    # Report BOTH resolutions. The camera's native mode and the size the pipeline
    # actually works on differ whenever the driver ignores our request - Camo
    # always sends 1280x720 and we downscale to 640x360 - and showing only the
    # camera figure would misrepresent what the detectors receive.
    camera_size = f"{stats.actual_width}x{stats.actual_height}"
    processed_size = f"{image.shape[1]}x{image.shape[0]}"
    size_line = (
        f"size     {processed_size}"
        if processed_size == camera_size
        else f"size     {processed_size}  (camera {camera_size})"
    )

    lines = [
        f"{camera.device.name if camera.device else 'no device'}",
        f"state    {state}",
        f"capture  {stats.measured_fps():5.1f} FPS   new frames {new_frame_fps:5.1f} FPS",
        size_line,
        f"frames   {stats.frames_captured}  dropped {stats.frames_dropped}"
        f" ({stats.drop_rate * 100:.0f}%)",
    ]
    if stats.reconnect_attempts > 1:
        lines.append(f"reconnects {stats.reconnect_attempts - 1}")

    # Dark strip behind the text so it stays readable over any image.
    overlay = image.copy()
    cv2.rectangle(overlay, (0, 0), (image.shape[1], 22 + 20 * len(lines)), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.45, image, 0.55, 0, image)

    for row, text in enumerate(lines):
        cv2.putText(
            image,
            text,
            (12, 26 + 20 * row),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            colour if row <= 1 else (235, 235, 235),
            1,
            cv2.LINE_AA,
        )


def run_preview(device: str | None, rotate: int | None, flip: bool | None) -> int:
    settings = get_settings().camera.model_copy()
    if device is not None:
        settings.device_name = device
    if rotate is not None:
        settings.rotate_degrees = rotate
    if flip is not None:
        settings.flip_horizontal = flip

    print(f"Opening camera matching {settings.device_name!r} ... press q to quit")

    window = "FocusGuard - camera test"
    frame_times: list[float] = []
    last_frame_index = -1
    last_state: CameraState | None = None

    with CameraSource(settings) as camera:
        # Give the reader thread a moment to produce a first frame.
        deadline = time.monotonic() + settings.open_timeout_seconds
        while camera.read() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        if camera.read() is None:
            print("\nNo frames received.")
            print("  - Is Camo Studio (or DroidCam) running and connected?")
            print("  - Run with --list to see the devices Windows reports.")
            return 1

        while True:
            frame = camera.read()
            state = camera.state

            if state is not last_state:
                logger.info("Camera state -> %s", state)
                last_state = state

            if frame is None:
                time.sleep(0.02)
                continue

            # Count only genuinely new frames. Timing every loop iteration would
            # measure how fast this loop spins - the previous version reported
            # over 300 "FPS" from a 25 FPS camera, because read() returns the
            # current frame again rather than blocking.
            if frame.index != last_frame_index:
                last_frame_index = frame.index
                frame_times.append(time.monotonic())
                if len(frame_times) > 30:
                    del frame_times[:-30]

            span = frame_times[-1] - frame_times[0] if len(frame_times) > 1 else 0.0
            new_frame_fps = (len(frame_times) - 1) / span if span > 0 else 0.0

            canvas = frame.image.copy()
            _draw_overlay(canvas, camera, new_frame_fps)
            cv2.imshow(window, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

    cv2.destroyAllWindows()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard camera preview and diagnostics")
    parser.add_argument("--list", action="store_true", help="list capture devices and exit")
    parser.add_argument("--device", help="device name fragment, e.g. Camo or DroidCam")
    parser.add_argument("--rotate", type=int, choices=[0, 90, 180, 270], help="rotate frames")
    parser.add_argument("--flip", action="store_true", help="mirror horizontally")
    args = parser.parse_args()

    configure_logging(level="INFO")

    if args.list:
        return list_devices()

    return run_preview(args.device, args.rotate, args.flip if args.flip else None)


if __name__ == "__main__":
    raise SystemExit(main())
