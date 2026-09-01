"""EAR calibration tests (spec sections 12, 48)."""

from __future__ import annotations

import json

import pytest

from focusguard_agent.calibration.ear_calibration import (
    MIN_CALIBRATION_SAMPLES,
    CalibrationProfile,
    EarCalibrator,
    compute_ear_threshold,
    load_profile,
    save_profile,
)


def _steady(value: float, count: int = 60, spread: float = 0.01) -> list[float]:
    """Samples around `value` with small, regular variation."""
    return [value + (spread if index % 2 else -spread) for index in range(count)]


# --- the threshold ----------------------------------------------------------


def test_threshold_sits_below_the_users_normal_range() -> None:
    """Normal variation must not trip the threshold; a real closure must."""
    profile = compute_ear_threshold(_steady(0.28, spread=0.01), k=2.5, drop_blinks=False)

    assert profile.ear_threshold < 0.28
    assert profile.ear_mean_open == pytest.approx(0.28, abs=0.005)
    assert profile.ear_threshold < 0.28 - 0.01, "below the lowest normal reading"


def test_users_with_different_eyes_get_different_thresholds() -> None:
    """The whole reason calibration exists (spec section 12)."""
    narrow = compute_ear_threshold(_steady(0.19), drop_blinks=False)
    wide = compute_ear_threshold(_steady(0.33), drop_blinks=False)

    assert narrow.ear_threshold < wide.ear_threshold


def test_blinks_during_calibration_are_discarded() -> None:
    """Users blink while calibrating; asking them not to gives strained eyes.

    Left in, the blinks would drag the mean down and inflate the standard
    deviation, producing a threshold far too low to ever detect a closure.
    """
    samples = _steady(0.30, count=50, spread=0.005) + [0.05] * 12  # 12 blinks

    with_blinks = compute_ear_threshold(samples, drop_blinks=True)
    without = compute_ear_threshold(_steady(0.30, count=50, spread=0.005), drop_blinks=False)

    assert with_blinks.ear_threshold == pytest.approx(without.ear_threshold, abs=0.03)
    assert with_blinks.ear_mean_open > 0.25, "blinks must not drag the open mean down"


# --- the safety clamp -------------------------------------------------------


def test_a_wildly_low_result_is_clamped_and_flagged() -> None:
    """A bad calibration must degrade to slightly wrong, not completely broken."""
    erratic = [0.30, 0.05, 0.28, 0.06, 0.31, 0.04] * 10
    profile = compute_ear_threshold(erratic, drop_blinks=False)

    assert profile.ear_threshold >= 0.15
    assert profile.clamped is True, "must be reported so the user can recalibrate"


def test_a_wildly_high_result_is_clamped() -> None:
    profile = compute_ear_threshold(_steady(0.60, spread=0.0001), drop_blinks=False)

    assert profile.ear_threshold <= 0.30
    assert profile.clamped is True


def test_a_good_calibration_is_not_flagged_as_clamped() -> None:
    profile = compute_ear_threshold(_steady(0.28, spread=0.01), drop_blinks=False)
    assert profile.clamped is False


@pytest.mark.parametrize("mean", [0.16, 0.20, 0.25, 0.30, 0.35])
def test_threshold_always_lands_inside_the_safe_range(mean: float) -> None:
    profile = compute_ear_threshold(_steady(mean), drop_blinks=False)
    assert 0.15 <= profile.ear_threshold <= 0.30


# --- refusing bad input -----------------------------------------------------


def test_too_few_samples_is_an_error_not_a_bad_threshold() -> None:
    with pytest.raises(ValueError, match="at least"):
        compute_ear_threshold([0.3] * (MIN_CALIBRATION_SAMPLES - 1))


def test_invalid_samples_are_ignored() -> None:
    samples = _steady(0.28) + [0.0, -1.0, 0.0]
    profile = compute_ear_threshold(samples, drop_blinks=False)
    assert profile.samples_used == 60


# --- the collector ----------------------------------------------------------


def test_calibrator_collects_and_reports_readiness() -> None:
    calibrator = EarCalibrator()
    assert calibrator.has_enough is False

    for value in _steady(0.29):
        calibrator.add(value)

    assert calibrator.has_enough is True
    assert calibrator.sample_count == 60
    assert calibrator.result().ear_threshold < 0.29


def test_calibrator_ignores_unmeasurable_frames() -> None:
    calibrator = EarCalibrator()
    calibrator.add(0.0)
    calibrator.add(0.3)
    assert calibrator.sample_count == 1


def test_calibrator_reset_clears_samples() -> None:
    calibrator = EarCalibrator()
    for value in _steady(0.29):
        calibrator.add(value)
    calibrator.reset()
    assert calibrator.sample_count == 0


# --- persistence ------------------------------------------------------------


def test_profile_survives_a_save_and_load(tmp_path) -> None:
    path = tmp_path / "calibration.json"
    original = compute_ear_threshold(_steady(0.27), drop_blinks=False)

    save_profile(original, path)
    loaded = load_profile(path)

    assert loaded is not None
    assert loaded.ear_threshold == original.ear_threshold


def test_missing_profile_returns_none() -> None:
    assert load_profile(__import__("pathlib").Path("nope.json")) is None


def test_corrupt_profile_returns_none_rather_than_crashing(tmp_path) -> None:
    """A damaged profile must never stop the agent starting."""
    path = tmp_path / "calibration.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_profile(path) is None


def test_unknown_fields_in_a_profile_are_ignored(tmp_path) -> None:
    """An older agent must still read a profile written by a newer one."""
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(
            {
                "ear_threshold": 0.22,
                "ear_mean_open": 0.30,
                "ear_std_open": 0.03,
                "samples_used": 100,
                "future_field": "ignored",
            }
        ),
        encoding="utf-8",
    )

    loaded = load_profile(path)
    assert loaded is not None
    assert loaded.ear_threshold == 0.22


def test_profile_round_trips_through_a_dict() -> None:
    profile = CalibrationProfile(
        ear_threshold=0.2, ear_mean_open=0.3, ear_std_open=0.04, samples_used=50
    )
    assert CalibrationProfile.from_dict(profile.to_dict()).ear_threshold == 0.2
