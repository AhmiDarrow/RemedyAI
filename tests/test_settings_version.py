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
    # Partner gender must round-trip; missing field caused silent reset to female.
    assert "agent_gender" in data
    assert data["agent_gender"] in ("female", "male", "neutral")


# GET /api/updates/check is owned by Go httpapi — see updates_test.go.


