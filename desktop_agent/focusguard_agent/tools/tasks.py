"""List the tasks your manager has assigned to you (spec sections 28, 29).

    python -m focusguard_agent.tools.tasks

Then start working on one:

    python -m focusguard_agent.agent --assigned-task 12

WHAT THIS READS
---------------
Only the open tasks assigned to the person this machine is enrolled for, with
their productive / neutral / distracting rules. The device credential cannot
read anything else - not a colleague's tasks, not any session or score.

The rules are shown deliberately: an employee should be able to see exactly what
will count as productive or distracting before they start, rather than finding
out from a report afterwards (section 62).
"""

from __future__ import annotations

import sys

from focusguard_agent.synchronization.client import BackendClient, TasksUnavailable
from focusguard_agent.synchronization.credentials import NotEnrolled, load_credential
from focusguard_shared.utilities import configure_logging


def _rules(label: str, values: list[str]) -> str | None:
    return f"      {label:<12}: {', '.join(values)}" if values else None


def main(argv: list[str] | None = None) -> int:
    configure_logging(level="WARNING")

    try:
        credential = load_credential()
    except NotEnrolled:
        print("This machine is not enrolled. Ask your manager for an enrolment token, then:")
        print("    python -m focusguard_agent.tools.enroll --token <token>")
        return 1

    try:
        with BackendClient(credential.backend_url, credential=credential) as client:
            tasks = client.assigned_tasks()
    except TasksUnavailable as error:
        print(f"Could not fetch your tasks: {error}", file=sys.stderr)
        return 1

    if not tasks:
        print("You have no open assigned tasks.")
        if credential.organization_id is None:
            print("(This machine is enrolled as a personal/student device.)")
        return 0

    print(f"\nYour open tasks ({len(tasks)}):\n")
    for task in tasks:
        project = f"  [{task['project_name']}]" if task.get("project_name") else ""
        print(f"  #{task['id']:<5} {task['title']}{project}")
        details = [f"{task['category']}", task["status"].replace("_", " ").lower()]
        if task.get("expected_minutes"):
            details.append(f"~{task['expected_minutes']} min")
        if task.get("deadline"):
            details.append(f"due {task['deadline'][:16].replace('T', ' ')} UTC")
        print(f"         {' · '.join(details)}")
        for line in (
            _rules("productive", task.get("productive_rules") or []),
            _rules("neutral", task.get("neutral_rules") or []),
            _rules("distracting", task.get("distracting_rules") or []),
        ):
            if line:
                print(line)
        print()

    print("Start one with:")
    print(f"    python -m focusguard_agent.agent --assigned-task {tasks[0]['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
