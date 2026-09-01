"""Drowsiness detection (spec section 13).

WHAT MAKES THIS DIFFERENT FROM EYES_CLOSED
------------------------------------------
`EYES_CLOSED` is a measurement: the eyes were shut for over a second.
`DROWSINESS` is an *interpretation* of that measurement, and interpretations
demand more evidence (spec section 61).

So a closure alone triggers the event, but three further signals decide how
confident the claim is:

| Signal | What it catches |
|---|---|
| **Prolonged closure** (>= 2.5 s) | the trigger - eyes shut far longer than any blink |
| **PERCLOS** | many partial closures rather than one long one |
| **Slow closures** | blinks that have become sluggish |
| **Head pitched down** | the nodding that accompanies real sleepiness (Phase 6) |

WHY PERCLOS
-----------
PERCLOS - the percentage of time the eyes are closed over a rolling window - is
the most extensively validated drowsiness measure in the driver-fatigue
literature. It exists because the failure mode it catches is invisible to a
single-closure test: somebody fighting sleep produces many half-second closures,
never one obvious three-second one, and a duration threshold alone scores them as
wide awake.

It is used here only to *corroborate*, never to accuse on its own. A user reading
a long document blinks more and could drift over the threshold, and "you look
sleepy" on that basis alone would be exactly the kind of weak claim section 61
forbids.

WHAT IS NOT CLAIMED
-------------------
This reports observable behaviour: the eyes were shut for N seconds, with these
corroborating signals, at this confidence. It does not diagnose fatigue, sleep
disorders or any medical condition (spec section 8). The user-facing wording is
"extended eye closure detected", never "you are asleep".

DOUBLE COUNTING
---------------
A drowsiness event always overlaps an EYES_CLOSED event, because the same closure
produced both. The scoring engine must take the greater penalty of the two rather
than adding them - see ``EVENT_SUPERSEDES`` in focusguard_shared.constants.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from focusguard_agent.config import VisionSettings
from focusguard_agent.events.state_machine import BehaviorEvent, EventStateMachine
from focusguard_shared.constants import VisionEventType
from focusguard_shared.utilities import get_logger

logger = get_logger("events.drowsiness")


@dataclass
class DrowsinessObservation:
    """One frame's worth of drowsiness-relevant information."""

    timestamp: float
    camera_ok: bool
    face_present: bool

    eyes_closed: bool
    #: Confidence that the eye reading itself is right (EAR/blendshape agreement).
    eye_confidence: float = 0.95
    valid: bool = True

    #: Head pitch in degrees, negative when looking down. None until Phase 6.
    head_pitch: float | None = None

    @property
    def usable(self) -> bool:
        return self.camera_ok and self.face_present and self.valid


@dataclass
class DrowsinessIndicators:
    """The corroborating evidence behind the current assessment.

    Persisted with the event and shown in reports, so a user is never told they
    looked drowsy without also being shown why (spec sections 9, 41).
    """

    perclos: float = 0.0
    perclos_elevated: bool = False
    slow_closure_count: int = 0
    slow_closures_elevated: bool = False
    head_down: bool = False
    corroborating: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.corroborating)


