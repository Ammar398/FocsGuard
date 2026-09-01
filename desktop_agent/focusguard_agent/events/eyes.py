"""Blink and eye-closure events (spec sections 12, 13).

THE DISTINCTION THIS MODULE EXISTS TO MAKE
------------------------------------------
Closed eyes are not evidence of anything on their own - everybody blinks roughly
15 to 20 times a minute. What separates a blink from inattention is **how long**
the eyes stay shut:

| Duration        | Meaning        | Cost to the user |
|-----------------|----------------|------------------|
| under ~0.4 s    | BLINK          | none - counted only |
| 1 s or more     | EYES_CLOSED    | penalised |
| 2.5 s or more   | DROWSINESS     | penalised heavily (Phase 5) |

Getting this wrong in either direction is bad. Treat every blink as inattention
and a user loses points for being alive; ignore duration entirely and someone
asleep at their desk scores as focused.

The blink *count* is not wasted either. Blink rate is one of the better-supported
drowsiness indicators, and Phase 5 uses the rate accumulated here rather than
measuring it again.

WHAT IS NOT CLAIMED
-------------------
A closed eye is a closed eye. This module does not infer tiredness, boredom or
attention from it - that interpretation belongs to the scoring engine, and even
there it stays a behavioural observation (spec sections 8, 61).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import VisionEventType
from focusguard_shared.utilities import get_logger

logger = get_logger("events.eyes")


@dataclass
class EyeObservation:
    """One frame's worth of eye information."""

    timestamp: float
    camera_ok: bool
    face_present: bool

    eyes_closed: bool
    ear_average: float
    confidence: float

    #: False when the eye was unmeasurable this frame - too far, too angled.
    valid: bool = True

    @property
    def usable(self) -> bool:
        """Whether any eye conclusion may be drawn from this frame at all."""
        return self.camera_ok and self.face_present and self.valid


@dataclass(frozen=True)
class Blink:
    """A completed short eye closure."""

    start_time: float
    end_time: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_time - self.start_time)


class EyeMonitor:
    """Counts blinks and emits EYES_CLOSED events."""

    #: Blink timestamps kept for the rolling-rate calculation.
    _RATE_WINDOW_SECONDS = 60.0

    def __init__(self, settings: VisionSettings) -> None:
        self._settings = settings

        self._closed = EventStateMachine(
            event_type=VisionEventType.EYES_CLOSED,
            grace_seconds=settings.eyes_closed_grace_seconds,
            exit_hysteresis_seconds=settings.event_exit_hysteresis_seconds,
        )

        self._closure_started: float | None = None
        self._blink_times: deque[float] = deque()
        self._blink_count = 0
        self._last_blink_duration = 0.0
        self._suspended = False

    # -- state -------------------------------------------------------------

    @property
    def eyes_closed(self) -> bool:
        """True once a closure has outlasted its grace period."""
        return self._closed.is_active

    @property
    def blink_count(self) -> int:
        return self._blink_count

    @property
    def last_blink_duration(self) -> float:
        return self._last_blink_duration

    @property
    def suspended(self) -> bool:
        return self._suspended

    def blink_rate_per_minute(self, now: float) -> float:
        """Blinks per minute over the last minute.

        Phase 5 reads this rather than recomputing it: both a markedly reduced
        and a markedly elevated blink rate are recognised drowsiness indicators.
        """
        self._trim_blinks(now)
        if not self._blink_times:
            return 0.0

        elapsed = now - self._blink_times[0]
        if elapsed < 1.0:
            # Too short a window to extrapolate; one blink would read as 60/min.
            return 0.0
        return len(self._blink_times) * 60.0 / elapsed

    # -- driving -----------------------------------------------------------

    def update(self, observation: EyeObservation) -> list[BehaviorEvent]:
        """Advance by one observation, returning any completed events."""
        if not observation.usable:
            return self._suspend(observation.timestamp)

        if self._suspended:
            self._suspended = False

        events: list[BehaviorEvent] = []
        now = observation.timestamp

        self._track_blink(observation)

        event = self._closed.update(
            condition=observation.eyes_closed,
            now=now,
            confidence=observation.confidence,
        )
        if event is not None:
            events.append(event)

        return events

    def _track_blink(self, observation: EyeObservation) -> None:
        """Count short closures as blinks; leave long ones to the event machine."""
        now = observation.timestamp

        if observation.eyes_closed:
            if self._closure_started is None:
                self._closure_started = now
            return

        if self._closure_started is None:
            return

        duration = now - self._closure_started
        self._closure_started = None
        self._last_blink_duration = duration

        # Only short closures are blinks. A longer one is an EYES_CLOSED event and
        # must not also inflate the blink count, or a user resting their eyes
        # would appear to be blinking rapidly.
        if 0.0 < duration <= self._settings.blink_max_duration_seconds:
            self._blink_count += 1
            self._blink_times.append(now)
            self._trim_blinks(now)

    def _trim_blinks(self, now: float) -> None:
        cutoff = now - self._RATE_WINDOW_SECONDS
        while self._blink_times and self._blink_times[0] < cutoff:
            self._blink_times.popleft()

    def _suspend(self, timestamp: float) -> list[BehaviorEvent]:
        """No usable eye data - close anything open and attribute nothing.

        Reached when the camera fails, the face leaves frame, or the eyes are
        unmeasurable. In none of those cases do we know whether the eyes are
        shut, so an in-progress closure cannot honestly be continued.
        """
        if self._suspended:
            return []

        self._suspended = True
        self._closure_started = None

        event = self._closed.flush(timestamp)
        return [event] if event is not None else []

    def flush(self, timestamp: float) -> list[BehaviorEvent]:
        """Close any open event, e.g. at session end."""
        self._closure_started = None
        event = self._closed.flush(timestamp)
        return [event] if event is not None else []
