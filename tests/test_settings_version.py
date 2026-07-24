"""Settings / updates endpoints must expose a real package version (not crash)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy import __version__
from remedy.interfaces.api import create_app


def test_settings_returns_package_version():
    """Regression: GET /api/settings used bare ``version`` → NameError → UI 0.9.0."""
    client = TestClient(create_app())
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("version"), data
    assert data["version"] == __version__
    assert data["version"] != "0.9.0"


def test_updates_check_returns_current_version():
    client = TestClient(create_app())
    r = client.get("/api/updates/check")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("current_version") == __version__


def test_updates_check_honors_shell_current_query():
    """Desktop shell version must drive availability — not only the sidecar."""
    client = TestClient(create_app())
    # Pretend the EXE is older than whatever is installed in Python.
    r = client.get("/api/updates/check", params={"current": "0.0.1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["current_version"] == "0.0.1"
    assert data.get("python_version") == __version__
    # If GitHub/PyPI is reachable and has any newer release, flag it.
    # Offline CI still returns a structured body without crashing.
    assert "update_available" in data
    assert "latest_desktop" in data or data.get("error")
