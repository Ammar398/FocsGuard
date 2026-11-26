"""First-run EAR calibration (spec sections 12, 48).

    python -m focusguard_agent.tools.calibrate
    python -m focusguard_agent.tools.calibrate --seconds 15
    python -m focusguard_agent.tools.calibrate --show      # with a preview window

Sit as you normally do while working and look at the screen. **Blink normally** -
the lowest fifth of the readings are treated as blinks and discarded, so trying to
hold your eyes open produces a strained, unrepresentative result.

Why this exists: the commonly quoted 0.21 EAR threshold is an average, not a fit.
Eye shape, glasses and camera angle all shift the value, so a user whose relaxed
EAR is 0.19 would be recorded as permanently asleep, and one at 0.34 would never
register a blink.
"""

from __future__ import annotations

import argparse
import time

import cv2

from focusguard_agent.calibration.ear_calibration import (
    EarCalibrator,
    default_profile_path,
    load_profile,
    save_profile,
)
from focusguard_agent.capture.camera import CameraSource, CameraState
from focusguard_agent.config import get_settings
from focusguard_agent.vision.drawing import draw_landmarks, draw_status_panel
from focusguard_agent.vision.eye_analysis import analyse_eyes
from focusguard_agent.vision.landmarks import FaceLandmarkDetector
from focusguard_agent.vision.scheduling import FrameRateLimiter
from focusguard_shared.utilities import configure_logging


def run(seconds: float, device: str | None, show: bool) -> int:
    settings = get_settings()
    camera_settings = settings.camera.model_copy()
    if device is not None:
        camera_settings.device_name = device

    existing = load_profile()
    if existing:
        print(f"Existing calibration: threshold {existing.ear_threshold:.3f} "
              f"(from {existing.created_at[:10]}). It will be replaced.\n")

    calibrator = EarCalibrator(
        k=settings.vision.ear_calibration_k,
        minimum=settings.vision.ear_threshold_min,
        maximum=settings.vision.ear_threshold_max,
    )
    limiter = FrameRateLimiter(settings.vision.landmark_fps)

    print("=" * 60)
    print("FocusGuard EAR calibration")
    print("=" * 60)
    print("  Sit as you normally would and look at the screen.")
    print("  Blink normally - do NOT hold your eyes open.")
    print(f"  Measuring for {seconds:.0f} seconds.\n")

    window = "FocusGuard - calibration"
    skipped_no_face = 0

    with CameraSource(camera_settings) as camera, FaceLandmarkDetector(max_faces=1) as detector:
        deadline = time.monotonic() + camera_settings.open_timeout_seconds
        while camera.read() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        if camera.read() is None:
            print("No frames received. Is Camo Studio running and showing your face?")
            return 1

        start = time.monotonic()
        end = start + seconds
        last_index = -1

        while time.monotonic() < end:
            now = time.monotonic()
            frame = camera.read()

            if frame is None or camera.state is not CameraState.OK:
                time.sleep(0.05)
                continue

            if frame.index == last_index:
                time.sleep(0.002)
                continue
            last_index = frame.index

            measured = camera.stats.measured_fps()
            frame_period = 1.0 / measured if measured > 0 else None
            if not limiter.should_run(now, frame_period):
                continue

            result = detector.detect(frame.image, timestamp=now)
            primary = result.primary
            if primary is None:
                skipped_no_face += 1
                continue

            height, width = frame.image.shape[:2]
            # The threshold passed here is irrelevant to calibration: we are
            # collecting raw EAR values, not classifying them.
            state = analyse_eyes(primary, width, height, ear_threshold=0.21)
            if state.valid:
                calibrator.add(state.ear_average)

            remaining = end - now
            print(
                f"\r  {remaining:4.1f}s left   samples {calibrator.sample_count:4d}   "
                f"ear {state.ear_average:.3f}",
                end="",
                flush=True,
            )

            if show:
                canvas = frame.image.copy()
                draw_landmarks(canvas, primary.pixel_landmarks(width, height))
                draw_status_panel(
                    canvas,
                    [
                        (f"calibrating  {remaining:4.1f}s left", (120, 220, 120)),
                        (f"samples      {calibrator.sample_count}", (190, 190, 190)),
                        (f"ear          {state.ear_average:.3f}", (190, 190, 190)),
                        ("blink normally", (0, 200, 255)),
                    ],
                )
                cv2.imshow(window, canvas)
                if (cv2.waitKey(1) & 0xFF) in (ord("q"), 27):
                    break

    if show:
        cv2.destroyAllWindows()

    print("\n")

    if skipped_no_face:
        print(f"  ({skipped_no_face} frames had no face and were skipped)")

    try:
        profile = calibrator.result()
    except ValueError as error:
        print(f"Calibration failed: {error}")
        print("\nMake sure your face is visible in Camo for the whole period, then retry.")
        return 1

    path = save_profile(profile)

    print("-" * 60)
    print(f"  Open-eye EAR mean : {profile.ear_mean_open:.3f}")
    print(f"  Standard deviation: {profile.ear_std_open:.3f}")
    print(f"  Samples used      : {profile.samples_used}")
    print(f"  YOUR THRESHOLD    : {profile.ear_threshold:.3f}")
    print(f"  Default would be  : {settings.vision.default_ear_threshold:.3f}")
    print("-" * 60)

    if profile.clamped:
        print("  WARNING: the computed threshold hit the safety limit, which usually")
        print("  means the readings were erratic - poor lighting, a moving camera, or")
        print("  a face partly out of frame. Consider running calibration again.")
    else:
        difference = profile.ear_threshold - settings.vision.default_ear_threshold
        if abs(difference) < 0.02:
            print("  Your eyes are close to the default; calibration changes little.")
        else:
            direction = "higher" if difference > 0 else "lower"
            print(f"  Your threshold is {abs(difference):.3f} {direction} than the default,")
            print("  so calibration meaningfully improves accuracy for you.")

    print(f"\n  Saved to {path}")
    print("  It is used automatically from now on. Re-run this any time to update it.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard personal EAR calibration")
    parser.add_argument("--seconds", type=float, default=12.0, help="measurement window")
    parser.add_argument("--device", help="camera name fragment, e.g. Camo")
    parser.add_argument("--show", action="store_true", help="show a preview window")
    parser.add_argument(
        "--print", action="store_true", dest="print_only", help="print the saved profile and exit"
    )
    parser.add_argument(
        "--wait-on-exit",
        action="store_true",
        help="keep the window open until Enter is pressed (used when the portal opens it)",
    )
    args = parser.parse_args()

    configure_logging(level="WARNING")

    if args.print_only:
        profile = load_profile()
        if profile is None:
            print(f"No calibration saved at {default_profile_path()}")
            return 1
        print(f"Threshold  : {profile.ear_threshold:.3f}")
        print(f"Open mean  : {profile.ear_mean_open:.3f}")
        print(f"Std dev    : {profile.ear_std_open:.3f}")
        print(f"Samples    : {profile.samples_used}")
        print(f"Clamped    : {profile.clamped}")
        print(f"Created    : {profile.created_at}")
        return 0

    code = run(args.seconds, args.device, args.show)
    if args.wait_on_exit:
        # Opened from the portal in a window of its own: without this, the
        # result would print and the window close before anyone read it.
        try:
            input("\nPress Enter to close this window.")
        except EOFError:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
