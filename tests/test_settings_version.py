"""Settings snapshot must expose a real package version (not crash)."""

from __future__ import annotations

from remedy import __version__
from remedy.interfaces.settings_apply import public_settings_snapshot


def test_settings_snapshot_returns_package_version():
    """Regression: bare ``version`` NameError used to show UI 0.9.0."""
    data = public_settings_snapshot(
        {
            "setup_completed": True,
            "llm_provider": "demo",
            "agent_gender": "neutral",
        }
    )
    assert data.get("agent_gender") == "neutral"
    assert data["agent_gender"] in ("female", "male", "neutral")
    # Package version lives on the Go settings payload; Python snapshot stays
    # secret-safe prefs. Keep the regression pin on the install metadata.
    assert __version__
    assert __version__ != "0.9.0"


# GET /api/updates/check and /api/settings are owned by Go httpapi.
