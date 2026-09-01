"""Gaze and attention-event tests (spec sections 14, 15, 60, 61).

The behaviour that matters most: an uncertain gaze reading must never become a
recorded distraction. A missed distraction costs a little accuracy; a fabricated
one costs the user points they did not lose.
"""

from __future__ import annotations

import numpy as np
import pytest

from focusguard_agent.config import ScoringSettings, VisionSettings
from focusguard_agent.events.attention import AttentionMonitor, AttentionObservation
from focusguard_agent.vision.eye_analysis import (
    LEFT_EYE_EAR_POINTS,
    RIGHT_EYE_EAR_POINTS,
)
from focusguard_agent.vision.gaze import (
    LEFT_IRIS_CENTER,
    RIGHT_IRIS_CENTER,
    estimate_gaze,
)
from focusguard_agent.vision.head_pose import HeadPoseResult
from focusguard_agent.vision.landmarks import FaceResult
from focusguard_shared.constants import (
    Classification,
    GazeDirection,
    HeadPose,
    MonitorProfile,
    VisionEventType,
)

# --- gaze -------------------------------------------------------------------


def _gaze_face(iris_offset: float = 0.0, eye_width: float = 0.09) -> FaceResult:
    """Face with the iris displaced by a fraction of the eye's half-width."""
    landmarks = np.full((478, 3), 0.5, dtype=np.float32)
    half = eye_width / 2.0

    for eye_indices, iris_index, centre_x in (
        (LEFT_EYE_EAR_POINTS, LEFT_IRIS_CENTER, 0.4),
        (RIGHT_EYE_EAR_POINTS, RIGHT_IRIS_CENTER, 0.6),
    ):
        landmarks[eye_indices[0]] = (centre_x - half, 0.5, 0.0)  # outer corner
        landmarks[eye_indices[3]] = (centre_x + half, 0.5, 0.0)  # inner corner
        landmarks[iris_index] = (centre_x + iris_offset * half, 0.5, 0.0)

    return FaceResult(landmarks=landmarks)


def _pose(
    yaw: float = 0.0,
    pitch: float = 0.0,
    direction: HeadPose = HeadPose.CENTER,
    confidence: float = 0.9,
    valid: bool = True,
) -> HeadPoseResult:
    return HeadPoseResult(
        yaw=yaw, pitch=pitch, roll=0.0, direction=direction,
        confidence=confidence, valid=valid,
    )


def test_a_centred_head_and_iris_reads_as_looking_at_the_screen() -> None:
    result = estimate_gaze(_gaze_face(), _pose(), 640, 360)

    assert result.direction is GazeDirection.SCREEN
    assert result.looking_at_screen is True
    assert result.valid is True


def test_a_turned_head_reads_as_looking_away() -> None:
    result = estimate_gaze(_gaze_face(), _pose(yaw=40, direction=HeadPose.LOOKING_LEFT), 640, 360)

    assert result.direction is GazeDirection.LEFT
    assert result.looking_at_screen is False


def test_closed_eyes_make_gaze_unmeasurable() -> None:
    """A closed eyelid hides the iris, so its centre is a guess."""
    result = estimate_gaze(_gaze_face(), _pose(), 640, 360, eyes_closed=True)

    assert result.direction is GazeDirection.UNKNOWN
    assert result.valid is False
    assert result.confidence == 0.0


def test_a_poor_head_reading_yields_a_poor_gaze_reading() -> None:
    good = estimate_gaze(_gaze_face(), _pose(confidence=0.9), 640, 360)
    poor = estimate_gaze(_gaze_face(), _pose(confidence=0.45), 640, 360)

    assert poor.confidence < good.confidence


def test_an_invalid_head_pose_gives_unknown_gaze() -> None:
    result = estimate_gaze(_gaze_face(), _pose(valid=False), 640, 360)
    assert result.direction is GazeDirection.UNKNOWN


