"""Where FocusGuard keeps data that does not belong in the repository.

Model weights, logs and the local event queue all live under LOCALAPPDATA rather
than inside the project folder (spec section 45). Two reasons: model bundles must
never be committed to git, and the repository stays small enough to hand in.
"""

from __future__ import annotations

import os
from pathlib import Path

_APP_NAME = "FocusGuard"


def app_data_dir() -> Path:
    """Root directory for FocusGuard's local data.

    Uses ``%LOCALAPPDATA%`` on Windows and ``~/.local/share`` elsewhere, so the
    test suite still runs on a non-Windows CI machine.
    """
    override = os.environ.get("FOCUSGUARD_DATA_DIR")
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / ".local" / "share"
    return base / _APP_NAME


def model_cache_dir() -> Path:
    """Directory holding downloaded model bundles (face_landmarker.task, YOLO ONNX)."""
    return app_data_dir() / "models"


def log_dir() -> Path:
    """Directory holding rotating log files."""
    return app_data_dir() / "logs"


def ensure_dirs() -> None:
    """Create the standard data directories if they do not exist."""
    for directory in (app_data_dir(), model_cache_dir(), log_dir()):
        directory.mkdir(parents=True, exist_ok=True)
