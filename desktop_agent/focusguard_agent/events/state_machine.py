"""The generic event state machine (spec sections 11, 14, 16, 17, 61).

Every behavioural event in FocusGuard - looking away, eyes closed, drowsiness, a
phone in frame - is produced by this one class. Detectors report a per-frame
observation; this turns a stream of those into discrete events that have a start,
an end and a duration.

    IDLE --(condition true)--> CANDIDATE --(held past grace)--> ACTIVE
             ^                     |                              |
             |                     |  (condition false)           | (false past hysteresis)
             +---------------------+------------------------------+
                              nothing emitted        event emitted on exit

WHY A GRACE PERIOD
------------------
Without one, a single frame in which the face detector blinked out becomes a
recorded distraction. The specification is explicit that looking away for half a
second must not cost points (section 14) and that a face missing for under two
seconds must be ignored (section 11). The grace period is that rule, expressed
once, in one place.

WHY HYSTERESIS ON THE WAY OUT
-----------------------------
Detection flickers. Without hysteresis, one recovered frame in the middle of a
30-second phone appearance would split it into two events, and section 16 requires
that a continuous appearance produce exactly ONE event with a single start and end.

WHY THE EVENT STARTS BEFORE THE GRACE PERIOD ELAPSED
----------------------------------------------------
An event's ``start_time`` is when the condition first became true, not when the
machine promoted it to ACTIVE. If someone looks away for six seconds with a
four-second grace, the honest record is a six-second gaze-away, not a two-second
one. The grace period decides *whether* to believe the signal, not when it began.

CONFIDENCE IS AVERAGED, NOT PEAKED
----------------------------------
The confidence attached to an emitted event is the mean across its active window.
Using the peak would overstate certainty - a detector that was 90% sure for one
frame and 40% sure for thirty should not produce a 90%-confidence event
(section 61).

This class holds no reference to a camera, a clock or a database. It takes a
boolean and a timestamp, which is what makes the whole event layer testable
without hardware.
"""

from __future__ import annotations

from dataclasses import dataclass

from focusguard_shared.constants import EventState, VisionEventType


@dataclass(frozen=True)
class BehaviorEvent:
    """A completed event, in monotonic time.

    Kept separate from the ``VisionEvent`` upload schema because durations must be
    measured on a monotonic clock - a wall clock can jump backwards when the
    system syncs time, which would produce negative durations. Conversion to wall
    time happens once, at the session boundary.
    """

    event_type: VisionEventType
    start_time: float
    end_time: float
    confidence: float

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def __str__(self) -> str:
        return (
            f"{self.event_type} for {self.duration_seconds:.1f}s "
            f"(confidence {self.confidence:.2f})"
        )


class EventStateMachine:
    """Turns a per-frame boolean into discrete, de-duplicated events."""

    def __init__(
        self,
        event_type: VisionEventType,
        grace_seconds: float,
        exit_hysteresis_seconds: float = 0.5,
        min_duration_seconds: float = 0.0,
    ) -> None:
        """
        Args:
            event_type: what this machine detects.
            grace_seconds: how long the condition must hold before it is believed.
            exit_hysteresis_seconds: how long it must stay false before the event
                closes, so brief detection dropouts do not split one event in two.
            min_duration_seconds: events shorter than this are discarded on exit.
                Used where a short occurrence has a different meaning entirely -
                a brief eye closure is a blink, not an EYES_CLOSED event.
        """
        self.event_type = event_type
        self.grace_seconds = grace_seconds
        self.exit_hysteresis_seconds = exit_hysteresis_seconds
        self.min_duration_seconds = min_duration_seconds

        self._state = EventState.IDLE
        self._condition_since: float | None = None
        self._inactive_since: float | None = None
        self._confidence_sum = 0.0
        self._confidence_samples = 0

    # -- introspection -----------------------------------------------------

    @property
    def state(self) -> EventState:
        return self._state

    @property
    def is_active(self) -> bool:
        """True once the event is confirmed - what the scoring engine reads."""
        return self._state is EventState.ACTIVE

    def current_duration(self, now: float) -> float:
        """How long the current event has been running, 0.0 if none is active."""
        if self._state is not EventState.ACTIVE or self._condition_since is None:
            return 0.0
        return max(0.0, now - self._condition_since)

    @property
    def mean_confidence(self) -> float:
        if self._confidence_samples == 0:
            return 0.0
        return self._confidence_sum / self._confidence_samples

    # -- driving -----------------------------------------------------------

    def update(
        self,
        condition: bool,
        now: float,
        confidence: float = 1.0,
    ) -> BehaviorEvent | None:
        """Advance the machine by one observation.

        Returns a completed event at the moment one closes, otherwise None.
        """
        if condition:
            return self._on_condition_true(now, confidence)
        return self._on_condition_false(now)

    def _on_condition_true(self, now: float, confidence: float) -> None:
        if self._state is EventState.IDLE:
            self._state = EventState.CANDIDATE
            self._condition_since = now
            self._confidence_sum = confidence
            self._confidence_samples = 1
            self._inactive_since = None
            return None

        self._confidence_sum += confidence
        self._confidence_samples += 1
        # The condition is true again, so any pending close is cancelled. This is
        # the hysteresis that keeps one flickering detection as a single event.
        self._inactive_since = None

        if self._state is EventState.CANDIDATE:
            assert self._condition_since is not None
            if now - self._condition_since >= self.grace_seconds:
                self._state = EventState.ACTIVE

        return None

    def _on_condition_false(self, now: float) -> BehaviorEvent | None:
        if self._state is EventState.IDLE:
            return None

        if self._state is EventState.CANDIDATE:
            # Never survived the grace period, so it never happened.
            self._reset()
            return None

        # ACTIVE: wait out the hysteresis before closing.
        if self._inactive_since is None:
            self._inactive_since = now
            return None

        if now - self._inactive_since < self.exit_hysteresis_seconds:
            return None

        # The event ends when the condition became false, not when the hysteresis
        # expired - otherwise every event would be inflated by the hysteresis.
        event = self._build_event(end_time=self._inactive_since)
        self._reset()
        return event

    def flush(self, now: float) -> BehaviorEvent | None:
        """Close any open event, e.g. at session end or camera loss.

        Without this, ending a session mid-distraction would silently discard it.
        """
        if self._state is not EventState.ACTIVE:
            self._reset()
            return None

        end_time = self._inactive_since if self._inactive_since is not None else now
        event = self._build_event(end_time=end_time)
        self._reset()
        return event

    def reset(self) -> None:
        """Discard all state without emitting anything.

        Used when the camera drops: whatever was happening cannot be attributed to
        the user, so it is dropped rather than recorded (see events/presence.py).
        """
        self._reset()

    # -- internals ---------------------------------------------------------

    def _build_event(self, end_time: float) -> BehaviorEvent | None:
        assert self._condition_since is not None
        duration = end_time - self._condition_since
        if duration < self.min_duration_seconds:
            return None

        return BehaviorEvent(
            event_type=self.event_type,
            start_time=self._condition_since,
            end_time=end_time,
            confidence=self.mean_confidence,
        )

    def _reset(self) -> None:
        self._state = EventState.IDLE
        self._condition_since = None
        self._inactive_since = None
        self._confidence_sum = 0.0
        self._confidence_samples = 0
