"""Rate limiting for pipeline stages that run slower than the camera.

THE PROBLEM THIS SOLVES
-----------------------
Detection stages deliberately run slower than capture (spec section 3), but they
can only run when a frame is available. That makes the achievable rates a
*divisor* of the capture rate, not a free choice.

Measured on this machine: the camera delivers a frame every 40 ms and the
landmark stage asks for one detection every 83.3 ms (12 Hz). A naive
``now - last >= interval`` test rejects the frame at 80 ms - it is 3 ms early -
and waits for the one at 120 ms. The stage then runs at **8.3 Hz instead of 12**,
a 30% shortfall with no CPU saving to show for it. The benchmark caught exactly
this: "Detections run: 219 (8.8 Hz, target 12 Hz)".

That undershoot matters for Phase 4. A blink lasts 100-400 ms, so at 8.3 Hz a
fast blink is covered by one or two samples; at 12.5 Hz it gets three or four.

THE FIX
-------
Allow a stage to run up to half a frame period early, so it snaps to whichever
frame boundary is *nearest* the target rather than always overshooting to the
next one. With a 40 ms frame period and an 83.3 ms target the threshold becomes
63.3 ms, the 80 ms frame is accepted, and the stage settles at 12.5 Hz - the
closest rate the frame grid can actually deliver.

The half-frame allowance is what keeps this correct at low rates too. Phone
detection at 3 Hz (333 ms) gets a 313 ms threshold and lands on the 320 ms frame
for 3.1 Hz. A cruder "run when 60% of the interval has passed" rule would fire at
200 ms and give 5 Hz - two-thirds faster than asked, on the most expensive stage
in the pipeline.
"""

from __future__ import annotations

from focusguard_shared.utilities import get_logger

logger = get_logger("vision.scheduling")

#: Frame period assumed before the camera has measured its real rate.
_DEFAULT_FRAME_PERIOD = 1.0 / 30.0


class FrameRateLimiter:
    """Decides when a stage should next run, aligned to the frame grid."""

    def __init__(self, target_fps: float) -> None:
        if target_fps <= 0:
            raise ValueError("target_fps must be positive")
        self.target_fps = target_fps
        self.interval = 1.0 / target_fps
        self._last_run: float | None = None
        self._runs = 0

    @property
    def runs(self) -> int:
        return self._runs

    def should_run(self, now: float, frame_period: float | None = None) -> bool:
        """True if the stage is due. Records the run when it returns True.

        Args:
            now: monotonic seconds.
            frame_period: seconds between camera frames. Pass the real measured
                value where available; the tolerance is half of it.
        """
        if self._last_run is None:
            self._last_run = now
            self._runs += 1
            return True

        period = frame_period if frame_period and frame_period > 0 else _DEFAULT_FRAME_PERIOD

        # Never allow more than half the target interval as tolerance, or a very
        # slow camera could let a stage run at nearly double its configured rate.
        tolerance = min(period / 2.0, self.interval / 2.0)

        if now - self._last_run >= self.interval - tolerance:
            self._last_run = now
            self._runs += 1
            return True

        return False

    def reset(self) -> None:
        self._last_run = None
        self._runs = 0
