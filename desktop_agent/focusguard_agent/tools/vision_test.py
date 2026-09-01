"""Live vision debug view - landmarks, presence and eye state.

    python -m focusguard_agent.tools.vision_test
    python -m focusguard_agent.tools.vision_test --mesh      # all 478 points
    python -m focusguard_agent.tools.vision_test --device DroidCam

What to check while it runs:

* Landmarks sit steadily on your face; eye contours and iris dots follow your eyes.
* Blink normally - the blink counter rises and NO event is recorded.
* Close your eyes for 3 seconds - EYES_CLOSED appears with its duration.
* Step out of frame for 5 seconds - FACE_ABSENT appears.
* Lock your phone - the panel turns red, monitoring SUSPENDS, and no event is
  recorded, because a dead camera is a technical fault and not a distraction.

If `ear` sits near your threshold with your eyes open, run the calibration:
    python -m focusguard_agent.tools.calibrate

Completed events print to the terminal as they close. Press q or Esc to quit.
"""

from __future__ import annotations

import argparse
import time

import cv2

from focusguard_agent.calibration.ear_calibration import load_profile
from focusguard_agent.capture.camera import CameraSource, CameraState
from focusguard_agent.config import get_settings
from focusguard_agent.events.attention import AttentionMonitor, AttentionObservation
from focusguard_agent.events.drowsiness import DrowsinessMonitor, DrowsinessObservation
from focusguard_agent.events.eyes import EyeMonitor, EyeObservation
from focusguard_agent.events.phone import PhoneMonitor, PhoneObservation
from focusguard_agent.events.presence import FacePresenceMonitor, PresenceObservation
from focusguard_agent.events.state_machine import BehaviorEvent
from focusguard_agent.scoring.engine import FocusScoringEngine, FocusSnapshot
from focusguard_agent.vision.drawing import (
    draw_bounding_box,
    draw_landmarks,
    draw_status_panel,
)
from focusguard_agent.vision.eye_analysis import EyeState, analyse_eyes
from focusguard_agent.vision.gaze import GazeResult, estimate_gaze
from focusguard_agent.vision.head_pose import HeadPoseResult, estimate_head_pose
from focusguard_agent.vision.landmarks import FaceLandmarkDetector
from focusguard_agent.vision.phone_detector import PhoneDetector
from focusguard_agent.vision.scheduling import FrameRateLimiter
from focusguard_shared.constants import GazeDirection, HeadPose, VisionEventType
from focusguard_shared.utilities import configure_logging, get_logger

logger = get_logger("tools.vision_test")

_GREEN = (120, 220, 120)
_AMBER = (0, 200, 255)
_RED = (60, 60, 240)
_GREY = (190, 190, 190)


