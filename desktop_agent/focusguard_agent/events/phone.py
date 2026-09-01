"""Phone-presence events (spec section 16).

THE REQUIREMENT THIS MODULE EXISTS TO MEET
------------------------------------------
*"Avoid counting every frame as another event. A continuous phone appearance
should create ONE event with start_time, end_time and duration."*

Detection at 3 Hz over a 30-second phone appearance produces about 90 positive
frames. Naively that is 90 events. Worse, object detection flickers - a hand
briefly covers the phone, it tilts and reflects the screen, it passes behind the
keyboard - so even grouping consecutive positives would split one appearance into
several.

Two mechanisms fix this, both already in the shared state machine:

* a **grace period** (1.5 s) before the appearance is believed at all, so a phone
  glimpsed while reaching past the desk is not an event;
* an **exit hysteresis** set to `phone_merge_gap_seconds` (2 s), so a detection
  gap shorter than that keeps the same event open.

The result is one event per continuous appearance, with an honest start, end and
duration - which is what the timeline and the reports need.

CONFIDENCE
----------
The event carries the mean detection confidence across its life, not the peak. A
detector that was 90% sure for one frame and 55% for thirty should not produce a
90%-confidence event (spec section 61).
"""

from __future__ import annotations

from dataclasses import dataclass

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import VisionEventType
from focusguard_shared.utilities import get_logger

logger = get_logger("events.phone")


@dataclass
class PhoneObservation:
    """One detection pass worth of phone information."""

    timestamp: float
    camera_ok: bool
    detected: bool
    confidence: float = 0.0

    @property
    def usable(self) -> bool:
        """Phone detection needs only a working camera, not a visible face.

        A user holding a phone below the desk may have their face out of frame
        while the phone is still visible, so this deliberately does not require
        `face_present` the way the eye and gaze monitors do.
        """
        return self.camera_ok


class PhoneMonitor:
    """Emits one PHONE_VISIBLE event per continuous appearance."""

    def __init__(self, settings: VisionSettings) -> None:
        self._settings = settings

        self._machine = EventStateMachine(
            event_type=VisionEventType.PHONE_VISIBLE,
            grace_seconds=settings.phone_grace_seconds,
            # The merge gap IS the exit hysteresis: a detection dropout shorter
            # than this keeps one appearance as a single event.
            exit_hysteresis_seconds=settings.phone_merge_gap_seconds,
        )

        self._event_count = 0
        self._total_seconds = 0.0
        self._suspended = False

    # -- state -------------------------------------------------------------

    @property
    def phone_visible(self) -> bool:
        return self._machine.is_active

    @property
    def event_count(self) -> int:
        """Completed appearances this session - the "phone distractions" figure."""
        return self._event_count

    @property
    def total_seconds(self) -> float:
        return self._total_seconds

    @property
    def suspended(self) -> bool:
        return self._suspended

    def current_duration(self, now: float) -> float:
        return self._machine.current_duration(now)

    # -- driving -----------------------------------------------------------

    def update(self, observation: PhoneObservation) -> list[BehaviorEvent]:
        if not observation.usable:
            return self._suspend(observation.timestamp)

        self._suspended = False

        event = self._machine.update(
            condition=observation.detected,
            now=observation.timestamp,
            confidence=observation.confidence if observation.detected else 0.0,
        )
        return self._record(event)

    def _record(self, event: BehaviorEvent | None) -> list[BehaviorEvent]:
        if event is None:
            return []

        self._event_count += 1
        self._total_seconds += event.duration_seconds
        logger.debug(
            "Phone appearance %d: %.1fs at %.2f confidence",
            self._event_count,
            event.duration_seconds,
            event.confidence,
        )
        return [event]

    def _suspend(self, timestamp: float) -> list[BehaviorEvent]:
        """Camera down - close any open appearance and attribute nothing more."""
        if self._suspended:
            return []

        self._suspended = True
        return self._record(self._machine.flush(timestamp))

    def flush(self, timestamp: float) -> list[BehaviorEvent]:
        return self._record(self._machine.flush(timestamp))
