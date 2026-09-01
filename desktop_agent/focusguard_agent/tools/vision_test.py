"""Live face-landmark and presence view - the Phase 3 acceptance demo.

    python -m focusguard_agent.tools.vision_test
    python -m focusguard_agent.tools.vision_test --mesh      # all 478 points
    python -m focusguard_agent.tools.vision_test --device DroidCam

What to check while it runs:

* Landmarks track your face; the eye contours and iris dots follow your eyes.
* Step out of frame. Nothing happens for the first two seconds, then FACE_ABSENT
  appears. Step back within two seconds and no event is ever recorded.
* Have a second person lean in - MULTIPLE_FACES appears after its grace period.
* Lock your phone. The panel turns red, monitoring SUSPENDS, and no FACE_ABSENT
  event is produced - a dead camera is a technical fault, not a distraction.

Completed events are printed to the terminal as they close, with their duration.
Press q or Esc to quit.
"""

from __future__ import annotations

import argparse
import time

import cv2

from focusguard_agent.capture.camera import CameraSource, CameraState
from focusguard_agent.config import get_settings
from focusguard_agent.events.presence import FacePresenceMonitor, PresenceObservation
from focusguard_agent.events.state_machine import BehaviorEvent
from focusguard_agent.vision.drawing import (
    draw_bounding_box,
    draw_landmarks,
    draw_status_panel,
)
from focusguard_agent.vision.landmarks import FaceLandmarkDetector
from focusguard_shared.utilities import configure_logging, get_logger

logger = get_logger("tools.vision_test")

_GREEN = (120, 220, 120)
_AMBER = (0, 200, 255)
_RED = (60, 60, 240)
_GREY = (190, 190, 190)


def _status_lines(
    camera: CameraSource,
    monitor: FacePresenceMonitor,
    face_count: int,
    landmark_count: int,
    inference_ms: float,
    detect_fps: float,
) -> list[tuple[str, tuple[int, int, int]]]:
    state = camera.state

    if state is CameraState.NO_SIGNAL:
        # The locked-phone case: frames are arriving but they are a frozen
        # placeholder, so nothing here may be attributed to the user.
        camera_colour = _RED
        camera_text = "camera    NO_SIGNAL (frozen feed) - not scored"
    elif state is not CameraState.OK:
        camera_colour = _RED
        camera_text = f"camera    {state} - not scored"
    else:
        camera_colour = _GREEN
        camera_text = f"camera    {state}"

    if monitor.suspended:
        presence_text, presence_colour = "presence  SUSPENDED (camera down)", _RED
    elif monitor.face_absent:
        presence_text, presence_colour = "presence  FACE_ABSENT", _RED
    elif monitor.multiple_faces:
        presence_text, presence_colour = "presence  MULTIPLE_FACES", _AMBER
    elif face_count > 0:
        presence_text, presence_colour = "presence  OK", _GREEN
    else:
        presence_text, presence_colour = "presence  no face (within grace)", _AMBER

    return [
        (camera_text, camera_colour),
        (presence_text, presence_colour),
        (f"faces     {face_count}   landmarks {landmark_count}", _GREY),
        (f"landmark  {inference_ms:5.1f} ms   {detect_fps:4.1f} FPS", _GREY),
        (f"capture   {camera.stats.measured_fps():4.1f} FPS", _GREY),
    ]


def _report(event: BehaviorEvent) -> None:
    print(
        f"  EVENT  {event.event_type:<16} "
        f"{event.duration_seconds:6.1f}s  confidence {event.confidence:.2f}"
    )


def run(device: str | None, show_mesh: bool) -> int:
    settings = get_settings()
    camera_settings = settings.camera.model_copy()
    if device is not None:
        camera_settings.device_name = device

    # Detection runs slower than capture on purpose (spec section 3). At 12 Hz the
    # landmark stage sees every second frame from a 25 FPS camera, which is ample
    # for behaviour that is measured in seconds.
    detect_interval = 1.0 / max(1e-6, settings.vision.landmark_fps)

    print(f"Opening camera matching {camera_settings.device_name!r} ... press q to quit\n")
    print(f"  grace: face absent {settings.vision.face_absent_grace_seconds}s, "
          f"multiple faces {settings.vision.multiple_faces_grace_seconds}s\n")

    monitor = FacePresenceMonitor(settings.vision)
    window = "FocusGuard - vision test"

    last_detect = 0.0
    face_count = 0
    landmark_count = 0
    inference_ms = 0.0
    detect_times: list[float] = []
    last_frame_index = -1

    with CameraSource(camera_settings) as camera, FaceLandmarkDetector() as detector:
        deadline = time.monotonic() + camera_settings.open_timeout_seconds
        while camera.read() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        if camera.read() is None:
            print("No frames received. Is Camo Studio running?")
            print("Try: python -m focusguard_agent.tools.camera_test --list")
            return 1

        while True:
            now = time.monotonic()
            frame = camera.read()
            camera_ok = camera.state is CameraState.OK

            # Even with no frames the monitor must be driven, or it would never
            # learn the camera has gone and would keep the last state forever.
            if frame is None or not camera_ok:
                for event in monitor.update(
                    PresenceObservation(timestamp=now, camera_ok=False, face_count=0)
                ):
                    _report(event)
                time.sleep(0.03)
                if frame is None:
                    continue

            is_new = frame.index != last_frame_index
            last_frame_index = frame.index

            faces_px = []
            if camera_ok and is_new and (now - last_detect) >= detect_interval:
                last_detect = now
                result = detector.detect(frame.image, timestamp=now)

                face_count = result.face_count
                inference_ms = result.inference_ms
                landmark_count = len(result.primary.landmarks) if result.primary else 0

                height, width = frame.image.shape[:2]
                faces_px = [face.pixel_landmarks(width, height) for face in result.faces]

                detect_times.append(now)
                if len(detect_times) > 20:
                    del detect_times[:-20]

                for event in monitor.update(
                    PresenceObservation(
                        timestamp=now, camera_ok=True, face_count=result.face_count
                    )
                ):
                    _report(event)

            span = detect_times[-1] - detect_times[0] if len(detect_times) > 1 else 0.0
            detect_fps = (len(detect_times) - 1) / span if span > 0 else 0.0

            canvas = frame.image.copy()
            for index, points in enumerate(faces_px):
                draw_landmarks(canvas, points, show_mesh=show_mesh)
                draw_bounding_box(
                    canvas,
                    points,
                    colour=_GREEN if index == 0 else _AMBER,
                    label="primary" if index == 0 else f"face {index + 1}",
                )

            draw_status_panel(
                canvas,
                _status_lines(
                    camera, monitor, face_count, landmark_count, inference_ms, detect_fps
                ),
            )
            cv2.imshow(window, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

        for event in monitor.flush(time.monotonic()):
            _report(event)

    cv2.destroyAllWindows()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard face landmark and presence test")
    parser.add_argument("--device", help="camera name fragment, e.g. Camo")
    parser.add_argument("--mesh", action="store_true", help="draw all 478 landmarks")
    args = parser.parse_args()

    configure_logging(level="INFO")
    return run(args.device, args.mesh)


if __name__ == "__main__":
    raise SystemExit(main())
