"""The FocusGuard agent - one process running the whole pipeline.

    python -m focusguard_agent.agent --name "Study Data Structures"

WHAT RUNS WHERE
---------------
    capture thread   camera -> latest-frame slot                (Phase 2)
    agent loop       vision -> events -> scoring -> persistence
    ipc thread       localhost server for the browser extension (Phase 10)

Three threads on a four-core machine. Detection stages run at their own rates
inside the agent loop rather than each getting a thread, because oversubscribing
four cores costs more in contention than it buys in parallelism.

EVERYTHING DEGRADES
-------------------
Each subsystem is optional and its absence is reported, never fatal:

| Missing | Result |
|---|---|
| Camera | activity monitoring continues; nothing is scored as absence |
| YOLO model | no phone detection; the rest is unaffected |
| Browser extension | browsers tracked as applications, without domains |
| Backend | events queue locally and upload later (Phase 12) |

A student with no webcam still gets application analytics; a user with no
extension still gets a focus score.

WRITE FIRST, UPLOAD LATER
-------------------------
Every event is written to the local database before anything else happens to it
(section 37). A crash loses at most the last few seconds, and a dropped network
connection costs nothing at all.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from focusguard_agent.activity_monitor.aggregator import ActivityAggregator
from focusguard_agent.activity_monitor.classifier import ActivityNormalizer
from focusguard_agent.activity_monitor.collector import WindowsActivityCollector
from focusguard_agent.activity_monitor.task_context import TaskContext, TaskContextEngine
from focusguard_agent.browser_monitor.monitor import BrowserMonitor
from focusguard_agent.calibration.ear_calibration import load_profile
from focusguard_agent.capture.camera import CameraSource, CameraState
from focusguard_agent.config import AgentSettings, get_settings
from focusguard_agent.events.attention import AttentionMonitor, AttentionObservation
from focusguard_agent.events.drowsiness import DrowsinessMonitor, DrowsinessObservation
from focusguard_agent.events.eyes import EyeMonitor, EyeObservation
from focusguard_agent.events.phone import PhoneMonitor, PhoneObservation
from focusguard_agent.events.presence import FacePresenceMonitor, PresenceObservation
from focusguard_agent.events.state_machine import BehaviorEvent
from focusguard_agent.ipc.server import IpcServer
from focusguard_agent.local_store.database import LocalStore, SessionClock
from focusguard_agent.scoring.engine import FocusScoringEngine, FocusSnapshot
from focusguard_agent.scoring.productivity import ProductivityTracker
from focusguard_agent.tools.stop import (
    clear_pid_file,
    clear_stop_flag,
    stop_requested,
    write_pid_file,
)
from focusguard_agent.vision.eye_analysis import analyse_eyes
from focusguard_agent.vision.gaze import estimate_gaze
from focusguard_agent.vision.head_pose import estimate_head_pose
from focusguard_agent.vision.landmarks import FaceLandmarkDetector
from focusguard_agent.vision.scheduling import FrameRateLimiter
from focusguard_shared.constants import GazeDirection, HeadPose, VisionEventType
from focusguard_shared.utilities import configure_logging, get_logger

logger = get_logger("agent")


@dataclass
class SubsystemStatus:
    """What actually started, so the banner can be honest about it."""

    camera: bool = False
    landmarks: bool = False
    phone: bool = False
    activity: bool = False
    ipc: bool = False


class FocusGuardAgent:
    """Runs a monitoring session end to end."""

    def __init__(
        self,
        settings: AgentSettings | None = None,
        session_name: str = "Focus session",
        task: TaskContext | None = None,
        store: LocalStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.session_name = session_name
        self.task = task

        self.store = store or LocalStore()
        self.status = SubsystemStatus()
        self._stop = threading.Event()

        self._session_id: int | None = None
        self._clock: SessionClock | None = None

        # Vision
        self._camera: CameraSource | None = None
        self._landmarks: FaceLandmarkDetector | None = None
        self._phone_detector = None

        profile = load_profile()
        self.ear_threshold = (
            profile.ear_threshold if profile else self.settings.vision.default_ear_threshold
        )
        self.calibrated = profile is not None

        # Event engines
        self._presence = FacePresenceMonitor(self.settings.vision)
        self._eyes = EyeMonitor(self.settings.vision)
        self._drowsiness = DrowsinessMonitor(self.settings.vision)
        self._attention = AttentionMonitor(self.settings.vision, self.settings.scoring)
        self._phone = PhoneMonitor(self.settings.vision)

        # Activity
        self._collector = WindowsActivityCollector(
            collect_window_titles=self.settings.activity.collect_window_titles
        )
        self._normalizer = ActivityNormalizer()
        self._task_engine = TaskContextEngine(task=task)
        self._aggregator = ActivityAggregator(
            min_span_seconds=self.settings.activity.min_span_seconds
        )
        self._ipc: IpcServer | None = None
        self._browser: BrowserMonitor | None = None

        # Scoring
        self._scoring = FocusScoringEngine(self.settings.scoring)
        self._productivity = ProductivityTracker(self.settings.scoring)

        # Rates
        self._landmark_limiter = FrameRateLimiter(self.settings.vision.landmark_fps)
        self._phone_limiter = FrameRateLimiter(self.settings.vision.phone_detection_fps)
        self._score_limiter = FrameRateLimiter(
            1.0 / max(0.1, self.settings.scoring.score_sample_interval_seconds)
        )
        self._activity_limiter = FrameRateLimiter(
            1.0 / max(0.1, self.settings.activity.poll_interval_seconds)
        )

        self.latest_snapshot: FocusSnapshot | None = None
        self._events_recorded = 0
        # Checked on a slow schedule: a file stat every tick would be wasteful
        # when the user is not in a hurry to the nearest millisecond.
        self._stop_check = FrameRateLimiter(2.0)

    # -- start-up ----------------------------------------------------------

    def _start_camera(self) -> None:
        try:
            self._camera = CameraSource(self.settings.camera)
            self._camera.start()

            deadline = time.monotonic() + self.settings.camera.open_timeout_seconds
            while self._camera.read() is None and time.monotonic() < deadline:
                time.sleep(0.05)

            self.status.camera = self._camera.read() is not None
            if not self.status.camera:
                logger.warning("No camera frames; vision monitoring is disabled")
        except Exception:  # noqa: BLE001 - a missing camera must not stop the agent
            logger.warning("Camera could not be started", exc_info=True)
            self.status.camera = False

    def _start_vision(self) -> None:
        if not self.status.camera:
            return

        try:
            self._landmarks = FaceLandmarkDetector()
            self.status.landmarks = True
        except Exception:  # noqa: BLE001
            logger.warning("Face landmarks unavailable", exc_info=True)

        try:
            from focusguard_agent.vision.phone_detector import PhoneDetector

            self._phone_detector = PhoneDetector(
                model_path=self.settings.model_dir / "yolov8n.onnx",
                input_size=self.settings.vision.yolo_input_size,
                confidence_threshold=self.settings.vision.phone_confidence_threshold,
            )
            self.status.phone = True
        except (FileNotFoundError, ImportError, RuntimeError) as error:
            logger.info("Phone detection disabled: %s", error)

    def _start_activity(self) -> None:
        self.status.activity = self._collector.available

        try:
            self._ipc = IpcServer(
                host=self.settings.activity.ipc_host, port=self.settings.activity.ipc_port
            )
            self.status.ipc = self._ipc.start()
            if self.status.ipc:
                self._browser = BrowserMonitor(ipc=self._ipc, task_engine=self._task_engine)
        except Exception:  # noqa: BLE001
            logger.warning("IPC server could not start", exc_info=True)
            self.status.ipc = False

    def banner(self) -> str:
        def mark(ok: bool, label: str, detail: str = "") -> str:
            return f"  [{'ok ' if ok else '--'}] {label:<22}{detail}"

        lines = [
            "=" * 62,
            "FocusGuard agent",
            "=" * 62,
            f"  Session : {self.session_name}",
        ]
        if self.task:
            lines.append(f"  Task    : {self.task.name} ({self.task.category})")

        lines += [
            "-" * 62,
            mark(self.status.camera, "camera",
                 self._camera.device.name if self._camera and self._camera.device else ""),
            mark(self.status.landmarks, "face landmarks",
                 f"EAR {self.ear_threshold:.3f} "
                 f"({'calibrated' if self.calibrated else 'default'})"),
            mark(self.status.phone, "phone detection",
                 getattr(self._phone_detector, "active_provider", "")),
            mark(self.status.activity, "application monitor"),
            mark(self.status.ipc, "browser endpoint",
                 f"127.0.0.1:{self.settings.activity.ipc_port}" if self.status.ipc else ""),
            "-" * 62,
            f"  Storing to {self.store.path}",
            "=" * 62,
        ]

        if not self.status.camera:
            lines.append("  Note: no camera - application monitoring only, nothing")
            lines.append("        is scored as absence.")
        if not self.status.ipc:
            lines.append("  Note: browser endpoint unavailable - browsers are tracked")
            lines.append("        as applications without domain detail.")

        return "\n".join(lines)

    # -- the loop ----------------------------------------------------------

    def run(self, duration_seconds: float | None = None) -> int:
        """Run until stopped, or for a fixed duration. Returns an exit code."""
        # Registered BEFORE the subsystems load. Starting MediaPipe and YOLO takes
        # about twelve seconds on this machine, and a user who runs `stop` during
        # that window should not be told that no agent is running.
        clear_stop_flag()
        write_pid_file(self.session_name)

        self._start_camera()
        self._start_vision()
        self._start_activity()

        print(self.banner())

        started_wall = datetime.now(UTC)
        started_monotonic = time.monotonic()
        self._clock = SessionClock(started_wall, started_monotonic)
        self._session_id = self.store.start_session(
            name=self.session_name,
            agent_version=self.settings.agent_version,
            task_name=self.task.name if self.task else None,
            task_category=str(self.task.category) if self.task else None,
            started_at=started_wall,
        )
        logger.info("Session %d started", self._session_id)

        if stop_requested():
            # Asked to stop while the models were still loading. Honour it rather
            # than starting a session the user has already cancelled.
            print("\nStop requested during start-up; shutting down.")
            self._stop.set()

        print("\nMonitoring. Press Ctrl+C to stop, or run:")
        print("  python -m focusguard_agent.tools.stop\n")
        end = None if duration_seconds is None else started_monotonic + duration_seconds

        try:
            while not self._stop.is_set():
                if end is not None and time.monotonic() >= end:
                    break
                if self._stop_check.should_run(time.monotonic(), 1.0) and stop_requested():
                    print("\nStop requested. Shutting down cleanly ...")
                    break
                self._tick()
                time.sleep(0.01)
        except KeyboardInterrupt:
            print("\nStopping ...")
        finally:
            self._shutdown()

        return 0

    def stop(self) -> None:
        self._stop.set()

    def _tick(self) -> None:
        now = time.monotonic()
        frame = self._camera.read() if self._camera else None
        camera_ok = bool(self._camera and self._camera.state is CameraState.OK)
        frame_period = None
        if self._camera:
            measured = self._camera.stats.measured_fps()
            frame_period = 1.0 / measured if measured > 0 else None

        eye_state = None
        pose = None
        gaze = None
        face_present = False

        if camera_ok and frame is not None and self._landmarks is not None:
            if self._landmark_limiter.should_run(now, frame_period):
                result = self._landmarks.detect(frame.image, timestamp=now)
                primary = result.primary
                face_present = primary is not None
                height, width = frame.image.shape[:2]

                if primary is not None:
                    eye_state = analyse_eyes(primary, width, height, self.ear_threshold)
                    pose = estimate_head_pose(
                        primary, width, height,
                        yaw_threshold=self.settings.vision.yaw_threshold_degrees,
                        pitch_up_threshold=self.settings.vision.pitch_up_threshold_degrees,
                        pitch_down_threshold=self.settings.vision.pitch_down_threshold_degrees,
                    )
                    gaze = estimate_gaze(
                        primary, pose, width, height,
                        eyes_closed=bool(eye_state and eye_state.eyes_closed),
                        min_confidence=self.settings.vision.gaze_min_confidence,
                    )

                self._drive_vision_events(now, camera_ok, result.face_count, eye_state, pose, gaze)

        elif self._camera is not None:
            # Camera down. Drive the monitors so they suspend rather than
            # freezing on their last known state.
            self._drive_vision_events(now, False, 0, None, None, None)

        if self._phone_detector is not None and camera_ok and frame is not None:
            if self._phone_limiter.should_run(now, frame_period):
                detection = self._phone_detector.detect(frame.image, timestamp=now)
                self._record(
                    self._phone.update(
                        PhoneObservation(
                            timestamp=now,
                            camera_ok=True,
                            detected=detection.detected,
                            confidence=detection.best_confidence,
                        )
                    )
                )

        if self._activity_limiter.should_run(now, 1.0):
            self._poll_activity(now)

        if self._score_limiter.should_run(now, 1.0):
            self._record_score(now, camera_ok, face_present)

    def _drive_vision_events(self, now, camera_ok, face_count, eye_state, pose, gaze) -> None:
        self._record(
            self._presence.update(
                PresenceObservation(timestamp=now, camera_ok=camera_ok, face_count=face_count)
            )
        )
        face_present = camera_ok and face_count > 0

        self._record(
            self._eyes.update(
                EyeObservation(
                    timestamp=now,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    eyes_closed=bool(eye_state and eye_state.eyes_closed),
                    ear_average=eye_state.ear_average if eye_state else 0.0,
                    confidence=eye_state.confidence if eye_state else 0.0,
                    valid=bool(eye_state and eye_state.valid),
                )
            )
        )
        self._record(
            self._drowsiness.update(
                DrowsinessObservation(
                    timestamp=now,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    eyes_closed=bool(eye_state and eye_state.eyes_closed),
                    eye_confidence=eye_state.confidence if eye_state else 0.0,
                    valid=bool(eye_state and eye_state.valid),
                    head_pitch=pose.pitch if pose and pose.valid else None,
                )
            )
        )

        current = self._aggregator.current
        self._record(
            self._attention.update(
                AttentionObservation(
                    timestamp=now,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    head_direction=pose.direction if pose else HeadPose.UNKNOWN,
                    head_pitch=pose.pitch if pose else 0.0,
                    head_confidence=pose.confidence if pose else 0.0,
                    gaze_direction=gaze.direction if gaze else GazeDirection.UNKNOWN,
                    gaze_confidence=gaze.confidence if gaze else 0.0,
                    gaze_valid=bool(gaze and gaze.valid and not gaze.low_confidence),
                    activity_classification=current.classification if current else None,
                )
            )
        )

    def _poll_activity(self, now: float) -> None:
        if not self.status.activity:
            return

        if self._browser is not None:
            self._browser.poll()

        idle = self._collector.is_idle(self.settings.activity.idle_threshold_seconds)
        window = self._collector.foreground()

        activity = None
        process_name = ""
        if window is not None and not window.is_transient:
            process_name = window.process_name
            application = self._normalizer.normalise(window.process_name)
            activity = (
                self._browser.classify(application, now=now)
                if self._browser is not None
                else self._task_engine.classify(application)
            )

        span = self._aggregator.update(
            now,
            activity,
            process_name=process_name,
            window_title=window.window_title if window else None,
            idle=idle,
        )
        if span is not None and self._session_id is not None and self._clock is not None:
            self.store.record_activity_event(
                session_id=self._session_id,
                application_name=span.application_name,
                category=str(span.category),
                classification=str(span.classification),
                rule_source=str(span.rule_source),
                start_time=self._clock.to_wall(span.start_time),
                end_time=self._clock.to_wall(span.end_time),
                duration_seconds=span.duration_seconds,
                process_name=span.process_name or None,
                window_title=span.window_title,
                domain=span.domain,
            )

        self._productivity.update(now, activity.classification if activity else None)

    def _record_score(self, now: float, camera_ok: bool, face_present: bool) -> None:
        active: dict[VisionEventType, float] = {}
        if self._presence.face_absent:
            active[VisionEventType.FACE_ABSENT] = 1.0
        if self._presence.multiple_faces:
            active[VisionEventType.MULTIPLE_FACES] = 1.0
        if self._eyes.eyes_closed:
            active[VisionEventType.EYES_CLOSED] = 0.9
        if self._drowsiness.is_drowsy:
            active[VisionEventType.DROWSINESS] = 0.85
        if self._attention.gaze_away:
            active[VisionEventType.GAZE_AWAY] = 0.7
        if self._attention.head_down:
            active[VisionEventType.HEAD_DOWN] = 0.8
        if self._phone.phone_visible:
            active[VisionEventType.PHONE_VISIBLE] = 0.8

        # With no camera at all, visual attention is not measurable and must not
        # be scored - but activity monitoring continues, so the session is still
        # worth running.
        suspended = bool(self._camera) and (not camera_ok or self._presence.suspended)

        snapshot = self._scoring.update(
            now,
            active_events=active,
            productivity_score=self._productivity.score(),
            suspended=suspended,
        )
        self.latest_snapshot = snapshot

        if self._session_id is not None and self._clock is not None and not suspended:
            self.store.record_score(
                session_id=self._session_id,
                recorded_at=self._clock.to_wall(now),
                visual_attention_score=snapshot.visual_attention_score,
                digital_productivity_score=snapshot.digital_productivity_score,
                combined_focus_score=snapshot.combined_focus_score,
                active_events=[str(event) for event in snapshot.active_events],
            )

    def _record(self, events: list[BehaviorEvent]) -> None:
        """Persist completed behavioural events."""
        if not events or self._session_id is None or self._clock is None:
            return

        for event in events:
            self.store.record_vision_event(
                session_id=self._session_id,
                event_type=str(event.event_type),
                start_time=self._clock.to_wall(event.start_time),
                end_time=self._clock.to_wall(event.end_time),
                duration_seconds=event.duration_seconds,
                confidence=event.confidence,
            )
            self._events_recorded += 1
            logger.info(
                "%s for %.1fs (confidence %.2f)",
                event.event_type,
                event.duration_seconds,
                event.confidence,
            )

    # -- shutdown ----------------------------------------------------------

    def _shutdown(self) -> None:
        now = time.monotonic()

        clear_stop_flag()
        clear_pid_file()

        # Close everything still open, or a session ended mid-distraction would
        # silently discard it.
        for monitor in (self._presence, self._eyes, self._drowsiness, self._attention, self._phone):
            self._record(monitor.flush(now))

        span = self._aggregator.flush(now)
        if span is not None and self._session_id is not None and self._clock is not None:
            self.store.record_activity_event(
                session_id=self._session_id,
                application_name=span.application_name,
                category=str(span.category),
                classification=str(span.classification),
                rule_source=str(span.rule_source),
                start_time=self._clock.to_wall(span.start_time),
                end_time=self._clock.to_wall(span.end_time),
                duration_seconds=span.duration_seconds,
                process_name=span.process_name or None,
                domain=span.domain,
            )

        summary = self._scoring.finish(now)

        if self._camera is not None:
            self._camera.stop()
        if self._landmarks is not None:
            self._landmarks.close()
        if self._phone_detector is not None:
            self._phone_detector.close()
        if self._ipc is not None:
            self._ipc.stop()

        if self._session_id is not None:
            self.store.end_session(self._session_id)

        self._print_summary(summary)
        self.store.close()

    def _print_summary(self, summary) -> None:
        def mmss(seconds: float) -> str:
            return f"{int(seconds) // 60}m {int(seconds) % 60:02d}s"

        print("\n" + "=" * 62)
        print("SESSION SUMMARY")
        print("=" * 62)
        print(f"  Duration          : {mmss(summary.duration_seconds)}")

        if summary.monitored_seconds > 0:
            print(f"  Focused           : {mmss(summary.focused_seconds)} "
                  f"({summary.focused_ratio * 100:.0f}%)")
            print(f"  Distracted        : {mmss(summary.distracted_seconds)}")
            print(f"  Average focus     : {summary.average_focus_score:.1f}")
            print(f"  Longest focus run : {mmss(summary.longest_focus_block_seconds)}")
        if summary.suspended_seconds > 1:
            print(f"  Not scored        : {mmss(summary.suspended_seconds)} "
                  "(camera unavailable)")

        if summary.event_seconds:
            print("\n  Attention events:")
            for event_type, seconds in sorted(
                summary.event_seconds.items(), key=lambda item: -item[1]
            ):
                count = summary.event_counts.get(event_type, 0)
                print(f"    {event_type:<18} {mmss(seconds):>9}  x{count}")

        totals = self._aggregator.totals
        if totals.by_application:
            print("\n  Most used:")
            for name, seconds in totals.most_used():
                print(f"    {name:<26} {mmss(seconds):>9}")

        productivity = self._productivity.score(rolling=False)
        if productivity is not None:
            print(f"\n  Digital productivity: {productivity:.0f}%")

        print(f"\n  Blinks            : {self._eyes.blink_count}")
        print(f"  Phone appearances : {self._phone.event_count}")
        print(f"  Events saved      : {self._events_recorded}")
        print(f"  Session id        : {self._session_id}")
        print("=" * 62)
        print("\nSee this again with:")
        print(f"  python -m focusguard_agent.tools.report --session {self._session_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard monitoring agent")
    parser.add_argument("--name", default="Focus session", help="what you are working on")
    parser.add_argument("--task", help="task name, for task-aware productivity")
    parser.add_argument("--task-category", default="OTHER", help="e.g. BACKEND, STUDY, DESIGN")
    parser.add_argument(
        "--productive", nargs="*", default=[], help="apps/domains counting as work"
    )
    parser.add_argument("--distracting", nargs="*", default=[], help="apps/domains that do not")
    parser.add_argument("--seconds", type=float, help="stop automatically after this long")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(level=settings.log_level, log_dir=settings.log_dir)

    task = None
    if args.task:
        from focusguard_shared.constants import TaskCategory

        try:
            category = TaskCategory(args.task_category.upper())
        except ValueError:
            print(f"Unknown task category {args.task_category!r}; using OTHER", file=sys.stderr)
            category = TaskCategory.OTHER

        task = TaskContext(
            name=args.task,
            category=category,
            productive=set(args.productive),
            distracting=set(args.distracting),
        )

    agent = FocusGuardAgent(settings=settings, session_name=args.name, task=task)

    def handle_signal(*_: object) -> None:
        agent.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    return agent.run(duration_seconds=args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