def test_a_tiny_face_falls_back_to_head_pose_with_low_confidence() -> None:
    """At distance the iris spans a handful of pixels and cannot be located."""
    result = estimate_gaze(_gaze_face(eye_width=0.005), _pose(), 640, 360)

    assert result.iris_horizontal == 0.0
    assert result.confidence < 0.85


def test_low_confidence_is_reported_as_unknown_never_as_away() -> None:
    """The central safety rule for gaze (spec section 61)."""
    result = estimate_gaze(
        _gaze_face(eye_width=0.005), _pose(confidence=0.35), 640, 360, min_confidence=0.55
    )

    assert result.direction is GazeDirection.UNKNOWN
    assert result.low_confidence is True


def test_head_down_produces_downward_gaze() -> None:
    result = estimate_gaze(
        _gaze_face(), _pose(pitch=-30, direction=HeadPose.LOOKING_DOWN), 640, 360
    )
    assert result.direction is GazeDirection.DOWN


# --- attention events -------------------------------------------------------


def _monitor(
    gaze_grace: float = 4.0,
    head_grace: float = 3.0,
    profile: MonitorProfile = MonitorProfile.SINGLE,
) -> AttentionMonitor:
    return AttentionMonitor(
        VisionSettings(
            gaze_away_grace_seconds=gaze_grace,
            head_down_grace_seconds=head_grace,
            event_exit_hysteresis_seconds=0.0,
        ),
        ScoringSettings(monitor_profile=profile, suppress_gaze_penalty_when_productive=True),
    )


def _feed(
    monitor: AttentionMonitor,
    samples: list[tuple[float, GazeDirection]],
    *,
    camera_ok: bool = True,
    face_present: bool = True,
    gaze_confidence: float = 0.8,
    gaze_valid: bool = True,
    head_direction: HeadPose = HeadPose.CENTER,
    classification: Classification | None = None,
) -> list:
    events = []
    for timestamp, gaze in samples:
        events.extend(
            monitor.update(
                AttentionObservation(
                    timestamp=timestamp,
                    camera_ok=camera_ok,
                    face_present=face_present,
                    head_direction=head_direction,
                    head_confidence=0.9,
                    gaze_direction=gaze,
                    gaze_confidence=gaze_confidence,
                    gaze_valid=gaze_valid,
                    activity_classification=classification,
                )
            )
        )
    return events


def test_a_brief_glance_away_costs_nothing() -> None:
    """Spec section 14: half a second of looking away must not hurt the score."""
    monitor = _monitor(gaze_grace=4.0)

    events = _feed(
        monitor,
        [
            (0.0, GazeDirection.SCREEN),
            (1.0, GazeDirection.LEFT),
            (1.5, GazeDirection.SCREEN),
            (3.0, GazeDirection.SCREEN),
        ],
    )

    assert events == []
    assert monitor.gaze_away is False


def test_a_sustained_look_away_becomes_an_event() -> None:
    monitor = _monitor(gaze_grace=4.0)

    _feed(monitor, [(0.0, GazeDirection.SCREEN)])
    _feed(monitor, [(1.0, GazeDirection.LEFT), (6.0, GazeDirection.LEFT)])
    assert monitor.gaze_away is True

    events = _feed(monitor, [(8.0, GazeDirection.SCREEN), (8.1, GazeDirection.SCREEN)])

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.GAZE_AWAY
    assert events[0].duration_seconds == pytest.approx(7.0)


def test_an_unknown_gaze_is_never_treated_as_looking_away() -> None:
    """The asymmetry that protects the user.

    A missed distraction costs a little accuracy. A fabricated one costs points
    the user did not lose and tells them something false about themselves.
    """
    monitor = _monitor(gaze_grace=4.0)

    events = _feed(
        monitor,
        [(0.0, GazeDirection.UNKNOWN), (10.0, GazeDirection.UNKNOWN)],
        gaze_confidence=0.2,
    )

    assert events == []
    assert monitor.gaze_away is False