class DrowsinessMonitor:
    """Emits DROWSINESS events, with confidence set by corroborating signals."""

    #: Confidence for a prolonged closure with no corroboration at all, and the
    #: bonus each additional agreeing signal adds. A long closure is decent
    #: evidence by itself; corroboration should strengthen it, not be required.
    _BASE_CONFIDENCE = 0.80
    _PER_INDICATOR = 0.06
    _MAX_CONFIDENCE = 0.98

    def __init__(self, settings: VisionSettings) -> None:
        self._settings = settings

        # Fed with `eyes_closed` and given the drowsiness duration as its grace
        # period, so the event starts when the eyes actually closed and is only
        # confirmed once the closure has outlasted any plausible blink.
        self._machine = EventStateMachine(
            event_type=VisionEventType.DROWSINESS,
            grace_seconds=settings.drowsy_duration_seconds,
            exit_hysteresis_seconds=settings.event_exit_hysteresis_seconds,
        )

        self._samples: deque[tuple[float, bool]] = deque()
        self._slow_closures: deque[float] = deque()
        self._closure_started: float | None = None
        self._head_down = False
        self._suspended = False
        self._last_indicators = DrowsinessIndicators()

    # -- state -------------------------------------------------------------

    @property
    def is_drowsy(self) -> bool:
        return self._machine.is_active

    @property
    def suspended(self) -> bool:
        return self._suspended

    @property
    def indicators(self) -> DrowsinessIndicators:
        return self._last_indicators

    def perclos(self, now: float) -> float:
        """Fraction of the recent window spent with the eyes closed, 0.0-1.0."""
        self._trim(now)
        if len(self._samples) < 2:
            return 0.0

        closed = 0.0
        total = 0.0
        previous_time, previous_closed = self._samples[0]

        for timestamp, closed_now in list(self._samples)[1:]:
            gap = timestamp - previous_time
            # Long gaps mean monitoring was suspended. Counting them would
            # attribute camera downtime to the user as eyes-closed time.
            if 0 < gap <= self._settings.perclos_max_sample_gap_seconds:
                total += gap
                if previous_closed:
                    closed += gap
            previous_time, previous_closed = timestamp, closed_now

        return closed / total if total > 0 else 0.0

    def slow_closure_count(self, now: float) -> int:
        self._trim_slow_closures(now)
        return len(self._slow_closures)

    # -- driving -----------------------------------------------------------

    def update(self, observation: DrowsinessObservation) -> list[BehaviorEvent]:
        """Advance by one observation, returning any completed events."""
        if not observation.usable:
            return self._suspend(observation.timestamp)

        self._suspended = False
        now = observation.timestamp

        self._samples.append((now, observation.eyes_closed))
        self._trim(now)
        self._track_slow_closures(observation)

        if observation.head_pitch is not None:
            self._head_down = (
                observation.head_pitch < -self._settings.pitch_down_threshold_degrees
            )

        indicators = self._assess(now)
        self._last_indicators = indicators

        confidence = min(
            self._MAX_CONFIDENCE,
            observation.eye_confidence
            * (self._BASE_CONFIDENCE + self._PER_INDICATOR * indicators.count),
        )

        event = self._machine.update(
            condition=observation.eyes_closed,
            now=now,
            confidence=confidence,
        )
        return [event] if event is not None else []

    def _assess(self, now: float) -> DrowsinessIndicators:
        perclos = self.perclos(now)
        perclos_elevated = perclos >= self._settings.perclos_threshold

        slow_count = self.slow_closure_count(now)
        slow_elevated = slow_count >= self._settings.drowsy_slow_blink_count

        corroborating: list[str] = []
        if perclos_elevated:
            corroborating.append(f"PERCLOS {perclos * 100:.0f}%")
        if slow_elevated:
            corroborating.append(f"{slow_count} slow closures")
        if self._head_down:
            corroborating.append("head down")

        return DrowsinessIndicators(
            perclos=perclos,
            perclos_elevated=perclos_elevated,
            slow_closure_count=slow_count,
            slow_closures_elevated=slow_elevated,
            head_down=self._head_down,
            corroborating=corroborating,
        )

    def _track_slow_closures(self, observation: DrowsinessObservation) -> None:
        """Count closures longer than a blink but shorter than the drowsy threshold.

        These are the sluggish blinks that precede obvious sleepiness, and they
        would otherwise fall between the blink counter and the closure detector.
        """
        now = observation.timestamp

        if observation.eyes_closed:
            if self._closure_started is None:
                self._closure_started = now
            return

        if self._closure_started is None:
            return

        duration = now - self._closure_started
        self._closure_started = None

        if (
            self._settings.blink_max_duration_seconds
            < duration
            < self._settings.drowsy_duration_seconds
        ):
            self._slow_closures.append(now)
            self._trim_slow_closures(now)

    def _trim(self, now: float) -> None:
        cutoff = now - self._settings.perclos_window_seconds
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _trim_slow_closures(self, now: float) -> None:
        cutoff = now - self._settings.drowsy_slow_blink_window_seconds
        while self._slow_closures and self._slow_closures[0] < cutoff:
            self._slow_closures.popleft()

    def _suspend(self, timestamp: float) -> list[BehaviorEvent]:
        """No usable data - close anything open and attribute nothing.

        PERCLOS history is kept rather than cleared: a brief camera glitch should
        not erase a minute of legitimate observation. The sample-gap limit already
        prevents the downtime itself from being counted as closed-eye time.
        """
        if self._suspended:
            return []

        self._suspended = True
        self._closure_started = None

        event = self._machine.flush(timestamp)
        return [event] if event is not None else []

    def flush(self, timestamp: float) -> list[BehaviorEvent]:
        self._closure_started = None
        event = self._machine.flush(timestamp)
        return [event] if event is not None else []