def _status_lines(
    camera: CameraSource,
    presence: FacePresenceMonitor,
    eyes: EyeMonitor,
    drowsiness: DrowsinessMonitor,
    attention: AttentionMonitor,
    phone: PhoneMonitor,
    snapshot: FocusSnapshot | None,
    eye_state: EyeState | None,
    pose: HeadPoseResult | None,
    gaze: GazeResult | None,
    face_count: int,
    ear_threshold: float,
    calibrated: bool,
    inference_ms: float,
    phone_ms: float,
    phone_available: bool,
    detect_fps: float,
    now: float,
) -> list[tuple[str, tuple[int, int, int]]]:
    state = camera.state

    if state is CameraState.NO_SIGNAL:
        camera_colour = _RED
        camera_text = "camera    NO_SIGNAL (frozen feed) - not scored"
    elif state is not CameraState.OK:
        camera_colour = _RED
        camera_text = f"camera    {state} - not scored"
    else:
        camera_colour = _GREEN
        camera_text = f"camera    {state}"

    if presence.suspended:
        presence_text, presence_colour = "presence  SUSPENDED (camera down)", _RED
    elif presence.face_absent:
        presence_text, presence_colour = "presence  FACE_ABSENT", _RED
    elif presence.multiple_faces:
        presence_text, presence_colour = "presence  MULTIPLE_FACES", _AMBER
    elif face_count > 0:
        presence_text, presence_colour = "presence  OK", _GREEN
    else:
        presence_text, presence_colour = "presence  no face (within grace)", _AMBER

    # Eye line. EAR decides; the blendshapes say how sure we are, so a
    # disagreement is surfaced rather than hidden behind a confident-looking value.
    if eyes.suspended or eye_state is None or not eye_state.valid:
        eye_text, eye_colour = "eyes      no reading", _GREY
    elif eyes.eyes_closed:
        eye_text, eye_colour = "eyes      EYES_CLOSED", _RED
    elif eye_state.eyes_closed:
        eye_text, eye_colour = "eyes      closing (within grace)", _AMBER
    elif not eye_state.signals_agree:
        eye_text, eye_colour = "eyes      open (signals disagree)", _AMBER
    else:
        eye_text, eye_colour = "eyes      open", _GREEN

    # Drowsiness is an interpretation, so the corroborating evidence is shown
    # alongside it rather than a bare verdict (spec sections 9, 41, 61).
    indicators = drowsiness.indicators
    if drowsiness.is_drowsy:
        reasons = ", ".join(indicators.corroborating) or "long closure only"
        drowsy_text, drowsy_colour = f"drowsy    DROWSINESS ({reasons})", _RED
    elif indicators.perclos_elevated or indicators.slow_closures_elevated:
        drowsy_text, drowsy_colour = "drowsy    signs present, no closure", _AMBER
    else:
        drowsy_text, drowsy_colour = "drowsy    no", _GREEN

    # Head pose. Angles are from the user's own perspective, so with a
    # non-mirrored camera a LEFT turn appears on the right of the preview.
    if pose is None or not pose.valid:
        pose_text, pose_colour = "head      no reading", _GREY
        angles_text = "angles    -"
    else:
        pose_colour = _GREEN if pose.is_centred else _AMBER
        if attention.head_down:
            pose_text, pose_colour = "head      HEAD_DOWN", _RED
        elif not pose.signals_agree:
            pose_text = f"head      {pose.direction} (signals disagree)"
            pose_colour = _AMBER
        else:
            pose_text = f"head      {pose.direction}"
        angles_text = (
            f"angles    yaw {pose.yaw:+6.1f}  pitch {pose.pitch:+6.1f}  "
            f"roll {pose.roll:+6.1f}"
        )

    # Gaze. UNKNOWN is a normal outcome, not a failure - and it never counts as
    # looking away (spec section 61).
    if attention.suppressed_by_activity:
        gaze_text, gaze_colour = "gaze      away, allowed (productive app)", _AMBER
    elif attention.gaze_away:
        gaze_text, gaze_colour = "gaze      GAZE_AWAY", _RED
    elif gaze is None or not gaze.valid or gaze.low_confidence:
        confidence = gaze.confidence if gaze else 0.0
        gaze_text = f"gaze      UNKNOWN (confidence {confidence:.2f}) - not scored"
        gaze_colour = _GREY
    elif gaze.looking_at_screen:
        gaze_text = f"gaze      SCREEN ({gaze.confidence:.2f})"
        gaze_colour = _GREEN
    else:
        gaze_text = f"gaze      {gaze.direction} ({gaze.confidence:.2f})"
        gaze_colour = _AMBER

    # Phone. One event per continuous appearance, never one per frame.
    if not phone_available:
        phone_text, phone_colour = "phone     detector unavailable", _GREY
    elif phone.phone_visible:
        phone_text = f"phone     PHONE_VISIBLE  {phone.current_duration(now):4.1f}s"
        phone_colour = _RED
    else:
        phone_text = (
            f"phone     none   {phone.event_count} appearances, "
            f"{phone.total_seconds:.0f}s total"
        )
        phone_colour = _GREEN

    ear_value = eye_state.ear_average if eye_state and eye_state.valid else 0.0
    source = "calibrated" if calibrated else "default"

    if snapshot is None:
        score_line = ("score     starting ...", _GREY)
    elif snapshot.suspended:
        score_line = ("score     PAUSED (camera down, not scored)", _GREY)
    else:
        penalty = f"-{snapshot.penalty:.0f}" if snapshot.penalty else "0"
        score_line = (
            f"score     {snapshot.combined_focus_score:5.1f}   "
            f"instant {snapshot.instant_score:5.1f}   penalty {penalty}",
            _GREEN if snapshot.combined_focus_score >= 80 else _AMBER,
        )

    return [
        score_line,
        (camera_text, camera_colour),
        (presence_text, presence_colour),
        (eye_text, eye_colour),
        (pose_text, pose_colour),
        (gaze_text, gaze_colour),
        (angles_text, _GREY),
        (phone_text, phone_colour),
        (drowsy_text, drowsy_colour),
        (f"perclos   {indicators.perclos * 100:4.0f}%   "
         f"slow closures {indicators.slow_closure_count}", _GREY),
        (f"ear       {ear_value:.3f}  threshold {ear_threshold:.3f} ({source})", _GREY),
        (
            f"blinks    {eyes.blink_count}   "
            f"{eyes.blink_rate_per_minute(now):.0f}/min",
            _GREY,
        ),
        (
            f"faces     {face_count}   landmark {inference_ms:5.1f} ms   "
            f"phone {phone_ms:5.1f} ms",
            _GREY,
        ),
        (f"rate      detect {detect_fps:4.1f} Hz   capture "
         f"{camera.stats.measured_fps():4.1f} FPS", _GREY),
    ]


