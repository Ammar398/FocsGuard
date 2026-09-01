"""Focus scoring (spec sections 18, 57, 59, 60).

WHERE THIS SITS
---------------
Detectors report observations. The event engine turns those into events with
durations and confidences. **This is the only module that decides what any of it
costs.** No detector may touch a score (spec section 57), which is what makes the
whole thing testable: this engine takes a set of active events and a timestamp,
and is otherwise a pure function of its own state.

THE SCORE MUST NOT OSCILLATE
----------------------------
Section 18 forbids 100 -> 20 -> 100. The raw penalty *does* jump like that: a
phone appears and 35 points vanish in one frame. So the reported score follows the
raw target through an exponential filter, and the filter is **asymmetric**:

    falling:   alpha = 0.15   time constant ~6 s
    recovering: alpha = 0.04  time constant ~25 s

A distraction registers promptly; the score has to be earned back. That asymmetry
is the whole anti-oscillation mechanism, and it is one line.

The alphas are defined **per second**, not per update. The pipeline updates at
whatever rate it manages - 12 Hz for landmarks, 1 Hz for score samples - and an
alpha applied per-update would make the smoothing speed depend on the frame rate.
Converting with ``1 - (1 - alpha) ** dt`` makes the behaviour identical whether
updates arrive at 1 Hz or 30 Hz.

NO DOUBLE COUNTING
------------------
A long eye closure raises both EYES_CLOSED and DROWSINESS. Adding both penalties
(10 + 40) would charge the user twice for one behaviour, so `resolve_superseded`
drops the covered event first (see focusguard_shared.constants).

SUSPENDED TIME BELONGS TO NOBODY
--------------------------------
While the camera is down, the score is frozen and no time is accumulated - not as
focused, not as distracted. A phone that locked mid-session must not quietly drag
the session average down.

TWO AXES, COMBINED LAST
-----------------------
Visual attention and digital productivity are scored separately and only combined
at the end (section 59). Someone can stare attentively at Netflix: high attention,
low productivity. Until activity monitoring exists (Phase 9), productivity has no
input and the combined score is simply the attention score, rather than a
half-invented number.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from focusguard_agent.config import ScoringSettings
from focusguard_shared.constants import (
    MonitorProfile,
    VisionEventType,
    resolve_superseded,
)
from focusguard_shared.utilities import get_logger

logger = get_logger("scoring.engine")

_MAX_SCORE = 100.0
_MIN_SCORE = 0.0

#: Updates further apart than this are treated as a gap rather than smoothed
#: across, so a pause does not produce one enormous smoothing step.
_MAX_SMOOTHING_STEP_SECONDS = 5.0


@dataclass(frozen=True)
class FocusSnapshot:
    """The scores at one instant (spec section 18)."""

    timestamp: float

    #: Unsmoothed 100 - penalty. Useful for the debug view; never shown to users,
    #: because it is exactly the jumpy value the smoothing exists to tame.
    instant_score: float

    visual_attention_score: float
    digital_productivity_score: float | None
    combined_focus_score: float

    rolling_1min: float
    session_average: float

    active_events: tuple[VisionEventType, ...] = ()
    penalty: float = 0.0
    suspended: bool = False

    @property
    def is_focused(self) -> bool:
        """No penalising event active.

        Deliberately not "score above N": a threshold on a smoothed value would
        make focused-time depend on how recently something else happened.
        """
        return not self.active_events


@dataclass
class SessionSummary:
    """Totals for the session report (spec sections 42, 43)."""

    duration_seconds: float = 0.0
    monitored_seconds: float = 0.0
    suspended_seconds: float = 0.0

    focused_seconds: float = 0.0
    distracted_seconds: float = 0.0

    average_focus_score: float = 0.0
    lowest_focus_score: float = 100.0
    longest_focus_block_seconds: float = 0.0

    #: Seconds spent with each event type active.
    event_seconds: dict[VisionEventType, float] = field(default_factory=dict)
    event_counts: dict[VisionEventType, int] = field(default_factory=dict)

    @property
    def focused_ratio(self) -> float:
        if self.monitored_seconds <= 0:
            return 0.0
        return self.focused_seconds / self.monitored_seconds


def compute_penalty(
    active_events: dict[VisionEventType, float],
    settings: ScoringSettings,
) -> tuple[float, tuple[VisionEventType, ...]]:
    """Total penalty for the currently active events, and which ones counted.

    Args:
        active_events: event type -> confidence, for events currently ACTIVE.

    Each penalty is scaled by confidence, so a contested reading costs less than
    a certain one (spec section 61).
    """
    if not active_events:
        return 0.0, ()

    counted = resolve_superseded(set(active_events))
    multi_monitor = settings.monitor_profile is not MonitorProfile.SINGLE

    total = 0.0
    for event_type in counted:
        weight = settings.event_weights.get(event_type, 0.0)

        # Section 60: with a second screen, looking away from the webcam is
        # frequently still work, so the same behaviour costs less.
        if event_type is VisionEventType.GAZE_AWAY and multi_monitor:
            weight *= settings.multi_monitor_gaze_penalty_scale

        total += weight * max(0.0, min(1.0, active_events[event_type]))

    return total, tuple(sorted(counted))


def smooth(previous: float, target: float, dt: float, settings: ScoringSettings) -> float:
    """Move `previous` toward `target`, faster downwards than upwards.

    The alphas are per-second, converted for the actual elapsed time so the
    smoothing speed does not depend on how often this is called.
    """
    if dt <= 0:
        return previous

    dt = min(dt, _MAX_SMOOTHING_STEP_SECONDS)
    alpha = (
        settings.smoothing_alpha_down if target < previous else settings.smoothing_alpha_up
    )

    effective = 1.0 - (1.0 - alpha) ** dt
    return previous + effective * (target - previous)


class FocusScoringEngine:
    """Turns active events into the six scores section 18 requires."""

    _ROLLING_WINDOW_DEFAULT = 60.0

    def __init__(self, settings: ScoringSettings | None = None) -> None:
        self._settings = settings or ScoringSettings()

        self._visual = _MAX_SCORE
        self._last_update: float | None = None
        self._started_at: float | None = None

        self._rolling: deque[tuple[float, float]] = deque()
        self._score_sum = 0.0
        self._score_samples = 0

        self._summary = SessionSummary()
        self._focus_block_started: float | None = None
        self._event_active_since: dict[VisionEventType, float] = {}

    # -- state -------------------------------------------------------------

    @property
    def visual_attention_score(self) -> float:
        return self._visual

    @property
    def summary(self) -> SessionSummary:
        return self._summary

    # -- driving -----------------------------------------------------------

    def update(
        self,
        now: float,
        active_events: dict[VisionEventType, float] | None = None,
        productivity_score: float | None = None,
        suspended: bool = False,
    ) -> FocusSnapshot:
        """Advance the score by one step.

        Args:
            active_events: event type -> confidence for events currently ACTIVE.
            productivity_score: 0-100 from activity monitoring, or None when it
                is unavailable (before Phase 9, or with no foreground data).
            suspended: True while the camera is unavailable. The score freezes
                and no time is attributed to anybody.
        """
        active_events = active_events or {}

        if self._started_at is None:
            self._started_at = now

        dt = 0.0 if self._last_update is None else max(0.0, now - self._last_update)
        self._last_update = now
        self._summary.duration_seconds = now - self._started_at

        if suspended:
            # Freeze. Nothing is measured, so nothing may be attributed.
            self._summary.suspended_seconds += dt
            self._close_focus_block()
            self._event_active_since.clear()
            return self._snapshot(now, 0.0, (), productivity_score, suspended=True)

        penalty, counted = compute_penalty(active_events, self._settings)
        target = max(_MIN_SCORE, min(_MAX_SCORE, _MAX_SCORE - penalty))

        self._visual = smooth(self._visual, target, dt, self._settings)
        self._accumulate(now, dt, counted)

        snapshot = self._snapshot(now, penalty, counted, productivity_score)
        self._record_sample(now, snapshot.combined_focus_score)
        return snapshot

    # -- internals ---------------------------------------------------------

    def _combine(self, productivity_score: float | None) -> float:
        """Blend the two axes (spec section 59).

        With no productivity data the combined score IS the attention score.
        Inventing a neutral 50 would drag every score toward the middle and make
        the number meaningless before Phase 9 exists.
        """
        if productivity_score is None:
            return self._visual

        weights = (
            self._settings.visual_attention_weight
            + self._settings.digital_productivity_weight
        )
        if weights <= 0:
            return self._visual

        return (
            self._settings.visual_attention_weight * self._visual
            + self._settings.digital_productivity_weight * productivity_score
        ) / weights

    def _snapshot(
        self,
        now: float,
        penalty: float,
        counted: tuple[VisionEventType, ...],
        productivity_score: float | None,
        suspended: bool = False,
    ) -> FocusSnapshot:
        combined = self._combine(productivity_score)

        return FocusSnapshot(
            timestamp=now,
            instant_score=max(_MIN_SCORE, _MAX_SCORE - penalty),
            visual_attention_score=round(self._visual, 2),
            digital_productivity_score=(
                round(productivity_score, 2) if productivity_score is not None else None
            ),
            combined_focus_score=round(combined, 2),
            rolling_1min=round(self._rolling_average(now), 2),
            session_average=round(self._session_average(), 2),
            active_events=counted,
            penalty=round(penalty, 2),
            suspended=suspended,
        )

    def _accumulate(
        self, now: float, dt: float, counted: tuple[VisionEventType, ...]
    ) -> None:
        """Update time totals for the session report."""
        if dt <= 0:
            self._track_focus_block(now, bool(counted))
            self._track_event_starts(now, counted)
            return

        self._summary.monitored_seconds += dt

        if counted:
            self._summary.distracted_seconds += dt
        else:
            self._summary.focused_seconds += dt

        for event_type in counted:
            self._summary.event_seconds[event_type] = (
                self._summary.event_seconds.get(event_type, 0.0) + dt
            )

        self._track_focus_block(now, bool(counted))
        self._track_event_starts(now, counted)

    def _track_event_starts(
        self, now: float, counted: tuple[VisionEventType, ...]
    ) -> None:
        """Count how many times each event type became active."""
        current = set(counted)
        for event_type in current - set(self._event_active_since):
            self._event_active_since[event_type] = now
            self._summary.event_counts[event_type] = (
                self._summary.event_counts.get(event_type, 0) + 1
            )
        for event_type in set(self._event_active_since) - current:
            del self._event_active_since[event_type]

    def _track_focus_block(self, now: float, distracted: bool) -> None:
        """Track the longest uninterrupted focused stretch (spec section 42)."""
        if distracted:
            self._close_focus_block(now)
        elif self._focus_block_started is None:
            self._focus_block_started = now
        else:
            self._summary.longest_focus_block_seconds = max(
                self._summary.longest_focus_block_seconds,
                now - self._focus_block_started,
            )

    def _close_focus_block(self, now: float | None = None) -> None:
        if self._focus_block_started is not None and now is not None:
            self._summary.longest_focus_block_seconds = max(
                self._summary.longest_focus_block_seconds,
                now - self._focus_block_started,
            )
        self._focus_block_started = None

    def _record_sample(self, now: float, score: float) -> None:
        self._rolling.append((now, score))
        window = self._settings.rolling_window_seconds or self._ROLLING_WINDOW_DEFAULT
        cutoff = now - window
        while self._rolling and self._rolling[0][0] < cutoff:
            self._rolling.popleft()

        self._score_sum += score
        self._score_samples += 1
        self._summary.average_focus_score = round(self._session_average(), 2)
        self._summary.lowest_focus_score = round(
            min(self._summary.lowest_focus_score, score), 2
        )

    def _rolling_average(self, now: float) -> float:
        if not self._rolling:
            return self._visual
        return sum(score for _, score in self._rolling) / len(self._rolling)

    def _session_average(self) -> float:
        if self._score_samples == 0:
            return self._visual
        return self._score_sum / self._score_samples

    def finish(self, now: float) -> SessionSummary:
        """Close the session and return its totals."""
        self._close_focus_block(now)
        self._summary.average_focus_score = round(self._session_average(), 2)
        if self._started_at is not None:
            self._summary.duration_seconds = now - self._started_at

        counts: dict[VisionEventType, int] = defaultdict(int)
        counts.update(self._summary.event_counts)
        self._summary.event_counts = dict(counts)
        return self._summary
