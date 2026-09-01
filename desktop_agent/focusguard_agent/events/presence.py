"""Face presence events (spec section 11).

Produces two events:

* ``FACE_ABSENT``    - nobody in front of a working camera.
* ``MULTIPLE_FACES`` - more than one person in frame.

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
**A broken camera is not an absent user.**

When the camera stops delivering frames - the phone locks, Wi-Fi drops, Camo
Studio is closed - the face detector receives nothing and finds no face. Feeding
that into FACE_ABSENT would record a distraction, and cost the user focus points,
for a technical fault they did not cause. On this machine, where the camera is a
phone on Wi-Fi, that is not a hypothetical: it happened during Phase 2 testing.

So while the camera is unavailable this monitor **suspends**. Any event in progress
is closed at the last known-good frame and the machines reset to IDLE. Time spent
with no camera is attributed to nobody: not to focus, not to distraction. That is
the honest treatment, and it is the one the specification's "no false claims"
requirement (section 61) demands.
"""

from __future__ import annotations

from dataclasses import dataclass

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import VisionEventType
from focusguard_shared.utilities import get_logger

logger = get_logger("events.presence")


@dataclass
class PresenceObservation:
    """One frame's worth of presence information."""

    timestamp: float
    camera_ok: bool
    face_count: int

    @property
    def face_present(self) -> bool:
        """True only when the camera works AND somebody was found."""
        return self.camera_ok and self.face_count > 0


class FacePresenceMonitor:
    """Tracks face presence and emits absence / multiple-face events."""

    def __init__(self, settings: VisionSettings) -> None:
        self._settings = settings

        self._absent = EventStateMachine(
            event_type=VisionEventType.FACE_ABSENT,
            grace_seconds=settings.face_absent_grace_seconds,
            exit_hysteresis_seconds=settings.event_exit_hysteresis_seconds,
        )
        self._multiple = EventStateMachine(
            event_type=VisionEventType.MULTIPLE_FACES,
            grace_seconds=settings.multiple_faces_grace_seconds,
            exit_hysteresis_seconds=settings.event_exit_hysteresis_seconds,
        )

        self._camera_available = True
        self._last_good_timestamp: float | None = None

    # -- state -------------------------------------------------------------

    @property
    def face_absent(self) -> bool:
        return self._absent.is_active

    @property
    def multiple_faces(self) -> bool:
        return self._multiple.is_active

    @property
    def suspended(self) -> bool:
        """True while the camera is unavailable and nothing is being attributed."""
        return not self._camera_available

    # -- driving -----------------------------------------------------------

    def update(self, observation: PresenceObservation) -> list[BehaviorEvent]:
        """Advance both machines by one observation, returning completed events."""
        if not observation.camera_ok:
            return self._suspend(observation.timestamp)

        events: list[BehaviorEvent] = []

        if not self._camera_available:
            logger.info("Camera recovered; presence monitoring resumed")
            self._camera_available = True

        self._last_good_timestamp = observation.timestamp

        absent_event = self._absent.update(
            condition=observation.face_count == 0,
            now=observation.timestamp,
        )
        if absent_event is not None:
            events.append(absent_event)

        multiple_event = self._multiple.update(
            condition=observation.face_count > 1,
            now=observation.timestamp,
        )
        if multiple_event is not None:
            events.append(multiple_event)

        return events

    def _suspend(self, timestamp: float) -> list[BehaviorEvent]:
        """Close and discard in-progress events because the camera has gone.

        Events already confirmed before the outage are still emitted - they
        happened, and the evidence for them is sound. Anything mid-flight is
        dropped, because we cannot know whether the condition continued.
        """
        if not self._camera_available:
            return []

        self._camera_available = False
        cutoff = self._last_good_timestamp if self._last_good_timestamp is not None else timestamp
        logger.info("Camera unavailable; presence monitoring suspended (not scored as absence)")

        events: list[BehaviorEvent] = []
        for machine in (self._absent, self._multiple):
            event = machine.flush(cutoff)
            if event is not None:
                events.append(event)

        return events

    def flush(self, timestamp: float) -> list[BehaviorEvent]:
        """Close any open events, e.g. when the session ends."""
        events: list[BehaviorEvent] = []
        for machine in (self._absent, self._multiple):
            event = machine.flush(timestamp)
            if event is not None:
                events.append(event)
        return events
