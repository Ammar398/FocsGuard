"""What changes when FocusGuard runs on the internet instead of this PC.

* the hosted database URL is pointed at the driver that is installed;
* the API serves the built portal itself, so the site has one address -
  without letting a path escape the portal folder or hiding API 404s;
* each computer remembers its server, only over https, and the Helper then
  trusts that server's portal and no other site.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from focusguard_agent.config import (
    AgentSettings,
    get_settings,
    normalise_server_url,
    save_server_url,
    server_file,
)
from focusguard_agent.helper.server import portal_origins
from focusguard_backend.database.session import normalise_url

# --- database ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    ["postgres://u:p@db.example.com:5432/fg", "postgresql://u:p@db.example.com:5432/fg"],
)
def test_a_hosted_postgres_url_uses_psycopg_3(given):
    url = normalise_url(given)
    assert url == "postgresql+psycopg://u:p@db.example.com:5432/fg"
    assert create_engine(url).dialect.driver == "psycopg"


def test_other_urls_are_left_alone():
    assert normalise_url("sqlite:///x.db") == "sqlite:///x.db"
    assert normalise_url("postgresql+psycopg://a/b") == "postgresql+psycopg://a/b"


# --- the portal, served by the API --------------------------------------------------


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    portal = tmp_path / "dist"
    (portal / "assets").mkdir(parents=True)
    (portal / "index.html").write_text("<!doctype html><title>FocusGuard</title>")
    (portal / "assets" / "app-1a2b.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the portal")

    monkeypatch.setenv("FOCUSGUARD_PORTAL_DIR", str(portal))
    monkeypatch.setenv("FOCUSGUARD_BACKEND_DB", str(tmp_path / "deploy.db"))
    from focusguard_backend.database import session as db_session

    db_session.reset_engine()
    import focusguard_backend.main as main

    app = importlib.reload(main).app
    with TestClient(app) as client:
        yield client
    monkeypatch.delenv("FOCUSGUARD_PORTAL_DIR")
    importlib.reload(main)
    db_session.reset_engine()


def test_the_portal_and_its_client_side_routes_are_served(deployed):
    home = deployed.get("/")
    assert home.status_code == 200 and "FocusGuard" in home.text
    # A page reload on /employees/3 must get the app, not a 404.
    assert "FocusGuard" in deployed.get("/employees/3").text
    asset = deployed.get("/assets/app-1a2b.js")
    assert asset.status_code == 200
    assert "immutable" in asset.headers["cache-control"]


def test_the_api_still_answers_and_its_404s_stay_404s(deployed):
    assert deployed.get("/health").json()["status"] == "ok"
    assert deployed.get("/api/v1/no-such-route").status_code == 404


def test_a_path_cannot_leave_the_portal_folder(deployed):
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "outside the portal" not in deployed.get(path).text


# --- which server a computer uses ------------------------------------------------


def test_only_https_server_addresses_are_accepted():
    assert normalise_server_url(" https://fg.onrender.com/ ") == "https://fg.onrender.com"
    assert normalise_server_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in ("http://fg.onrender.com", "fg.onrender.com", "https://fg.onrender.com/api"):
        with pytest.raises(ValueError):
            normalise_server_url(bad)


def test_a_saved_server_is_used_and_its_portal_is_trusted(tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUSGUARD_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FOCUSGUARD_SYNC__BACKEND_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no project .env in the way

    save_server_url("https://fg.onrender.com/")
    assert server_file().parent == tmp_path

    settings = get_settings(reload=True)
    try:
        assert settings.sync.backend_url == "https://fg.onrender.com"
        origins = portal_origins(settings)
        assert "https://fg.onrender.com" in origins
        assert "http://localhost:5173" in origins  # local development still works
        assert "https://evil.example.com" not in origins
    finally:
        import focusguard_agent.config as config

        config._settings = None  # the next caller loads its own settings


def test_without_a_saved_server_nothing_extra_is_trusted():
    origins = portal_origins(AgentSettings())
    assert origins == {
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
        "http://127.0.0.1:8000",
    }
