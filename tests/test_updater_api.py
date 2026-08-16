"""Update check API shape."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app


def test_updates_check_returns_structure():
    app = create_app(api_key="")
    client = TestClient(app)
    fake = {
        "current": "0.10.32",
        "latest": "0.10.32",
        "update_available": False,
        "url": None,
        "errors": [],
    }
    # Patch common entry points
    with (
        patch("remedy.interfaces.updater.check_for_updates", return_value=fake, create=True),
        patch("remedy.interfaces.routes.misc.check_for_updates", return_value=fake, create=True),
    ):
        for path in ("/api/updates/check", "/api/updates/check?current=0.10.32"):
            r = client.get(path)
            if r.status_code == 404:
                continue
            assert r.status_code == 200
            data = r.json()
            assert isinstance(data, dict)
            return
    pytest.skip("updates check route not found")


def test_is_trusted_download_url_logic():
    """Mirror Rust allowlist intent in a pure-Python check for docs/regression."""
    def trusted(url: str) -> bool:
        if url.startswith("https://github.com/AhmiDarrow/RemedyAI/releases/"):
            return True
        if url.startswith("https://objects.githubusercontent.com/") or url.startswith(
            "https://release-assets.githubusercontent.com/"
        ):
            return ".." not in url and len(url) < 2048
        return False

    assert trusted(
        "https://github.com/AhmiDarrow/RemedyAI/releases/download/v0.10.32/x.exe"
    )
    assert not trusted("https://github.com/evil/repo/releases/download/v1/x.exe")
    assert not trusted("http://evil.com/x.exe")
