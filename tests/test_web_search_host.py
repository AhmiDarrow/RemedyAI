"""Managed OpenSERP host: pin, pytest skip, no SSRF hole."""

from __future__ import annotations

from pathlib import Path

from remedy.runtime import web_search_host as host


def test_pytest_skips_download_and_start(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("REMEDY_ENSURE_ASSETS", raising=False)
    r = host.ensure_web_search_host(tmp_path)
    assert r.get("skipped") is True
    assert host.find_binary(tmp_path) is None


def test_disabled_does_not_start(tmp_path: Path):
    r = host.ensure_web_search_host(tmp_path, enabled=False)
    assert r.get("skipped") is True
    assert r.get("reason") == "web_tools_disabled"


def test_base_url_is_loopback_only():
    url = host.base_url()
    assert url.startswith("http://127.0.0.1:")
    assert str(host.OPENSERP_PORT) in url


def test_platform_asset_is_pinned():
    asset = host._platform_asset()
    if asset is None:
        return
    name, digest = asset
    assert name in host._ASSETS
    assert digest == host._ASSETS[name]
    assert len(digest) == 64
