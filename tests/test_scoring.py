"""Focus scoring tests (spec sections 18, 57, 59, 60).

Every number a user sees comes from this engine, so it is tested harder than
anything else. It takes a timestamp and a set of active events and is otherwise
pure - no camera, no clock, no database.
"""

from __future__ import annotations

import pytest

from focusguard_agent.config import ScoringSettings
from focusguard_agent.scoring.engine import (
    FocusScoringEngine,
    compute_penalty,
    smooth,
)
from focusguard_agent.scoring.productivity import ProductivityTracker
from focusguard_shared.constants import (
    Classification,
    MonitorProfile,
    VisionEventType,
)

PHONE = VisionEventType.PHONE_VISIBLE
GAZE = VisionEventType.GAZE_AWAY
DROWSY = VisionEventType.DROWSINESS
CLOSED = VisionEventType.EYES_CLOSED
ABSENT = VisionEventType.FACE_ABSENT


def _settings(**overrides) -> ScoringSettings:
    return ScoringSettings(**overrides)


def _run(
    engine: FocusScoringEngine,
    events: dict[VisionEventType, float] | None,
    start: float,
    seconds: float,
    step: float = 1.0,
    **kwargs,
):
    """Drive the engine for a span, returning the final snapshot."""
    snapshot = None
    time = start
    while time <= start + seconds + 1e-9:
        snapshot = engine.update(time, events, **kwargs)
        time += step
    return snapshot


# --- penalties --------------------------------------------------------------


def test_no_events_means_no_penalty() -> None:
    penalty, counted = compute_penalty({}, _settings())
    assert penalty == 0.0
    assert counted == ()


def test_penalty_uses_the_configured_weight() -> None:
    penalty, _ = compute_penalty({PHONE: 1.0}, _settings())
    assert penalty == pytest.approx(35.0)


def test_penalty_scales_with_confidence() -> None:
    """A contested reading must cost less than a certain one (section 61)."""
    certain, _ = compute_penalty({PHONE: 1.0}, _settings())
    unsure, _ = compute_penalty({PHONE: 0.5}, _settings())

    assert unsure == pytest.approx(certain / 2)


def test_simultaneous_events_add_up() -> None:
    penalty, counted = compute_penalty({PHONE: 1.0, GAZE: 1.0}, _settings())

    assert penalty == pytest.approx(50.0)
    assert set(counted) == {PHONE, GAZE}


def test_drowsiness_supersedes_eyes_closed_rather_than_adding_to_it() -> None:
    """One closure produced both events; charging for both would double-count.

    Summing would give 50 - more than either event is meant to cost.
    """
    penalty, counted = compute_penalty({DROWSY: 1.0, CLOSED: 1.0}, _settings())

    assert penalty == pytest.approx(40.0), "the drowsiness weight alone"
    assert counted == (DROWSY,)


def test_an_unknown_event_type_costs_nothing() -> None:
    penalty, _ = compute_penalty({VisionEventType.FOCUSED: 1.0}, _settings())
    assert penalty == 0.0


# --- multi-monitor (spec section 60) ---------------------------------------


def test_gaze_away_costs_less_on_a_multi_monitor_setup() -> None:
    single, _ = compute_penalty({GAZE: 1.0}, _settings(monitor_profile=MonitorProfile.SINGLE))
    dual, _ = compute_penalty({GAZE: 1.0}, _settings(monitor_profile=MonitorProfile.DUAL))

    assert dual < single
    assert dual == pytest.approx(single * 0.4)


def test_the_multi_monitor_discount_applies_only_to_gaze() -> None:
    """A phone is a phone regardless of how many screens are on the desk."""
    single, _ = compute_penalty({PHONE: 1.0}, _settings(monitor_profile=MonitorProfile.SINGLE))
    dual, _ = compute_penalty({PHONE: 1.0}, _settings(monitor_profile=MonitorProfile.MULTI))

    assert dual == pytest.approx(single)


# --- smoothing: the anti-oscillation mechanism ------------------------------


def test_the_score_never_jumps_straight_to_the_target() -> None:
    """Section 18 forbids 100 -> 20 -> 100."""
    settings = _settings()
    after_one_second = smooth(100.0, 20.0, dt=1.0, settings=settings)

    assert after_one_second < 100.0, "it must move"
    assert after_one_second > 80.0, "but nowhere near the target in one step"


def test_the_score_falls_faster_than_it_recovers() -> None:
    """A distraction registers promptly; the score has to be earned back."""
    settings = _settings()

    fall = 100.0 - smooth(100.0, 50.0, dt=1.0, settings=settings)
    rise = smooth(50.0, 100.0, dt=1.0, settings=settings) - 50.0

    assert fall > rise * 2


