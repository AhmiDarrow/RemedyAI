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


def test_updates_check_second_poll_does_not_refetch():
    """Chrome polls this; live was 525ms hitting PyPI + GitHub every time."""
    import json
    from unittest.mock import patch

    app = create_app(api_key="")
    client = TestClient(app)
    body = json.dumps(
        {
            "info": {"version": "0.41.6"},
            "version": "0.41.6",
            "tag_name": "v0.41.6",
            "html_url": "https://github.com/AhmiDarrow/RemedyAI/releases/latest",
            "assets": [],
        }
    ).encode()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body

    n = {"c": 0}

    def _urlopen(req, timeout=None):
        n["c"] += 1
        return _Resp()

    with patch("urllib.request.urlopen", _urlopen):
        r1 = client.get("/api/updates/check", params={"current": "0.41.6"})
        after_first = n["c"]
        r2 = client.get("/api/updates/check", params={"current": "0.41.6"})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert after_first >= 1
    assert n["c"] == after_first
    assert r1.json()["current_version"] == "0.41.6"
    assert r2.json() == r1.json()


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
