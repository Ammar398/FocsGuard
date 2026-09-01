"""Package the browser extension for Chrome and Edge.

    python browser_extension/build.py

WHY ONE CODEBASE, NOT TWO
-------------------------
The original architecture called for separate `chrome/` and `edge/` folders.
Microsoft Edge is Chromium-based and loads the *same* Manifest V3 extension with
the same APIs, so two folders would mean applying every fix twice and watching
them drift - a bug source, and the "copy-pasted logic" section 56 rules out.

One source tree, one build script, two output folders. If a genuine
Edge-specific need appears later it becomes a documented override file here,
rather than a forked codebase.

LOADING IT
----------
Chrome:  chrome://extensions  -> Developer mode -> Load unpacked -> dist/chrome
Edge:    edge://extensions    -> Developer mode -> Load unpacked -> dist/edge

Then open the FocusGuard popup and paste the pairing token from:
    python -m focusguard_agent.tools.show_token
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "src"
DIST = ROOT / "dist"

TARGETS = ("chrome", "edge")


def _make_icon(path: Path) -> None:
    """Generate a placeholder icon if none has been supplied."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return

    size = 128
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=(37, 99, 235, 255))
    draw.ellipse((34, 34, size - 34, size - 34), fill=(255, 255, 255, 255))
    draw.ellipse((52, 52, size - 52, size - 52), fill=(37, 99, 235, 255))
    image.save(path)


def build(target: str, port: int) -> Path:
    output = DIST / target
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for item in SOURCE.iterdir():
        if item.is_file():
            shutil.copy2(item, output / item.name)

    icon = output / "icon128.png"
    if not icon.exists():
        _make_icon(icon)

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # The icon is optional during development; drop the reference rather than
    # letting the browser refuse to load the whole extension over it.
    if not icon.exists():
        manifest.pop("icons", None)

    if port != 48213:
        manifest["host_permissions"] = [f"http://127.0.0.1:{port}/*"]
        for name in ("background.js", "popup.js"):
            script = output / name
            script.write_text(
                script.read_text(encoding="utf-8").replace("48213", str(port)),
                encoding="utf-8",
            )

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the FocusGuard browser extension")
    parser.add_argument("--port", type=int, default=48213, help="agent IPC port")
    args = parser.parse_args()

    if not SOURCE.exists():
        print(f"Source directory missing: {SOURCE}", file=sys.stderr)
        return 1

    print("FocusGuard extension build")
    print("=" * 52)
    for target in TARGETS:
        output = build(target, args.port)
        print(f"  {target:<8} -> {output}")

    print("\nLoad it:")
    print("  Chrome:  chrome://extensions -> Developer mode -> Load unpacked")
    print("  Edge:    edge://extensions   -> Developer mode -> Load unpacked")
    print("\nThen paste the pairing token from:")
    print("  python -m focusguard_agent.tools.show_token")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
