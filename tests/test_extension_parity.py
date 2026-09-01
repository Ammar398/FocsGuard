"""The extension's URL minimisation must match Python's (spec sections 20, 35).

Two implementations of one rule is exactly what silently drifts apart, and a
drift here would leak search terms. Both exist for a good reason: minimisation
has to happen inside the browser so the query string never crosses a process
boundary, while the agent needs the same logic to validate what arrives.

This runs the JavaScript against the same cases as `test_urls.py`. It is skipped
when node is unavailable rather than failing, since node is not a runtime
dependency of the agent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "browser_extension" / "test_minimise.mjs"


def _find_node() -> str | None:
    """Locate node, including installs that are not on PATH."""
    found = shutil.which("node")
    if found:
        return found

    candidates = [
        Path("E:/files/node.exe"),
        Path("C:/Program Files/nodejs/node.exe"),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs" / "node.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def test_javascript_minimisation_matches_python() -> None:
    node = _find_node()
    if node is None:
        pytest.skip("node is not available; extension parity not checked")

    result = subprocess.run(
        [node, str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, (
        "The extension's minimiseUrl() disagrees with the Python implementation:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "0 failure(s)" in result.stdout
