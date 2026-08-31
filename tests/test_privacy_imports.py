"""Structural privacy guarantee (spec sections 34, 55 Rule 15).

FocusGuard promises it does not keylog, read the clipboard, capture the screen or
record audio. A promise in a README is worth very little; this test makes it a
property of the build.

It parses every source file in the desktop agent and fails if any of them imports
a library capable of those things. The point is that the capability is absent
from the process entirely - there is no feature flag that could switch it on,
because there is no code that could perform it.

If a future phase genuinely needs one of these libraries, this test is where that
decision has to be argued and documented, rather than slipped in unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1] / "desktop_agent"

#: Top-level module names the agent must never import, and why.
FORBIDDEN_IMPORTS: dict[str, str] = {
    "keyboard": "global keystroke capture",
    "pynput": "keyboard and mouse event capture",
    "pyautogui": "screenshots and synthetic input",
    "pyscreenshot": "screen capture",
    "mss": "fast screen capture",
    "d3dshot": "GPU screen capture",
    "pyperclip": "clipboard reading",
    "win32clipboard": "Windows clipboard reading",
    "sounddevice": "microphone capture",
    "pyaudio": "microphone capture",
    "soundcard": "microphone capture",
}

#: ``PIL.ImageGrab`` is a screen-capture entry point inside an otherwise
#: legitimate imaging library, so it is checked as a submodule.
FORBIDDEN_SUBMODULES: dict[str, str] = {
    "PIL.ImageGrab": "screen capture via Pillow",
}


def _iter_agent_sources() -> list[Path]:
    return sorted(AGENT_ROOT.rglob("*.py"))


def _imported_names(tree: ast.AST) -> set[str]:
    """Collect every module name imported by a parsed source file."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module)
                for alias in node.names:
                    names.add(f"{node.module}.{alias.name}")
    return names


def test_agent_sources_exist() -> None:
    """Guards against the audit silently passing because it found no files."""
    sources = _iter_agent_sources()
    assert sources, f"No agent sources found under {AGENT_ROOT}"


@pytest.mark.parametrize("source", _iter_agent_sources(), ids=lambda p: p.name)
def test_no_surveillance_imports(source: Path) -> None:
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    imported = _imported_names(tree)

    for name in sorted(imported):
        root = name.split(".")[0]
        if root in FORBIDDEN_IMPORTS:
            pytest.fail(
                f"{source.relative_to(AGENT_ROOT)} imports {name!r} "
                f"({FORBIDDEN_IMPORTS[root]}), which FocusGuard must never do."
            )
        if name in FORBIDDEN_SUBMODULES:
            pytest.fail(
                f"{source.relative_to(AGENT_ROOT)} imports {name!r} "
                f"({FORBIDDEN_SUBMODULES[name]}), which FocusGuard must never do."
            )
