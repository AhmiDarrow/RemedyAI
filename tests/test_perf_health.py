"""Perf / health regressions: vision probes must not freeze the API event loop."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.vision.runtime import invalidate_running_cache, is_running
from remedy.vision.service import get_status


def test_ping_is_public_and_fast():
    client = TestClient(create_app())
    t0 = time.perf_counter()
    r = client.get("/api/ping")
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    assert r.json().get("status") == "ok"
    assert "version" in r.json()
    # Local TestClient should be well under 100ms; keep a loose bound for CI.
    assert ms < 500, f"/api/ping took {ms:.0f}ms"


def test_is_running_skips_http_when_port_closed(tmp_path: Path):
    """Dead vision port must not urlopen (was ~4s freezes → status bar flap)."""
    home = tmp_path / "remedy-home"
    home.mkdir()
    from remedy.vision.config import save_vision_json

    save_vision_json(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 18740,  # unused
            "base_url": "http://127.0.0.1:18740/v1",
        },
        home,
    )
    invalidate_running_cache()
    with patch("remedy.vision.runtime._health") as health:
        t0 = time.perf_counter()
        ok = is_running(home, force=True)
        ms = (time.perf_counter() - t0) * 1000
        assert ok is False
        health.assert_not_called()
        # Windows TCP connect to a closed port is ~100–300ms; still far below the old ~4s urlopen.
        assert ms < 800, f"is_running on closed port took {ms:.0f}ms"


def test_get_status_light_skips_catalog_and_health(tmp_path: Path):
    status = get_status(
        {"home_dir": str(tmp_path / "remedy-home"), "vision": {"enabled": False}},
        light=True,
    )
    assert status["enabled"] is False
    assert status.get("catalog") is None
    assert status.get("health") is None


def test_settings_includes_light_vision(tmp_path: Path, monkeypatch):
    """GET /settings must not hang on vision HTTP health."""
    home = tmp_path / "home"
    home.mkdir()
    cfg = home / "config.toml"
    cfg.write_text(
        'name = "Remedy"\nsetup_completed = true\nllm_provider = "openai"\n'
        f'home_dir = "{home.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Bypass auth for route exercise
    app = create_app(api_key=None)
    client = TestClient(app)
    with patch("remedy.interfaces.routes.settings.load_config") as lc:
        lc.return_value = {
            "name": "Remedy",
            "setup_completed": True,
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
            "llm_base_url": "https://api.openai.com/v1",
            "home_dir": str(home),
            "vision": {"enabled": True, "model_id": "qwen2.5-vl-3b"},
        }
        with patch(
            "remedy.interfaces.routes.settings._find_config_path",
            return_value=cfg,
        ):
            with patch(
                "remedy.vision.service.is_running",
                return_value=False,
            ):
                t0 = time.perf_counter()
                r = client.get("/api/settings")
                ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200
    body = r.json()
    assert "vision" in body
    assert body["vision"]["enabled"] is True
    assert ms < 2000, f"/api/settings took {ms:.0f}ms"


def test_secret_load_skips_repeated_harden(tmp_path: Path, monkeypatch):
    """auth_dir must not re-run icacls on every secrets read."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.interfaces import secret_store

    secret_store.invalidate_provider_keys_cache()
    secret_store._hardened_paths.clear()
    secret_store.set_provider_secret("openai", "sk-test", home=tmp_path)

    with patch.object(secret_store, "_harden_path", wraps=secret_store._harden_path) as harden:
        secret_store.invalidate_provider_keys_cache()
        # After first create, reads should not harden again.
        for _ in range(5):
            secret_store.load_provider_keys(tmp_path)
        # At most one dir harden if path was not yet in set; ideally zero on pure reads.
        assert harden.call_count <= 1


def test_provider_keys_cache_hit(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.interfaces import secret_store

    secret_store.invalidate_provider_keys_cache()
    secret_store.set_provider_secret("xai", "sk-x", home=tmp_path)
    secret_store.invalidate_provider_keys_cache()
    a = secret_store.load_provider_keys(tmp_path)
    b = secret_store.load_provider_keys(tmp_path)
    assert a == b == {"xai": "sk-x"}
    # Mutating returned dict must not poison cache
    a["xai"] = "mutated"
    c = secret_store.load_provider_keys(tmp_path)
    assert c["xai"] == "sk-x"
