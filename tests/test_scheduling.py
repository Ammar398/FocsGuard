"""Frame-rate limiter tests (spec sections 3, 44).

A stage can only run when a frame is available, so achievable rates are divisors
of the capture rate. These tests pin the real measured case: a 25 FPS camera with
a 12 Hz target was running at 8.8 Hz, a 30% shortfall with no CPU saving.
"""

from __future__ import annotations

import pytest

from focusguard_agent.vision.scheduling import FrameRateLimiter


def _simulate(target_fps: float, camera_fps: float, seconds: float = 10.0) -> float:
    """Run the limiter against an evenly spaced frame grid; return achieved Hz."""
    limiter = FrameRateLimiter(target_fps)
    frame_period = 1.0 / camera_fps

    runs = 0
    frames = int(seconds * camera_fps)
    for index in range(frames):
        now = index * frame_period
        if limiter.should_run(now, frame_period):
            runs += 1

    # The first call always runs, so exclude it from the rate.
    return (runs - 1) / seconds


def test_twelve_hz_target_on_a_25fps_camera() -> None:
    """The exact case the benchmark caught.

    12 Hz is not achievable on a 25 FPS grid; the nearest is 12.5 Hz (every second
    frame). The naive test undershot to 8.3 Hz by rejecting the 80 ms frame for
    being 3 ms early.
    """
    achieved = _simulate(target_fps=12.0, camera_fps=25.0)
    assert achieved == pytest.approx(12.5, abs=0.2)


def test_three_hz_target_does_not_overshoot() -> None:
    """Phone detection is the most expensive stage; running it fast is costly.

    A cruder "60% of the interval" rule would fire at 200 ms instead of 333 ms and
    deliver 5 Hz - two-thirds faster than requested.
    """
    achieved = _simulate(target_fps=3.0, camera_fps=25.0)
    assert achieved == pytest.approx(3.1, abs=0.3)
    assert achieved < 4.0, "must never run substantially faster than requested"


@pytest.mark.parametrize("target", [2.0, 3.0, 5.0, 8.0, 12.0, 15.0])
def test_never_runs_far_above_the_target(target: float) -> None:
    achieved = _simulate(target_fps=target, camera_fps=25.0)
    assert achieved <= target * 1.30, f"{achieved:.1f} Hz is well above {target} Hz"


@pytest.mark.parametrize("target", [3.0, 8.0, 12.0])
def test_stays_close_to_the_target(target: float) -> None:
    """The whole point: land near the requested rate, not 30% under it."""
    achieved = _simulate(target_fps=target, camera_fps=25.0)
    assert achieved >= target * 0.85, f"{achieved:.1f} Hz undershoots {target} Hz"


def test_first_call_always_runs() -> None:
    limiter = FrameRateLimiter(12.0)
    assert limiter.should_run(100.0, 0.04) is True


def test_second_call_is_refused_when_too_early() -> None:
    limiter = FrameRateLimiter(12.0)
    limiter.should_run(0.0, 0.04)
    assert limiter.should_run(0.01, 0.04) is False


def test_tolerance_is_capped_at_half_the_interval() -> None:
    """A very slow camera must not let a stage run at nearly double its rate."""
    achieved = _simulate(target_fps=10.0, camera_fps=2.0, seconds=60.0)
    assert achieved <= 2.0, "cannot exceed the camera rate"


def test_runs_are_counted() -> None:
    limiter = FrameRateLimiter(10.0)
    for index in range(10):
        limiter.should_run(index * 0.1, 0.04)
    assert limiter.runs == 10


def test_reset_clears_state() -> None:
    limiter = FrameRateLimiter(10.0)
    limiter.should_run(0.0, 0.04)
    limiter.reset()
    assert limiter.runs == 0
    assert limiter.should_run(0.0, 0.04) is True


def test_target_fps_must_be_positive() -> None:
    with pytest.raises(ValueError):
        FrameRateLimiter(0)
