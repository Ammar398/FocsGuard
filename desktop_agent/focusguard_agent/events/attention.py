"""Gaze-away and head-down events (spec sections 14, 15, 60, 61).

WHY THE GRACE PERIODS ARE LONG HERE
-----------------------------------
Looking away is the easiest signal to get wrong, because normal work is full of
it: glancing at a keyboard, reaching for a drink, thinking. The specification is
blunt - half a second of looking away must not hurt the score - so `GAZE_AWAY`
needs four seconds of sustained evidence before it becomes an event, and
`HEAD_DOWN` three.

WHY A LOW-CONFIDENCE GAZE READING COUNTS AS NOT-AWAY
----------------------------------------------------
When gaze confidence falls below the threshold the direction is `UNKNOWN`, and
this monitor treats that as **no evidence of looking away** rather than as
evidence of it.

That asymmetry is deliberate. A missed distraction costs a little accuracy; a
fabricated one costs the user points they did not lose, and tells them something
false about their own behaviour. Given a 15-pixel iris on a phone camera, the
uncertain case is common, and it must fail towards the user.

MULTI-MONITOR (SECTION 60)
--------------------------
On a dual or multi-monitor setup, looking away from the webcam is frequently
still work. Two adjustments apply:

* the gaze-away grace period is extended, so a longer glance is tolerated;
* gaze-away is **suppressed entirely** while the foreground application is
  classified productive - reading documentation on a second screen with the IDE
  focused is work, not distraction.

The scoring engine additionally scales down the penalty weight (Phase 8). This
module handles whether the *event* happens at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from focusguard_agent.config import ScoringSettings, VisionSettings
from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import (
    Classification,
    GazeDirection,
    HeadPose,
    MonitorProfile,
    VisionEventType,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("events.attention")

#: How much longer the gaze-away grace period runs on a multi-screen setup.
_MULTI_MONITOR_GRACE_MULTIPLIER = 1.75


@dataclass
class AttentionObservation:
    """One frame's worth of orientation information."""

    timestamp: float
    camera_ok: bool
    face_present: bool

    head_direction: HeadPose = HeadPose.UNKNOWN
    head_pitch: float = 0.0
    head_confidence: float = 0.0

    gaze_direction: GazeDirection = GazeDirection.UNKNOWN
    gaze_confidence: float = 0.0
    gaze_valid: bool = False

    #: Productivity verdict for the active application, when known. Used only to
    #: suppress gaze-away on multi-monitor setups (spec section 60).
    activity_classification: Classification | None = None

    @property
    def usable(self) -> bool:
        return self.camera_ok and self.face_present


class AttentionMonitor:
    """Emits GAZE_AWAY and HEAD_DOWN events."""

    def __init__(
        self,
        vision: VisionSettings,
        scoring: ScoringSettings | None = None,
    ) -> None:
        self._vision = vision
        self._scoring = scoring or ScoringSettings()

        multi_monitor = self._scoring.monitor_profile is not MonitorProfile.SINGLE
        gaze_grace = vision.gaze_away_grace_seconds
        if multi_monitor:
            gaze_grace *= _MULTI_MONITOR_GRACE_MULTIPLIER
            logger.info(
                "Multi-monitor profile: gaze-away grace extended to %.1fs", gaze_grace
            )

        self._multi_monitor = multi_monitor
        self._gaze_away = EventStateMachine(
            event_type=VisionEventType.GAZE_AWAY,
            grace_seconds=gaze_grace,
            exit_hysteresis_seconds=vision.event_exit_hysteresis_seconds,
        )
        self._head_down = EventStateMachine(
            event_type=VisionEventType.HEAD_DOWN,
            grace_seconds=vision.head_down_grace_seconds,
            exit_hysteresis_seconds=vision.event_exit_hysteresis_seconds,
        )

        self._suspended = False
        self._suppressed_by_activity = False

    # -- state -------------------------------------------------------------

    @property
    def gaze_away(self) -> bool:
        return self._gaze_away.is_active

    @property
    def head_down(self) -> bool:
        return self._head_down.is_active

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def suppressed_by_activity(self) -> bool:
        """True when a productive app is holding gaze-away off (section 60)."""
        return self._suppressed_by_activity

    # -- driving -----------------------------------------------------------

    def update(self, observation: AttentionObservation) -> list[BehaviorEvent]:
        if not observation.usable:
            return self._suspend(observation.timestamp)

        self._suspended = False
        events: list[BehaviorEvent] = []
        now = observation.timestamp

        looking_away = self._is_looking_away(observation)

        event = self._gaze_away.update(
            condition=looking_away,
            now=now,
            confidence=observation.gaze_confidence,
        )
        if event is not None:
            events.append(event)

        head_event = self._head_down.update(
            condition=observation.head_direction is HeadPose.LOOKING_DOWN,
            now=now,
            confidence=observation.head_confidence,
        )
        if head_event is not None:
            events.append(head_event)

        return events

    def _is_looking_away(self, observation: AttentionObservation) -> bool:
        """Decide whether this frame counts as looking away from the screen."""
        self._suppressed_by_activity = False

        # UNKNOWN means the measurement failed, not that the user looked away.
        # Treating it as away would fabricate distractions from noise.
        if not observation.gaze_valid or observation.gaze_direction is GazeDirection.UNKNOWN:
            return False

        if observation.gaze_direction is GazeDirection.SCREEN:
            return False

        # Section 60: a second screen makes looking away normal work, so a
        # productive foreground application vetoes the event entirely.
        if (
            self._multi_monitor
            and self._scoring.suppress_gaze_penalty_when_productive
            and observation.activity_classification is Classification.PRODUCTIVE
        ):
            self._suppressed_by_activity = True
            return False

        return True

    def _suspend(self, timestamp: float) -> list[BehaviorEvent]:
        """No face or no camera - nothing can be concluded about where they look."""
        if self._suspended:
            return []

        self._suspended = True
        self._suppressed_by_activity = False

        events: list[BehaviorEvent] = []
        for machine in (self._gaze_away, self._head_down):
            event = machine.flush(timestamp)
            if event is not None:
                events.append(event)
        return events

    def flush(self, timestamp: float) -> list[BehaviorEvent]:
        events: list[BehaviorEvent] = []
        for machine in (self._gaze_away, self._head_down):
            event = machine.flush(timestamp)
            if event is not None:
                events.append(event)
        return events