def test_an_invalid_gaze_reading_is_never_treated_as_looking_away() -> None:
    monitor = _monitor(gaze_grace=4.0)
    events = _feed(
        monitor,
        [(0.0, GazeDirection.LEFT), (10.0, GazeDirection.LEFT)],
        gaze_valid=False,
    )
    assert events == []


def test_head_down_is_its_own_event() -> None:
    monitor = _monitor(head_grace=3.0)

    _feed(
        monitor,
        [(0.0, GazeDirection.SCREEN), (4.0, GazeDirection.SCREEN)],
        head_direction=HeadPose.LOOKING_DOWN,
    )
    assert monitor.head_down is True

    events = _feed(
        monitor,
        [(6.0, GazeDirection.SCREEN), (6.1, GazeDirection.SCREEN)],
        head_direction=HeadPose.CENTER,
    )

    assert len(events) == 1
    assert events[0].event_type is VisionEventType.HEAD_DOWN


def test_a_camera_failure_produces_no_attention_events() -> None:
    monitor = _monitor()
    events = _feed(
        monitor, [(0.0, GazeDirection.LEFT), (20.0, GazeDirection.LEFT)], camera_ok=False
    )

    assert events == []
    assert monitor.suspended is True


def test_a_missing_face_suspends_attention_monitoring() -> None:
    monitor = _monitor()
    events = _feed(
        monitor, [(0.0, GazeDirection.LEFT), (20.0, GazeDirection.LEFT)], face_present=False
    )

    assert events == []
    assert monitor.suspended is True


# --- multi-monitor (spec section 60) ---------------------------------------


def test_multi_monitor_extends_the_gaze_grace_period() -> None:
    """With a second screen, looking away is frequently still work."""
    single = _monitor(gaze_grace=4.0, profile=MonitorProfile.SINGLE)
    dual = _monitor(gaze_grace=4.0, profile=MonitorProfile.DUAL)

    for monitor in (single, dual):
        _feed(monitor, [(0.0, GazeDirection.SCREEN)])
        _feed(monitor, [(1.0, GazeDirection.LEFT), (6.0, GazeDirection.LEFT)])

    assert single.gaze_away is True
    assert dual.gaze_away is False, "the dual-monitor user gets a longer allowance"


def test_a_productive_app_suppresses_gaze_away_on_multi_monitor() -> None:
    """Reading docs on a second screen with the IDE focused is work."""
    monitor = _monitor(gaze_grace=4.0, profile=MonitorProfile.DUAL)

    events = _feed(
        monitor,
        [(0.0, GazeDirection.LEFT), (30.0, GazeDirection.LEFT)],
        classification=Classification.PRODUCTIVE,
    )

    assert events == []
    assert monitor.gaze_away is False
    assert monitor.suppressed_by_activity is True


def test_a_distracting_app_does_not_suppress_gaze_away() -> None:
    monitor = _monitor(gaze_grace=4.0, profile=MonitorProfile.DUAL)

    _feed(monitor, [(0.0, GazeDirection.SCREEN)], classification=Classification.DISTRACTING)
    _feed(
        monitor,
        [(1.0, GazeDirection.LEFT), (30.0, GazeDirection.LEFT)],
        classification=Classification.DISTRACTING,
    )

    assert monitor.gaze_away is True
    assert monitor.suppressed_by_activity is False


def test_single_monitor_ignores_the_activity_classification() -> None:
    """The suppression exists for second screens, not as a general exemption."""
    monitor = _monitor(gaze_grace=4.0, profile=MonitorProfile.SINGLE)

    _feed(monitor, [(0.0, GazeDirection.SCREEN)], classification=Classification.PRODUCTIVE)
    _feed(
        monitor,
        [(1.0, GazeDirection.LEFT), (10.0, GazeDirection.LEFT)],
        classification=Classification.PRODUCTIVE,
    )

    assert monitor.gaze_away is True
