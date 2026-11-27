"""The "Download FocusGuard for Windows" archive.

It must carry the server it was downloaded from (so setup needs no typing), the
agent and the phone model (so phone detection works), and nothing from the
server side: no backend code, no databases, no .env.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from focusguard_backend.api.v1 import downloads
from focusguard_backend.database.session import create_all, get_engine, reset_engine

URL = "/api/v1/downloads/FocusGuard-Setup.zip"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "download.db"))
    monkeypatch.delenv("FOCUSGUARD_PUBLIC_URL", raising=False)
    reset_engine()
    downloads.build_archive.cache_clear()
    from focusguard_backend.main import app

    create_all(get_engine())
    with TestClient(app, base_url="https://fg.example.com") as test_client:
        yield test_client
    downloads.build_archive.cache_clear()
    reset_engine()


def _open(response) -> zipfile.ZipFile:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "FocusGuard-Setup.zip" in response.headers["content-disposition"]
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_the_installer_carries_this_servers_address(client):
    archive = _open(client.get(URL))
    assert archive.read("FocusGuard/server.txt").decode().strip() == "https://fg.example.com"


def test_a_configured_public_address_wins(client, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_PUBLIC_URL", "https://focusguard.onrender.com/")
    archive = _open(client.get(URL))
    assert archive.read("FocusGuard/server.txt").decode().strip() == (
        "https://focusguard.onrender.com"
    )


def test_it_contains_everything_the_installer_needs(client):
    names = set(_open(client.get(URL)).namelist())
    for needed in (
        "FocusGuard/Install FocusGuard.cmd",
        "FocusGuard/install.ps1",
        "FocusGuard/README.txt",
        "FocusGuard/requirements/agent.txt",
        "FocusGuard/requirements/base.txt",
        "FocusGuard/models/yolov8n.onnx",
        "FocusGuard/desktop_agent/focusguard_agent/helper/__main__.py",
        "FocusGuard/desktop_agent/focusguard_agent/agent.py",
        "FocusGuard/shared/focusguard_shared/__init__.py",
    ):
        assert needed in names, needed


def test_nothing_from_the_server_side_is_included(client):
    for name in _open(client.get(URL)).namelist():
        assert "focusguard_backend" not in name
        assert "__pycache__" not in name
        assert not name.endswith((".db", ".env", ".pyc")), name


def test_the_windows_scripts_use_windows_line_endings(client):
    archive = _open(client.get(URL))
    for script in ("Install FocusGuard.cmd", "install.ps1"):
        text = archive.read(f"FocusGuard/{script}").decode()
        assert "\r\n" in text and "\n" not in text.replace("\r\n", "")
        # Windows PowerShell 5.1 reads a BOM-less script as the ANSI code page.
        assert text.isascii(), script


def test_a_server_without_the_agent_files_says_so(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FOCUSGUARD_AGENT_SOURCE", str(tmp_path / "nothing-here"))
    response = client.get(URL)
    assert response.status_code == 503
    assert "not available" in response.json()["detail"]


def test_it_carries_the_browser_extension_with_the_id_the_helper_trusts(client):
    import base64
    import hashlib
    import json

    from focusguard_agent.helper.server import EXTENSION_ID

    archive = _open(client.get(URL))
    names = set(archive.namelist())
    for name in ("manifest.json", "background.js", "popup.html", "popup.js", "icon128.png"):
        assert f"FocusGuard/extension/{name}" in names

    manifest = json.loads(archive.read("FocusGuard/extension/manifest.json"))
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    assert "".join(chr(ord("a") + int(c, 16)) for c in digest) == EXTENSION_ID