def test_smoothing_speed_does_not_depend_on_the_update_rate() -> None:
    """The alphas are per second, so 1 Hz and 10 Hz must agree after 10 seconds.

    Applying an alpha per update instead would make the score fall ten times
    faster on a fast machine than a slow one.
    """
    settings = _settings()

    slow = 100.0
    for _ in range(10):
        slow = smooth(slow, 0.0, dt=1.0, settings=settings)

    fast = 100.0
    for _ in range(100):
        fast = smooth(fast, 0.0, dt=0.1, settings=settings)

    assert slow == pytest.approx(fast, abs=0.5)


def test_a_zero_time_step_changes_nothing() -> None:
    assert smooth(80.0, 20.0, dt=0.0, settings=_settings()) == 80.0


def test_a_long_gap_does_not_produce_one_enormous_step() -> None:
    """After a pause, the score must not snap to the target."""
    settings = _settings()
    result = smooth(100.0, 0.0, dt=3600.0, settings=settings)
    assert result > 40.0


# --- the engine end to end --------------------------------------------------


def test_a_focused_session_stays_near_one_hundred() -> None:
    engine = FocusScoringEngine(_settings())
    snapshot = _run(engine, {}, start=0.0, seconds=60)

    assert snapshot.combined_focus_score == pytest.approx(100.0, abs=0.1)
    assert snapshot.is_focused is True


def test_a_distraction_lowers_the_score_and_recovery_takes_longer() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=10)
    during = _run(engine, {PHONE: 0.9}, start=11.0, seconds=20)
    assert during.visual_attention_score < 80.0

    ten_seconds_later = _run(engine, {}, start=32.0, seconds=10)
    assert ten_seconds_later.visual_attention_score > during.visual_attention_score
    assert ten_seconds_later.visual_attention_score < 95.0, "recovery is gradual"


def test_the_score_stays_within_bounds_under_every_event_at_once() -> None:
    engine = FocusScoringEngine(_settings())
    everything = {PHONE: 1.0, GAZE: 1.0, DROWSY: 1.0, ABSENT: 1.0}

    snapshot = _run(engine, everything, start=0.0, seconds=300)

    assert 0.0 <= snapshot.visual_attention_score <= 100.0
    assert snapshot.instant_score == 0.0, "penalties exceed 100 but the score floors"


def test_the_instant_score_is_reported_separately_from_the_smoothed_one() -> None:
    engine = FocusScoringEngine(_settings())
    snapshot = engine.update(0.0, {PHONE: 1.0})

    assert snapshot.instant_score == pytest.approx(65.0)
    assert snapshot.visual_attention_score == pytest.approx(100.0), "smoothing lags"


# --- suspended time belongs to nobody --------------------------------------


def test_a_suspended_camera_freezes_the_score() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=10)
    before = engine.visual_attention_score

    _run(engine, {PHONE: 1.0}, start=11.0, seconds=60, suspended=True)

    assert engine.visual_attention_score == pytest.approx(before)


def test_suspended_time_is_neither_focused_nor_distracted() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=10)
    _run(engine, {}, start=11.0, seconds=30, suspended=True)
    summary = engine.finish(41.0)

    assert summary.suspended_seconds > 25
    assert summary.focused_seconds < 15
    assert summary.distracted_seconds == 0.0


def test_a_locked_phone_does_not_drag_the_session_average_down() -> None:
    """The whole point of freezing: a technical fault must not cost the user."""
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=30)
    focused_average = engine.summary.average_focus_score

    _run(engine, {}, start=31.0, seconds=120, suspended=True)

    assert engine.summary.average_focus_score == pytest.approx(focused_average)


# --- session summary (spec section 42) -------------------------------------


def test_focused_and_distracted_time_are_tracked() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=30)
    _run(engine, {PHONE: 1.0}, start=31.0, seconds=10)
    summary = engine.finish(41.0)

    assert summary.focused_seconds == pytest.approx(30, abs=3)
    assert summary.distracted_seconds == pytest.approx(10, abs=3)
    assert summary.focused_ratio > 0.6


def test_time_per_event_type_is_recorded() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {PHONE: 1.0}, start=0.0, seconds=20)
    _run(engine, {GAZE: 1.0}, start=21.0, seconds=10)
    summary = engine.finish(31.0)

    assert summary.event_seconds[PHONE] == pytest.approx(20, abs=3)
    assert summary.event_seconds[GAZE] == pytest.approx(10, abs=3)


