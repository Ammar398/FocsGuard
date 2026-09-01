"""Live foreground-application monitor - the Phase 9 acceptance demo.

    python -m focusguard_agent.tools.activity_test
    python -m focusguard_agent.tools.activity_test --seconds 120
    python -m focusguard_agent.tools.activity_test --titles   # opt in to titles

Run it, then switch between applications for a minute. Each completed span prints
as it closes, and a summary follows at the end.

WHAT THIS COLLECTS
    application name, category, duration, and whether you were idle.

WHAT IT CANNOT COLLECT
    window contents, keystrokes, clipboard, screenshots. The Windows calls used
    return none of those, and tests/test_privacy_imports.py fails the build if a
    library capable of them is ever imported into the agent.

Window titles are OFF unless you pass --titles: a title can leak a document name
where an application name cannot.

Press Ctrl+C to stop early.
"""

from __future__ import annotations

import argparse
import time

from focusguard_agent.activity_monitor.aggregator import ActivityAggregator, ActivitySpan
from focusguard_agent.activity_monitor.classifier import (
    ActivityClassifier,
    ActivityNormalizer,
)
from focusguard_agent.activity_monitor.collector import WindowsActivityCollector
from focusguard_agent.config import get_settings
from focusguard_shared.constants import Classification
from focusguard_shared.utilities import configure_logging

_MARKS = {
    Classification.PRODUCTIVE: "+",
    Classification.NEUTRAL: "=",
    Classification.DISTRACTING: "-",
}


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}m {int(seconds) % 60:02d}s"


def _report(span: ActivitySpan) -> None:
    mark = _MARKS.get(span.classification, "?")
    label = span.domain or span.application_name
    print(
        f"  {mark} {label:<28} {_mmss(span.duration_seconds):>8}  "
        f"{span.category:<18} {span.classification}"
    )


def run(seconds: float, collect_titles: bool) -> int:
    settings = get_settings()

    collector = WindowsActivityCollector(collect_window_titles=collect_titles)
    if not collector.available:
        print("Foreground monitoring is only available on Windows.")
        return 1

    normalizer = ActivityNormalizer()
    classifier = ActivityClassifier()
    aggregator = ActivityAggregator(min_span_seconds=settings.activity.min_span_seconds)

    print("=" * 66)
    print("FocusGuard - foreground application monitor")
    print("=" * 66)
    print(f"  Watching for {seconds:.0f}s. Switch between applications to see spans.")
    print(f"  Window titles : {'ON (opt-in)' if collect_titles else 'OFF'}")
    print(f"  Idle after    : {settings.activity.idle_threshold_seconds:.0f}s")
    print(f"  Ignoring spans under {settings.activity.min_span_seconds:.0f}s")
    print("=" * 66)
    print("  + productive    = neutral    - distracting\n")

    start = time.monotonic()
    end = start + seconds
    was_idle = False

    try:
        while time.monotonic() < end:
            now = time.monotonic()
            idle = collector.is_idle(settings.activity.idle_threshold_seconds)

            if idle != was_idle:
                print(f"  [{'idle' if idle else 'active again'}]")
                was_idle = idle

            window = collector.foreground()
            activity = None
            process_name = ""

            # A shell surface holding the foreground mid-switch is not usage.
            if window is not None and not window.is_transient:
                process_name = window.process_name
                activity = classifier.classify(normalizer.normalise(window.process_name))

            span = aggregator.update(
                now,
                activity,
                process_name=process_name,
                window_title=window.window_title if window else None,
                idle=idle,
            )
            if span is not None:
                _report(span)

            time.sleep(settings.activity.poll_interval_seconds)
    except KeyboardInterrupt:
        print("\n  stopped")

    final = aggregator.flush(time.monotonic())
    if final is not None:
        _report(final)

    totals = aggregator.totals
    print("\n" + "=" * 66)
    print("ACTIVITY SUMMARY")
    print("=" * 66)

    if not totals.by_application:
        print("  No spans recorded. Try switching applications and staying in one")
        print(f"  for more than {settings.activity.min_span_seconds:.0f} seconds.")
        return 0

    print(f"  Tracked time  : {_mmss(totals.total_seconds)}")
    if totals.idle_seconds > 1:
        print(f"  Idle          : {_mmss(totals.idle_seconds)} (not attributed)")
    if totals.discarded_spans:
        print(f"  Ignored       : {totals.discarded_spans} spans too short to count")

    print("\n  Most used:")
    for name, span_seconds in totals.most_used():
        share = span_seconds / totals.total_seconds * 100
        print(f"    {name:<30} {_mmss(span_seconds):>8}  {share:4.0f}%")

    print("\n  By classification:")
    for classification in Classification:
        value = totals.seconds_for(classification)
        if value > 0:
            share = value / totals.total_seconds * 100
            print(f"    {classification:<14} {_mmss(value):>8}  {share:4.0f}%")

    print("\n  By category:")
    for category, value in sorted(totals.by_category.items(), key=lambda item: -item[1]):
        print(f"    {category:<20} {_mmss(value):>8}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard foreground activity monitor")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to watch")
    parser.add_argument(
        "--titles", action="store_true", help="also collect window titles (opt-in)"
    )
    args = parser.parse_args()

    configure_logging(level="WARNING")
    return run(args.seconds, args.titles)


if __name__ == "__main__":
    raise SystemExit(main())
