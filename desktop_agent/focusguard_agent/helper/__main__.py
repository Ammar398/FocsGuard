"""Run the FocusGuard Helper.

    python -m focusguard_agent.helper                    # run it now (Ctrl+C to quit)
    python -m focusguard_agent.helper --install-startup  # also start it at every Windows sign-in
    python -m focusguard_agent.helper --remove-startup
    python -m focusguard_agent.helper --server https://your-focusguard.onrender.com
                                         # which FocusGuard server this computer uses

The startup entry is a one-line script in your own Startup folder - visible in
Task Manager -> Startup apps, and removable there or with --remove-startup. It
starts only the helper, which waits quietly and opens the camera never; a
monitoring session still needs your "Yes" each time.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from focusguard_agent.helper.server import FocusGuardHelper, HelperServer
from focusguard_shared.utilities import configure_logging

STARTUP_FILE = "FocusGuard Helper.cmd"


def startup_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise SystemExit("APPDATA is not set; the Startup folder exists only on Windows.")
    startup = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / STARTUP_FILE


def install_startup() -> Path:
    # pythonw: no console window for the helper itself. The agent it launches
    # still opens its own visible window whenever monitoring runs.
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    path = startup_path()
    path.write_text(
        "@echo off\r\n"
        "rem FocusGuard Helper - lets the FocusGuard portal connect this computer and\r\n"
        "rem start sessions after you approve them. Remove this file to disable.\r\n"
        f'cd /d "{Path.cwd()}"\r\n'
        f'start "" "{interpreter}" -m focusguard_agent.helper\r\n',
        encoding="utf-8",
        # The text already holds CRLF; newline="" stops Windows doubling it.
        newline="",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FocusGuard Helper")
    parser.add_argument("--install-startup", action="store_true")
    parser.add_argument("--remove-startup", action="store_true")
    parser.add_argument("--server", metavar="URL",
                        help="the address of your FocusGuard website, e.g. https://x.onrender.com")
    parser.add_argument("--setup-only", action="store_true",
                        help="apply --server/--install-startup and exit without running")
    args = parser.parse_args(argv)

    if args.server:
        from focusguard_agent.config import get_settings, normalise_server_url, save_server_url

        try:
            normalise_server_url(args.server)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        saved = save_server_url(args.server)
        url = get_settings(reload=True).sync.backend_url
        print(f"This computer now uses the FocusGuard server at {url} (saved in {saved}).")
        print("If the Helper is already running, restart it (or sign out of Windows and back in).")

    if args.remove_startup:
        startup_path().unlink(missing_ok=True)
        print("The helper will no longer start with Windows.")
        return 0

    if args.install_startup:
        print(f"The helper will start when you sign in to Windows ({install_startup()}).")

    if args.setup_only:
        return 0

    configure_logging(level="INFO")
    helper = FocusGuardHelper()
    server = HelperServer(helper)
    if not server.bind():
        print(f"The FocusGuard Helper is already running (port {server.port}).")
        return 0

    helper.tidy_local_store()
    helper.start_scheduler()
    print(f"FocusGuard Helper ready on http://127.0.0.1:{server.port}")
    print("Open the FocusGuard portal and click 'Connect this computer'.")
    print("Anything that connects this computer or turns the webcam on asks you first.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        helper.stop_scheduler()
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
