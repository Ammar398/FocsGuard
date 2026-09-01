"""Personal EAR threshold calibration (spec sections 12, 48).

WHY A FIXED THRESHOLD IS WRONG
------------------------------
Published EAR work often quotes 0.21 as the closed-eye threshold. It is a
reasonable average and a poor individual fit. Open-eye EAR varies widely with eye
shape, whether the user wears glasses, and the angle the camera sits at - a phone
propped below the monitor foreshortens the eye vertically and lowers every reading.

A user whose relaxed EAR is 0.19 would be recorded as permanently asleep. One
whose relaxed EAR is 0.34 would never register a blink at all.

THE METHOD
----------
Sample EAR for a few seconds while the user looks normally at the screen, then set

    threshold = mean_open_EAR - (k * standard_deviation)      k = 2.5 by default

Subtracting multiples of the standard deviation places the threshold below the
user's normal variation, so ordinary fluctuation does not trip it while a real
closure - which drops EAR far below the open range - does.

Blinks during calibration are expected and are discarded before the statistics are
computed. Asking someone not to blink for five seconds produces strained, unnatural
eyes and a threshold that does not match how they actually sit.

THE CLAMP
---------
The result is clamped to [0.15, 0.30]. Calibration can go wrong - the user blinks
throughout, the camera is knocked, the face is half out of frame - and an
unclamped threshold of 0.02 or 0.60 would silently break every downstream signal.
The clamp bounds the damage to "slightly wrong" rather than "completely broken",
and a clamped result is recorded so it can be reported rather than hidden.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from focusguard_shared.utilities import get_logger
from focusguard_shared.utilities.paths import app_data_dir

logger = get_logger("calibration.ear")

#: Minimum usable samples. Below this the standard deviation is meaningless.
MIN_CALIBRATION_SAMPLES = 20

#: Samples in the lowest fraction of the range are treated as blinks and dropped.
_BLINK_PERCENTILE = 0.20


@dataclass
class CalibrationProfile:
    """A user's personal vision thresholds."""

    ear_threshold: float
    ear_mean_open: float
    ear_std_open: float
    samples_used: int

    #: True when the computed threshold hit the safety clamp, which usually means
    #: the calibration itself was poor and is worth re-running.
    clamped: bool = False

    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    agent_version: str = "0.1.0"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationProfile:
        known = {key: data[key] for key in data if key in cls.__dataclass_fields__}
        return cls(**known)


def default_profile_path() -> Path:
    return app_data_dir() / "calibration.json"


def save_profile(profile: CalibrationProfile, path: Path | None = None) -> Path:
    target = path or default_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
    logger.info("Saved calibration profile to %s", target)
    return target


def load_profile(path: Path | None = None) -> CalibrationProfile | None:
    """Load a saved profile, or None when there is none or it is unreadable.

    A corrupt profile must never stop the agent starting; the configured default
    threshold is used instead.
    """
    source = path or default_profile_path()
    if not source.exists():
        return None

    try:
        return CalibrationProfile.from_dict(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        logger.warning("Calibration profile at %s is unreadable; ignoring it", source)
        return None


def compute_ear_threshold(
    samples: list[float],
    k: float = 2.5,
    minimum: float = 0.15,
    maximum: float = 0.30,
    drop_blinks: bool = True,
) -> CalibrationProfile:
    """Derive a personal EAR threshold from open-eye samples.

    Raises:
        ValueError: when there are too few usable samples to be meaningful.
    """
    usable = [value for value in samples if value > 0]
    if len(usable) < MIN_CALIBRATION_SAMPLES:
        raise ValueError(
            f"Need at least {MIN_CALIBRATION_SAMPLES} samples, got {len(usable)}. "
            "Stay in frame and look at the screen for the full calibration period."
        )

    if drop_blinks:
        # Blinks are the lowest readings. Removing the bottom fifth keeps the
        # statistics describing the OPEN eye, which is what the threshold is
        # measured down from.
        ordered = sorted(usable)
        keep_from = int(len(ordered) * _BLINK_PERCENTILE)
        candidate = ordered[keep_from:]
        if len(candidate) >= MIN_CALIBRATION_SAMPLES:
            usable = candidate

    mean_open = statistics.fmean(usable)
    std_open = statistics.pstdev(usable) if len(usable) > 1 else 0.0

    raw_threshold = mean_open - (k * std_open)
    threshold = min(max(raw_threshold, minimum), maximum)
    clamped = threshold != raw_threshold

    if clamped:
        logger.warning(
            "Calibrated EAR threshold %.3f was clamped to %.3f "
            "(open mean %.3f, std %.3f) - consider recalibrating",
            raw_threshold,
            threshold,
            mean_open,
            std_open,
        )

    return CalibrationProfile(
        ear_threshold=round(threshold, 4),
        ear_mean_open=round(mean_open, 4),
        ear_std_open=round(std_open, 4),
        samples_used=len(usable),
        clamped=clamped,
    )


class EarCalibrator:
    """Collects EAR samples during a calibration session."""

    def __init__(self, k: float = 2.5, minimum: float = 0.15, maximum: float = 0.30) -> None:
        self._samples: list[float] = []
        self._k = k
        self._minimum = minimum
        self._maximum = maximum

    def add(self, ear: float) -> None:
        """Record one sample. Invalid readings are ignored, not stored as zero."""
        if ear > 0:
            self._samples.append(ear)

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    @property
    def has_enough(self) -> bool:
        return len(self._samples) >= MIN_CALIBRATION_SAMPLES

    def result(self) -> CalibrationProfile:
        return compute_ear_threshold(
            self._samples,
            k=self._k,
            minimum=self._minimum,
            maximum=self._maximum,
        )

    def reset(self) -> None:
        self._samples.clear()