def _draw_score(image, snapshot: FocusSnapshot | None) -> None:
    """Draw the focus score large, top-right - the number a user actually reads."""
    if snapshot is None:
        return

    score = snapshot.combined_focus_score
    if snapshot.suspended:
        colour, label = _GREY, "PAUSED"
    elif score >= 80:
        colour, label = _GREEN, "FOCUS"
    elif score >= 55:
        colour, label = _AMBER, "FOCUS"
    else:
        colour, label = _RED, "FOCUS"

    height, width = image.shape[:2]
    x = width - 150

    overlay = image.copy()
    cv2.rectangle(overlay, (x - 12, 8), (width - 8, 92), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, image, 0.5, 0, image)

    cv2.putText(image, label, (x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 1, cv2.LINE_AA)
    cv2.putText(
        image, f"{score:.0f}", (x, 72), cv2.FONT_HERSHEY_SIMPLEX, 1.4, colour, 3, cv2.LINE_AA
    )
    cv2.putText(
        image,
        f"avg {snapshot.session_average:.0f}",
        (x + 72, 72),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        _GREY,
        1,
        cv2.LINE_AA,
    )


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

    profile = load_profile()
    ear_threshold = profile.ear_threshold if profile else settings.vision.default_ear_threshold
    if profile:
        print(f"Using calibrated EAR threshold {ear_threshold:.3f}")
    else:
        print(f"No calibration found; using default EAR threshold {ear_threshold:.3f}")
        print("Run 'python -m focusguard_agent.tools.calibrate' for a personal threshold.")

    limiter = FrameRateLimiter(settings.vision.landmark_fps)
    presence = FacePresenceMonitor(settings.vision)
    eyes = EyeMonitor(settings.vision)
    drowsiness = DrowsinessMonitor(settings.vision)
    attention = AttentionMonitor(settings.vision, settings.scoring)
    phone = PhoneMonitor(settings.vision)
    scoring = FocusScoringEngine(settings.scoring)
    score_limiter = FrameRateLimiter(1.0 / settings.scoring.score_sample_interval_seconds)

    # Phone detection is optional: the ONNX model has to be exported first, and
    # the rest of the pipeline must still run without it.
    phone_detector = None
    phone_limiter = FrameRateLimiter(settings.vision.phone_detection_fps)
    try:
        phone_detector = PhoneDetector(
            model_path=settings.model_dir / "yolov8n.onnx",
            input_size=settings.vision.yolo_input_size,
            confidence_threshold=settings.vision.phone_confidence_threshold,
        )
        print(f"Phone detection enabled on {phone_detector.active_provider}")
    except (FileNotFoundError, ImportError, RuntimeError) as error:
        print(f"Phone detection disabled: {error}")
        print("Run scripts/export_yolo.ps1 to enable it.")

    print(f"\nOpening camera matching {camera_settings.device_name!r} ... press q to quit\n")

    window = "FocusGuard - vision test"
    face_count = 0
    inference_ms = 0.0
    detect_times: list[float] = []
    last_frame_index = -1
    eye_state: EyeState | None = None
    pose: HeadPoseResult | None = None
    phone_ms = 0.0
    snapshot: FocusSnapshot | None = None
    last_phone_confidence = 0.8
    phone_boxes: list = []
    gaze: GazeResult | None = None

    # The overlay persists between detections: detection runs at ~12.5 Hz while
    # this loop spins far faster, so clearing it every iteration would leave most
    # frames undrawn and the overlay would flicker.
    faces_px: list = []

    with CameraSource(camera_settings) as camera, FaceLandmarkDetector() as detector:
        deadline = time.monotonic() + camera_settings.open_timeout_seconds
        while camera.read() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        if camera.read() is None:
            print("No frames received. Is Camo Studio running and showing your face?")
            print("Try: python -m focusguard_agent.tools.camera_test --list")
            return 1

        while True:
            now = time.monotonic()
            frame = camera.read()
            camera_ok = camera.state is CameraState.OK

            if frame is None or not camera_ok:
                for event in presence.update(
                    PresenceObservation(timestamp=now, camera_ok=False, face_count=0)
                ):
                    _report(event)
                for event in eyes.update(
                    EyeObservation(
                        timestamp=now,
                        camera_ok=False,
                        face_present=False,
                        eyes_closed=False,
                        ear_average=0.0,
                        confidence=0.0,
                        valid=False,
                    )
                ):
                    _report(event)
                for event in drowsiness.update(
                    DrowsinessObservation(
                        timestamp=now,
                        camera_ok=False,
                        face_present=False,
                        eyes_closed=False,
                        valid=False,
                    )
                ):
                    _report(event)
                for event in attention.update(
                    AttentionObservation(
                        timestamp=now, camera_ok=False, face_present=False
                    )
                ):
                    _report(event)
                for event in phone.update(
                    PhoneObservation(timestamp=now, camera_ok=False, detected=False)
                ):
                    _report(event)

                faces_px = []
                face_count = 0
                eye_state = None
                pose = None
                gaze = None
                phone_boxes = []
                time.sleep(0.03)
                if frame is None:
                    continue

            is_new = frame.index != last_frame_index
            last_frame_index = frame.index

            measured = camera.stats.measured_fps()
            frame_period = 1.0 / measured if measured > 0 else None

            if camera_ok and is_new and limiter.should_run(now, frame_period):
                result = detector.detect(frame.image, timestamp=now)
                face_count = result.face_count
                inference_ms = result.inference_ms

                height, width = frame.image.shape[:2]
                faces_px = [face.pixel_landmarks(width, height) for face in result.faces]

                primary = result.primary
                eye_state = (
                    analyse_eyes(primary, width, height, ear_threshold) if primary else None
                )
                pose = (
                    estimate_head_pose(
                        primary,
                        width,
                        height,
                        yaw_threshold=settings.vision.yaw_threshold_degrees,
                        pitch_up_threshold=settings.vision.pitch_up_threshold_degrees,
                        pitch_down_threshold=settings.vision.pitch_down_threshold_degrees,
                    )
                    if primary
                    else None
                )
                gaze = (
                    estimate_gaze(
                        primary,
                        pose,
                        width,
                        height,
                        eyes_closed=bool(eye_state and eye_state.eyes_closed),
                        min_confidence=settings.vision.gaze_min_confidence,
                    )
                    if primary and pose
                    else None
                )

                for event in presence.update(
                    PresenceObservation(
                        timestamp=now, camera_ok=True, face_count=result.face_count
                    )
                ):
                    _report(event)

                for event in eyes.update(
                    EyeObservation(
                        timestamp=now,
                        camera_ok=True,
                        face_present=primary is not None,
                        eyes_closed=bool(eye_state and eye_state.eyes_closed),
                        ear_average=eye_state.ear_average if eye_state else 0.0,
                        confidence=eye_state.confidence if eye_state else 0.0,
                        valid=bool(eye_state and eye_state.valid),
                    )
                ):
                    _report(event)

                for event in drowsiness.update(
                    DrowsinessObservation(
                        timestamp=now,
                        camera_ok=True,
                        face_present=primary is not None,
                        eyes_closed=bool(eye_state and eye_state.eyes_closed),
                        eye_confidence=eye_state.confidence if eye_state else 0.0,
                        valid=bool(eye_state and eye_state.valid),
                        head_pitch=pose.pitch if pose and pose.valid else None,
                    )
                ):
                    _report(event)

                for event in attention.update(
                    AttentionObservation(
                        timestamp=now,
                        camera_ok=True,
                        face_present=primary is not None,
                        head_direction=pose.direction if pose else HeadPose.UNKNOWN,
                        head_pitch=pose.pitch if pose else 0.0,
                        head_confidence=pose.confidence if pose else 0.0,
                        gaze_direction=gaze.direction if gaze else GazeDirection.UNKNOWN,
                        gaze_confidence=gaze.confidence if gaze else 0.0,
                        gaze_valid=bool(gaze and gaze.valid and not gaze.low_confidence),
                    )
                ):
                    _report(event)

                detect_times.append(now)
                if len(detect_times) > 20:
                    del detect_times[:-20]

            # Phone detection runs on its own, much slower schedule (~3 Hz):
            # it is the most expensive stage, and a phone appearing is measured in
            # seconds, not frames.
            if (
                phone_detector is not None
                and camera_ok
                and is_new
                and phone_limiter.should_run(now, frame_period)
            ):
                phone_result = phone_detector.detect(frame.image, timestamp=now)
                phone_ms = phone_result.inference_ms
                phone_boxes = [
                    (d.x1, d.y1, d.x2, d.y2, d.confidence) for d in phone_result.detections
                ]
                if phone_result.detected:
                    last_phone_confidence = phone_result.best_confidence

                for event in phone.update(
                    PhoneObservation(
                        timestamp=now,
                        camera_ok=True,
                        detected=phone_result.detected,
                        confidence=phone_result.best_confidence,
                    )
                ):
                    _report(event)

            # Scoring runs at its own slow cadence (1 Hz by default). A
            # frame-rate score series would add millions of rows for no
            # analytical gain (spec section 45), and the smoothing is defined per
            # second so the rate does not change the result.
            if score_limiter.should_run(now, frame_period):
                active: dict[VisionEventType, float] = {}
                if presence.face_absent:
                    active[VisionEventType.FACE_ABSENT] = 1.0
                if presence.multiple_faces:
                    active[VisionEventType.MULTIPLE_FACES] = 1.0
                if eyes.eyes_closed:
                    active[VisionEventType.EYES_CLOSED] = (
                        eye_state.confidence if eye_state else 0.9
                    )
                if drowsiness.is_drowsy:
                    active[VisionEventType.DROWSINESS] = (
                        eye_state.confidence if eye_state else 0.8
                    )
                if attention.gaze_away:
                    active[VisionEventType.GAZE_AWAY] = gaze.confidence if gaze else 0.7
                if attention.head_down:
                    active[VisionEventType.HEAD_DOWN] = pose.confidence if pose else 0.8
                if phone.phone_visible:
                    active[VisionEventType.PHONE_VISIBLE] = last_phone_confidence

                snapshot = scoring.update(
                    now,
                    active_events=active,
                    # Productivity has no input until Phase 9 supplies foreground
                    # application data; None keeps the combined score honest.
                    productivity_score=None,
                    suspended=not camera_ok or presence.suspended,
                )

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

            for x1, y1, x2, y2, confidence in phone_boxes:
                cv2.rectangle(
                    canvas, (int(x1), int(y1)), (int(x2), int(y2)), _RED, 2, cv2.LINE_AA
                )
                cv2.putText(
                    canvas,
                    f"phone {confidence:.2f}",
                    (int(x1), max(12, int(y1) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    _RED,
                    1,
                    cv2.LINE_AA,
                )

            draw_status_panel(
                canvas,
                _status_lines(
                    camera, presence, eyes, drowsiness, attention, phone,
                    snapshot, eye_state, pose, gaze, face_count,
                    ear_threshold, profile is not None, inference_ms,
                    phone_ms, phone_detector is not None, detect_fps, now,
                ),
                width=420,
            )
            _draw_score(canvas, snapshot)
            cv2.imshow(window, canvas)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break

        closing = time.monotonic()
        for event in presence.flush(closing):
            _report(event)
        for event in eyes.flush(closing):
            _report(event)
        for event in drowsiness.flush(closing):
            _report(event)
        for event in attention.flush(closing):
            _report(event)
        for event in phone.flush(closing):
            _report(event)

        summary = scoring.finish(closing)

        def _mmss(seconds: float) -> str:
            return f"{int(seconds) // 60}m {int(seconds) % 60}s"

        print("\n" + "=" * 54)
        print("SESSION SUMMARY")
        print("=" * 54)
        print(f"  Duration          : {_mmss(summary.duration_seconds)}")
        print(f"  Focused           : {_mmss(summary.focused_seconds)} "
              f"({summary.focused_ratio * 100:.0f}%)")
        print(f"  Distracted        : {_mmss(summary.distracted_seconds)}")
        if summary.suspended_seconds > 1:
            print(f"  Not scored        : {_mmss(summary.suspended_seconds)} "
                  "(camera unavailable)")
        print(f"  Average focus     : {summary.average_focus_score:.1f}")
        print(f"  Lowest focus      : {summary.lowest_focus_score:.1f}")
        print(f"  Longest focus run : {_mmss(summary.longest_focus_block_seconds)}")

        if summary.event_seconds:
            print("\n  Time per event:")
            for event_type, seconds in sorted(
                summary.event_seconds.items(), key=lambda item: -item[1]
            ):
                count = summary.event_counts.get(event_type, 0)
                print(f"    {event_type:<16} {_mmss(seconds):>10}  x{count}")
        print(f"\nBlinks this session: {eyes.blink_count}")
        print(f"Phone appearances  : {phone.event_count} "
              f"({phone.total_seconds:.0f}s total)")
        if phone_detector is not None:
            phone_detector.close()

    cv2.destroyAllWindows()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard vision debug view")
    parser.add_argument("--device", help="camera name fragment, e.g. Camo")
    parser.add_argument("--mesh", action="store_true", help="draw all 478 landmarks")
    args = parser.parse_args()

    configure_logging(level="INFO")
    return run(args.device, args.mesh)


if __name__ == "__main__":
    raise SystemExit(main())
