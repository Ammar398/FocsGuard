"""Enrol this machine with a FocusGuard backend (spec section 25).

    python -m focusguard_agent.tools.enroll --token <enrolment token>
    python -m focusguard_agent.tools.enroll --status
    python -m focusguard_agent.tools.enroll --forget

WHAT ENROLMENT ACTUALLY DOES
----------------------------
It trades a short-lived token someone handed you for a lasting credential that
never leaves this machine. Afterwards the agent can upload; before it, the agent
still runs and still records everything locally - enrolment adds the backend, it
is not a prerequisite for monitoring.

That ordering is deliberate. A student should be able to use FocusGuard with no
server at all, and section 37 requires the agent to keep working offline.

WHAT THE USER IS AGREEING TO
----------------------------
The consent text below is printed before anything is sent, and enrolment stops if
it is declined. Section 25 requires the person being monitored to be told what is
collected; showing it at the moment they connect the machine to a server is the
point where that actually matters.

The list is not marketing copy - it is the literal contents of the upload
payload, and `--yes` skips the prompt but still prints it.
"""

from __future__ import annotations

import argparse
import platform
import sys

from focusguard_agent.config import get_settings
from focusguard_agent.synchronization.client import BackendClient, EnrollmentFailed
from focusguard_agent.synchronization.credentials import (
    NotEnrolled,
    forget_credential,
    load_credential,
    read_device_uuid,
    save_credential,
)
from focusguard_shared.utilities import configure_logging

#: Bumped whenever the text below changes, so the recorded consent says which
#: wording the person actually agreed to (spec sections 25, 62).
CONSENT_VERSION = "2026-11"

CONSENT = """
FocusGuard will upload the following to {backend}:

  * when your sessions start and end, and what you named them
  * attention events - face not visible, eyes closed, looking away, phone visible
  * which applications you used and for how long
  * website DOMAINS only, for example "youtube.com"
  * your focus scores over time

It will NOT upload:

  * webcam images or video of any kind
  * screenshots or the contents of any window
  * anything you type, your clipboard, or your passwords
  * full web addresses, search terms, or page contents

You can stop monitoring at any time, and you can disconnect this machine with:
    python -m focusguard_agent.tools.enroll --forget
"""


def _print_status() -> int:
    device_uuid = read_device_uuid()
    print(f"Device identifier : {device_uuid}")

    try:
        credential = load_credential()
    except NotEnrolled:
        print("Enrolment status  : NOT ENROLLED")
        print("\nThis machine records locally and uploads nothing.")
        print("To connect it to a backend, ask for an enrolment token and run:")
        print("    python -m focusguard_agent.tools.enroll --token <token>")
        return 1

    print(f"Enrolment status  : ENROLLED with {credential.backend_url}")
    print(f"Reports for user  : {credential.user_id}")
    if credential.organization_id:
        print(f"Organisation      : {credential.organization_id}")
    # The secret itself is never printed. A credential echoed to a terminal ends
    # up in scrollback, in a screenshot, and in a screen recording.
    print("\nThe device secret is stored locally and is never displayed.")
    return 0


def _forget() -> int:
    if forget_credential():
        print("This machine is no longer enrolled. It still records locally.")
        print("\nNote: the backend still holds the device. Ask a manager to revoke")
        print("it there as well if you want the credential invalidated server-side.")
        return 0

    print("This machine was not enrolled; nothing to remove.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enrol this machine with a backend")
    parser.add_argument("--token", help="the one-time enrolment token")
    parser.add_argument("--backend", help="backend URL (default: from settings)")
    parser.add_argument("--status", action="store_true", help="show enrolment state")
    parser.add_argument("--forget", action="store_true", help="remove the stored credential")
    parser.add_argument(
        "--yes", action="store_true", help="accept the consent notice without prompting"
    )
    arguments = parser.parse_args(argv)

    configure_logging(level="WARNING")

    if arguments.status:
        return _print_status()
    if arguments.forget:
        return _forget()

    if not arguments.token:
        parser.error("--token is required (or use --status / --forget)")

    settings = get_settings()
    backend = (arguments.backend or settings.sync.backend_url).rstrip("/")

    print(CONSENT.format(backend=backend))

    if not arguments.yes:
        answer = input("Do you agree to this? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("\nEnrolment cancelled. Nothing was sent.")
            return 1

    device_uuid = read_device_uuid()

    try:
        with BackendClient(backend) as client:
            credential = client.enroll(
                enrollment_token=arguments.token.strip(),
                device_uuid=device_uuid,
                platform=f"{platform.system()} {platform.release()}",
                agent_version=settings.agent_version,
                consent_version=CONSENT_VERSION,
            )
    except EnrollmentFailed as error:
        print(f"\nEnrolment failed: {error}", file=sys.stderr)
        print(
            "\nAn enrolment token works once and expires after an hour. "
            "Ask for a new one.",
            file=sys.stderr,
        )
        return 1

    save_credential(credential)

    print("\nEnrolled.")
    print(f"  backend : {credential.backend_url}")
    print(f"  device  : {credential.device_uuid}")
    print("\nThe agent will now upload as it runs. Start it with:")
    print('    python -m focusguard_agent.agent --name "Study session"')
    if credential.organization_id is not None:
        print("\nOr, to work on a task your manager assigned:")
        print("    python -m focusguard_agent.tools.tasks")
        print("    python -m focusguard_agent.agent --assigned-task <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
