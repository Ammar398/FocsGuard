"""Print a session report from stored data (spec sections 42, 43).

    python -m focusguard_agent.tools.report            # the most recent session
    python -m focusguard_agent.tools.report --list     # list sessions
    python -m focusguard_agent.tools.report --session 3
    python -m focusguard_agent.tools.report --json     # machine-readable

Works after the fact, so a session whose agent was killed - or one from last week -
still produces its full report. Every event was written to the database as it
happened; only the printout was ever lost.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from focusguard_agent.local_store.database import LocalStore
from focusguard_agent.reporting.session_report import SessionReport, build_session_report
from focusguard_shared.constants import Classification
from focusguard_shared.utilities import configure_logging

_MARKS = {
    Classification.PRODUCTIVE.value: "+",
    Classification.NEUTRAL.value: "=",
    Classification.DISTRACTING.value: "-",
}


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _bar(fraction: float, width: int = 24) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "#" * filled + "." * (width - filled)


def list_sessions(store: LocalStore, limit: int = 20) -> int:
    sessions = store.recent_sessions(limit)
    if not sessions:
        print("No sessions recorded yet. Run:")
        print("  python -m focusguard_agent.agent --name \"Study session\"")
        return 1

    print(f"{'id':>4}  {'started':<17} {'duration':>9}  {'focus':>6}  name")
    print("-" * 72)

    for row in sessions:
        report = build_session_report(store, row["id"])
        if report is None:
            continue
        started = (report.started_at.strftime("%Y-%m-%d %H:%M") if report.started_at else "-")
        focus = f"{report.average_focus_score:.0f}" if report.average_focus_score else "-"
        running = "" if report.completed else "  (unfinished)"
        print(
            f"{row['id']:>4}  {started:<17} {_duration(report.duration_seconds):>9}  "
            f"{focus:>6}  {report.name}{running}"
        )
    return 0


def print_report(report: SessionReport) -> None:
    print("=" * 64)
    print(f"SESSION REPORT  -  {report.name}")
    print("=" * 64)

    if report.started_at:
        print(f"  Date      : {report.started_at.strftime('%A %d %B %Y, %H:%M')}")
    if report.task_name:
        print(f"  Task      : {report.task_name} ({report.task_category})")
    if not report.completed:
        print("  Status    : unfinished (the agent did not shut down cleanly)")

    print(f"  Duration  : {_duration(report.duration_seconds)}")

    if report.scored_seconds <= 0:
        print("\n  No score samples recorded - the camera was unavailable throughout.")
    else:
        print("\n" + "-" * 64)
        print("ATTENTION")
        print("-" * 64)
        print(f"  Focused     {_bar(report.focused_ratio)}  "
              f"{_duration(report.focused_seconds)} ({report.focused_ratio * 100:.0f}%)")
        print(f"  Distracted  {_bar(1 - report.focused_ratio)}  "
              f"{_duration(report.distracted_seconds)}")

        if report.unscored_seconds > 5:
            # Stated plainly rather than hidden: this time was not measured, and
            # was not charged to the user either.
            print(f"  Not scored  {'':24}  {_duration(report.unscored_seconds)} "
                  "(camera unavailable)")

        print(f"\n  Average focus score : {report.average_focus_score:.1f}")
        print(f"  Range               : {report.lowest_focus_score:.0f} "
              f"to {report.highest_focus_score:.0f}")
        print(f"  Longest focus block : {_duration(report.longest_focus_block_seconds)}")
        print(f"  Visual attention    : {report.average_visual_attention:.1f}")
        if report.average_productivity is not None:
            print(f"  Digital productivity: {report.average_productivity:.1f}")

    if report.events:
        print("\n" + "-" * 64)
        print("WHAT INTERRUPTED YOU")
        print("-" * 64)
        print(f"  {'event':<18} {'total':>9} {'times':>6} {'longest':>9}  confidence")
        for event in report.events:
            print(
                f"  {event.event_type:<18} {_duration(event.seconds):>9} "
                f"{event.count:>6} {_duration(event.longest_seconds):>9}  "
                f"{event.mean_confidence:.2f}"
            )

    if report.applications:
        print("\n" + "-" * 64)
        print("WHERE THE TIME WENT")
        print("-" * 64)
        for app in report.top_applications(8):
            mark = _MARKS.get(app.classification, "?")
            label = app.name + ("  (site)" if app.is_domain else "")
            print(f"  {mark} {label:<32} {_duration(app.seconds):>9}  {app.category}")

        if report.classification_seconds:
            total = sum(report.classification_seconds.values())
            print()
            for classification in (
                Classification.PRODUCTIVE.value,
                Classification.NEUTRAL.value,
                Classification.DISTRACTING.value,
            ):
                seconds = report.classification_seconds.get(classification, 0.0)
                if seconds > 0:
                    share = seconds / total if total else 0
                    print(f"  {classification:<13} {_bar(share)}  "
                          f"{_duration(seconds)} ({share * 100:.0f}%)")

    print("\n" + "-" * 64)
    print("AT A GLANCE")
    print("-" * 64)
    phone = report.event("PHONE_VISIBLE")
    drowsy = report.event("DROWSINESS")
    absent = report.event("FACE_ABSENT")

    print(f"  Phone appearances   : {phone.count if phone else 0}")
    print(f"  Drowsiness episodes : {drowsy.count if drowsy else 0}")
    print(f"  Times away          : {absent.count if absent else 0}")

    worst = report.most_distracting()
    if worst:
        print(f"  Biggest distraction : {worst.name} ({_duration(worst.seconds)})")

    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="FocusGuard session report")
    parser.add_argument("--session", type=int, help="session id (default: most recent)")
    parser.add_argument("--list", action="store_true", help="list recorded sessions")
    parser.add_argument("--json", action="store_true", help="print as JSON")
    parser.add_argument("--limit", type=int, default=20, help="how many to list")
    args = parser.parse_args()

    configure_logging(level="WARNING")
    store = LocalStore()

    try:
        if args.list:
            return list_sessions(store, args.limit)

        session_id = args.session
        if session_id is None:
            sessions = store.recent_sessions(1)
            if not sessions:
                print("No sessions recorded yet. Run:")
                print('  python -m focusguard_agent.agent --name "Study session"')
                return 1
            session_id = sessions[0]["id"]

        report = build_session_report(store, session_id)
        if report is None:
            print(f"No session with id {session_id}. Try --list.")
            return 1

        if args.json:
            print(json.dumps(asdict(report), indent=2, default=str))
        else:
            print_report(report)
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
