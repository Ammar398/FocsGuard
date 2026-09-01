"""Stop a running FocusGuard agent gracefully.

    python -m focusguard_agent.tools.stop
    python -m focusguard_agent.tools.stop --status

WHY NOT JUST KILL THE PROCESS
-----------------------------
`Stop-Process` or Task Manager ends the agent immediately. The data already
written is safe - everything is stored as it happens - but two things are lost:

* any event still in progress, such as a phone appearance that had not ended;
* the closing summary.

This asks the agent to stop *itself*, so it closes open events, writes them, and
prints its report.

HOW IT WORKS
------------
A flag file, not a signal. Sending Ctrl+C to another process on Windows is
awkward and unreliable - it involves console process groups and can hit the
sender instead. The agent already loops many times a second, so it simply checks
for a file, and deletes it on the way out.

Simple, reliable, and it works no matter how the agent was launched: a terminal,
a scheduled task, or a shortcut with no console at all.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from focusguard_shared.utilities.paths import app_data_dir

STOP_FLAG_NAME = "stop.flag"
PID_FILE_NAME = "agent.pid"


def stop_flag_path() -> Path:
    return app_data_dir() / STOP_FLAG_NAME


def pid_file_path() -> Path:
    return app_data_dir() / PID_FILE_NAME


def write_pid_file(session_name: str) -> None:
    """Record that an agent is running, for `--status` and for stop to confirm."""
    path = pid_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n{session_name}", encoding="utf-8")


def clear_pid_file() -> None:
    pid_file_path().unlink(missing_ok=True)


def read_pid_file() -> tuple[int, str] | None:
    path = pid_file_path()
    if not path.exists():
        return None

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return int(lines[0]), (lines[1] if len(lines) > 1 else "")
    except (OSError, ValueError, IndexError):
        return None


def is_running(pid: int) -> bool:
    """Whether that process still exists.

    A stale pid file is normal after a hard kill, so this is checked rather than
    trusted - otherwise `stop` would wait on an agent that died days ago.
    """
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        return True


def request_stop() -> None:
    path = stop_flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def clear_stop_flag() -> None:
    stop_flag_path().unlink(missing_ok=True)


def stop_requested() -> bool:
    return stop_flag_path().exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop the FocusGuard agent gracefully")
    parser.add_argument("--status", action="store_true", help="report whether one is running")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds to wait")
    args = parser.parse_args()

    running = read_pid_file()

    if args.status:
        if running and is_running(running[0]):
            print(f"Agent running: pid {running[0]}, session {running[1]!r}")
            return 0
        print("No agent is running.")
        if running:
            print("(a stale pid file was left by a hard kill; it will be replaced)")
        return 1

    if running is None or not is_running(running[0]):
        print("No agent appears to be running.")
        print("If one is, it predates this feature - use Ctrl+C in its window.")
        clear_pid_file()
        return 1

    pid, session_name = running
    print(f"Asking the agent to stop (pid {pid}, session {session_name!r}) ...")
    request_stop()

    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if not is_running(pid):
            print("Stopped cleanly. Its summary was printed in its own window.")
            print("\nTo see the report again:")
            print("  python -m focusguard_agent.tools.report")
            return 0
        time.sleep(0.25)

    print(f"Still running after {args.timeout:.0f}s.")
    print("It may be mid-detection. Wait a moment, or press Ctrl+C in its window.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
