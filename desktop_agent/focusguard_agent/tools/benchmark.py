"""Measure real capture and processing cost on this machine (spec section 44).

The frame-rate defaults in ``config.py`` were chosen from the specification, not
from measurement. This script replaces the estimates with numbers, on the actual
i5-6500 that has to run the finished system.

    python -m focusguard_agent.tools.benchmark
    python -m focusguard_agent.tools.benchmark --seconds 60

Each phase that adds a processing stage registers it here, so the CPU budget is
re-checked as the pipeline grows rather than only at the end, when a regression
would be expensive to unpick. Phase 2 measures capture alone: the baseline every
later stage is added on top of.

CPU is reported as a percentage of *one* core, matching Task Manager's per-process
figure. With 4 cores, 400% is full saturation - and spec section 44 requires we
stay well below it.
"""

from __future__ import annotations

import argparse
import platform
import time
from dataclasses import dataclass, field

import psutil

from focusguard_agent.capture.camera import CameraSource
from focusguard_agent.config import get_settings
from focusguard_agent.hardware.detector import detect_hardware
from focusguard_shared.utilities import configure_logging


@dataclass
class StageTimer:
    """Collects per-call durations for one pipeline stage."""

    name: str
    samples: list[float] = field(default_factory=list)

    def record(self, seconds: float) -> None:
        self.samples.append(seconds * 1000.0)  # store as milliseconds

    @property
    def calls(self) -> int:
        return len(self.samples)

    @property
    def mean_ms(self) -> float:
        return sum(self.samples) / len(self.samples) if self.samples else 0.0

    @property
    def p95_ms(self) -> float:
        """95th percentile: the occasional slow frame matters more than the mean.

        A stage that averages 12 ms but spikes to 60 ms will visibly stutter, and
        the mean alone would hide that.
        """
        if not self.samples:
            return 0.0
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return ordered[index]

    @property
    def max_ms(self) -> float:
        return max(self.samples) if self.samples else 0.0


def _print_header() -> None:
    report = detect_hardware()
    memory = psutil.virtual_memory()

    print("=" * 72)
    print("FocusGuard Benchmark")
    print("=" * 72)
    print(f"  CPU       : {report.cpu}")
    print(f"  RAM       : {report.total_ram_gb:g} GB ({memory.percent:.0f}% in use)")
    gpu = report.primary_gpu
    print(f"  GPU       : {gpu if gpu else 'none detected'}")
    print(f"  Provider  : {report.selected_provider.value}")
    print(f"  Python    : {platform.python_version()}")
    print("=" * 72)


def run_capture_benchmark(seconds: float) -> int:
    settings = get_settings()

    _print_header()
    print(f"\nMeasuring capture for {seconds:.0f}s ...\n")

    process = psutil.Process()
    stages = {"capture_read": StageTimer("capture_read")}

    with CameraSource(settings.camera) as camera:
        deadline = time.monotonic() + settings.camera.open_timeout_seconds
        while camera.read() is None and time.monotonic() < deadline:
            time.sleep(0.05)

        first_frame = camera.read()
        if first_frame is None:
            print("No frames received - is the camera running?")
            print("Try: python -m focusguard_agent.tools.camera_test --list")
            return 1

        processed_size = first_frame.size
        camera_size = (camera.stats.actual_width, camera.stats.actual_height)

        # Let the driver settle before measuring. The first second after a camera
        # opens is dominated by negotiation and buffer allocation, and including
        # it makes the CPU figure look far worse than the steady state.
        time.sleep(1.0)

        # Prime the CPU counter; the first call always returns 0.0.
        process.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None)

        start = time.monotonic()
        end = start + seconds
        consumed = 0
        cpu_samples: list[float] = []
        next_cpu_sample = start + 1.0

        while time.monotonic() < end:
            read_started = time.perf_counter()
            frame = camera.read_new()
            stages["capture_read"].record(time.perf_counter() - read_started)

            if frame is not None:
                consumed += 1
            else:
                # Nothing new yet. Sleeping briefly keeps this loop from becoming
                # a busy-wait that would distort the very CPU figure we measure.
                time.sleep(0.002)

            now = time.monotonic()
            if now >= next_cpu_sample:
                cpu_samples.append(process.cpu_percent(interval=None))
                next_cpu_sample = now + 1.0

        elapsed = time.monotonic() - start
        stats = camera.stats
        memory_mb = process.memory_info().rss / (1024**2)

    mean_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0
    peak_cpu = max(cpu_samples) if cpu_samples else 0.0
    cores = psutil.cpu_count(logical=True) or 1

    print("-" * 72)
    print("CAPTURE")
    print(f"  Camera resolution   : {stats.actual_width}x{stats.actual_height}")
    print(f"  Processed resolution: {processed_size[0]}x{processed_size[1]}"
          f"{'  (downscaled in software)' if processed_size != camera_size else ''}")
    print(f"  Frames captured     : {stats.frames_captured}")
    print(f"  Capture rate        : {stats.frames_captured / elapsed:.1f} FPS")
    print(f"  Frames consumed     : {consumed} ({consumed / elapsed:.1f} FPS)")
    print(f"  Frames dropped      : {stats.frames_dropped} ({stats.drop_rate * 100:.0f}%)")
    print(f"  Reconnects          : {max(0, stats.reconnect_attempts - 1)}")

    print("\nSTAGE TIMINGS (milliseconds)")
    print(f"  {'stage':<20} {'calls':>8} {'mean':>8} {'p95':>8} {'max':>8}")
    for timer in stages.values():
        print(
            f"  {timer.name:<20} {timer.calls:>8} "
            f"{timer.mean_ms:>8.2f} {timer.p95_ms:>8.2f} {timer.max_ms:>8.2f}"
        )

    print("\nRESOURCE USE")
    print(f"  Process CPU mean    : {mean_cpu:.0f}% of one core "
          f"({mean_cpu / cores:.0f}% of {cores} cores)")
    print(f"  Process CPU peak    : {peak_cpu:.0f}% of one core")
    print(f"  Process memory      : {memory_mb:.0f} MB")

    budget = 100.0 * cores * 0.60  # section 44: never sit at full CPU
    print("\nVERDICT")
    if mean_cpu < budget:
        print(f"  Within budget: {mean_cpu:.0f}% used of {budget:.0f}% "
              f"(60% of {cores} cores) available for the whole pipeline.")
    else:
        print(f"  OVER BUDGET: {mean_cpu:.0f}% exceeds the {budget:.0f}% target. "
              "Reduce capture resolution or frame rate.")
    print("-" * 72)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard performance benchmark")
    parser.add_argument("--seconds", type=float, default=20.0, help="measurement window")
    args = parser.parse_args()

    configure_logging(level="WARNING")
    return run_capture_benchmark(args.seconds)


if __name__ == "__main__":
    raise SystemExit(main())