def test_event_occurrences_are_counted() -> None:
    """Feeds the "phone distractions: 3" figure."""
    engine = FocusScoringEngine(_settings())

    for index in range(3):
        base = index * 20.0
        _run(engine, {}, start=base, seconds=5)
        _run(engine, {PHONE: 1.0}, start=base + 6, seconds=5)

    summary = engine.finish(60.0)
    assert summary.event_counts[PHONE] == 3


def test_the_longest_uninterrupted_focus_block_is_measured() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=20)
    _run(engine, {PHONE: 1.0}, start=21.0, seconds=3)
    _run(engine, {}, start=25.0, seconds=45)  # the longer block
    summary = engine.finish(70.0)

    assert summary.longest_focus_block_seconds == pytest.approx(45, abs=4)


def test_the_lowest_score_of_the_session_is_recorded() -> None:
    engine = FocusScoringEngine(_settings())

    _run(engine, {}, start=0.0, seconds=5)
    _run(engine, {DROWSY: 1.0}, start=6.0, seconds=60)
    summary = engine.finish(66.0)

    assert summary.lowest_focus_score < 80.0


# --- the two axes (spec section 59) ----------------------------------------


def test_without_productivity_data_the_combined_score_is_the_attention_score() -> None:
    """Inventing a neutral 50 would drag every score toward the middle."""
    engine = FocusScoringEngine(_settings())
    snapshot = _run(engine, {}, start=0.0, seconds=10, productivity_score=None)

    assert snapshot.digital_productivity_score is None
    assert snapshot.combined_focus_score == pytest.approx(
        snapshot.visual_attention_score
    )


def test_attention_and_productivity_are_blended_when_both_exist() -> None:
    engine = FocusScoringEngine(_settings())
    snapshot = _run(engine, {}, start=0.0, seconds=10, productivity_score=20.0)

    assert snapshot.combined_focus_score == pytest.approx(60.0, abs=1.0)


def test_staring_attentively_at_the_wrong_thing_scores_badly() -> None:
    """The canonical section 59 case: full attention, no productivity."""
    engine = FocusScoringEngine(_settings())
    snapshot = _run(engine, {}, start=0.0, seconds=20, productivity_score=10.0)

    assert snapshot.visual_attention_score > 95.0
    assert snapshot.combined_focus_score < 60.0


# --- productivity tracker ---------------------------------------------------


def test_productivity_is_none_before_any_activity_data() -> None:
    assert ProductivityTracker().score() is None


def test_all_productive_time_scores_one_hundred() -> None:
    tracker = ProductivityTracker()
    for second in range(30):
        tracker.update(float(second), Classification.PRODUCTIVE)

    assert tracker.score() == pytest.approx(100.0)


def test_all_distracting_time_scores_zero() -> None:
    tracker = ProductivityTracker()
    for second in range(30):
        tracker.update(float(second), Classification.DISTRACTING)

    assert tracker.score() == pytest.approx(0.0)


def test_neutral_activity_counts_as_half() -> None:
    """Slack and email are real work; scoring them zero would be unfair and
    would push users toward gaming the metric."""
    tracker = ProductivityTracker()
    for second in range(30):
        tracker.update(float(second), Classification.NEUTRAL)

    assert tracker.score() == pytest.approx(50.0)


def test_mixed_activity_scores_proportionally() -> None:
    tracker = ProductivityTracker()
    for second in range(20):
        tracker.update(float(second), Classification.PRODUCTIVE)
    for second in range(20, 40):
        tracker.update(float(second), Classification.DISTRACTING)

    assert tracker.score() == pytest.approx(50.0, abs=5.0)


def test_idle_time_is_not_attributed_to_any_classification() -> None:
    tracker = ProductivityTracker()
    tracker.update(0.0, Classification.PRODUCTIVE)
    tracker.update(1.0, None)
    tracker.update(60.0, Classification.PRODUCTIVE)

    assert tracker.totals.total_seconds < 5.0, "the 59s idle gap is not counted"


def test_a_long_gap_between_samples_is_not_counted() -> None:
    """A pause must not be attributed to whatever was in front of the agent."""
    tracker = ProductivityTracker()
    tracker.update(0.0, Classification.DISTRACTING)
    tracker.update(3600.0, Classification.DISTRACTING)

    assert tracker.totals.total_seconds == 0.0


def test_session_totals_are_available_for_the_report() -> None:
    tracker = ProductivityTracker()
    for second in range(10):
        tracker.update(float(second), Classification.PRODUCTIVE)
    for second in range(10, 20):
        tracker.update(float(second), Classification.DISTRACTING)

    totals = tracker.totals
    assert totals.productive_seconds == pytest.approx(9, abs=2)
    assert totals.distracting_seconds == pytest.approx(9, abs=2)
    assert totals.ratio(Classification.PRODUCTIVE) == pytest.approx(0.5, abs=0.1)
