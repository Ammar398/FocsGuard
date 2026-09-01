"""Print the IPC pairing token for the browser extension.

    python -m focusguard_agent.tools.show_token

The token is what stops any web page on your machine from POSTing fabricated
browsing activity to the agent. Paste it into the FocusGuard extension popup
once; it is stored in the extension and not asked for again.
"""

from __future__ import annotations

from focusguard_agent.ipc.server import load_or_create_token, token_path


def main() -> int:
    token = load_or_create_token()

    print("FocusGuard pairing token")
    print("=" * 46)
    print(f"\n  {token}\n")
    print(f"Stored at: {token_path()}")
    print("\nPaste it into the FocusGuard extension popup, under 'Pairing token'.")
    print("Treat it like a password: anything holding it can post activity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
