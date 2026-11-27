"""The FocusGuard installer for Windows (the "Download FocusGuard" button).

    GET /api/v1/downloads/FocusGuard-Setup.zip

WHAT IS IN IT
-------------
The desktop agent and Helper source, their requirements, the phone-detection
model, an installer script, and **the address of this server** - read from the
request, so a computer set up from the deployed site uploads to that site and
its Helper trusts that site's portal, with nothing to type.

    FocusGuard/
        Install FocusGuard.cmd     double-click this
        install.ps1                what it runs (readable before running)
        server.txt                 this server's address
        shared/  desktop_agent/  requirements/
        models/yolov8n.onnx

WHY NO SIGN-IN IS NEEDED
------------------------
Nothing in the archive is secret: the code is the same for everybody and the
server address is public. Linking the computer to an account still happens
afterwards, with the "Connect this computer" button and a Windows dialog. A
plain link (no token) is also what lets the browser download it normally.

The archive is built once per server address and kept in memory (~12 MB, almost
all of it the model).
"""

from __future__ import annotations

import io
import os
import zipfile
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response

from focusguard_shared.utilities import get_logger

logger = get_logger("backend.api.downloads")

router = APIRouter(prefix="/downloads", tags=["downloads"])

ARCHIVE_NAME = "FocusGuard-Setup.zip"
TOP = "FocusGuard"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_INSTALLER = Path(__file__).resolve().parents[2] / "installer"

#: Copied as they are. Anything else in the repository (backend, tests, local
#: databases, .env) is never part of the download.
_TREES = ("shared/focusguard_shared", "desktop_agent/focusguard_agent")
_FILES = ("requirements/base.txt", "requirements/agent.txt")
_MODEL = "desktop_agent/models/yolov8n.onnx"
#: The browser extension, unpacked: the person loads this folder in Chrome/Edge.
_EXTENSION = "browser_extension/src"
_SKIP_PARTS = {"__pycache__", ".pytest_cache"}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".log"}


def source_root() -> Path:
    return Path(os.environ.get("FOCUSGUARD_AGENT_SOURCE", _REPO_ROOT)).resolve()


def public_url(request: Request) -> str:
    """This server's address as the visitor reached it.

    `FOCUSGUARD_PUBLIC_URL` wins when set, for hosts whose proxy does not pass
    the original scheme and host through.
    """
    configured = os.environ.get("FOCUSGUARD_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _included(path: Path) -> bool:
    return not (_SKIP_PARTS & set(path.parts)) and path.suffix not in _SKIP_SUFFIXES


@lru_cache(maxsize=4)
def build_archive(server_url: str, root: Path) -> bytes:
    missing = [p for p in (*_TREES, *_FILES, _MODEL, _EXTENSION) if not (root / p).exists()]
    if missing:
        raise FileNotFoundError(", ".join(missing))

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for tree in _TREES:
            for path in sorted((root / tree).rglob("*")):
                if path.is_file() and _included(path.relative_to(root)):
                    archive.write(path, f"{TOP}/{path.relative_to(root).as_posix()}")
        for name in _FILES:
            archive.write(root / name, f"{TOP}/{name}")
        for path in sorted((root / _EXTENSION).iterdir()):
            if path.is_file():
                archive.write(path, f"{TOP}/extension/{path.name}")
        # Already compressed weights: storing them saves time and nothing else.
        archive.write(root / _MODEL, f"{TOP}/models/yolov8n.onnx",
                      compress_type=zipfile.ZIP_STORED)
        for script in ("Install FocusGuard.cmd", "install.ps1", "README.txt"):
            # CRLF: these are opened by cmd.exe, PowerShell and Notepad.
            text = (_INSTALLER / script).read_text(encoding="utf-8").replace("\r\n", "\n")
            archive.writestr(f"{TOP}/{script}", text.replace("\n", "\r\n"))
        archive.writestr(f"{TOP}/server.txt", server_url + "\r\n")
    return buffer.getvalue()


@router.get(f"/{ARCHIVE_NAME}")
def download_agent(request: Request) -> Response:
    server = public_url(request)
    try:
        data = build_archive(server, source_root())
    except FileNotFoundError as error:
        logger.error("Installer files missing from this server: %s", error)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The FocusGuard installer is not available on this server.",
        ) from error
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ARCHIVE_NAME}"',
            "Cache-Control": "no-cache",
        },
    )
