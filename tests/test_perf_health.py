"""Perf / health regressions: vision probes must not freeze the API event loop."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from remedy.vision.runtime import invalidate_running_cache, is_running
from remedy.vision.service import get_status


def test_stream_lock_reflects_active_turns(tmp_path: Path):
    """Desktop parent gates self-inject restarts on stream locks — no auth, no DB."""
    from remedy.core.stream_lock import (
        acquire_stream_lock,
        any_stream_active,
        release_stream_lock,
    )

    assert any_stream_active() is False
    acquire_stream_lock(tmp_path, "sid-turn-active")
    try:
        assert any_stream_active() is True
    finally:
        release_stream_lock(tmp_path, "sid-turn-active")
    assert any_stream_active() is False


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


def test_settings_snapshot_includes_light_vision(tmp_path: Path, monkeypatch):
    """Settings snapshot must not hang on vision HTTP health (Go owns GET)."""
    from remedy.interfaces.settings_apply import public_settings_snapshot

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("REMEDY_HOME", str(home))
    with patch("remedy.vision.service.is_running", return_value=False):
        t0 = time.perf_counter()
        body = public_settings_snapshot(
            {
                "name": "Remedy",
                "setup_completed": True,
                "llm_provider": "openai",
                "llm_model": "gpt-4o-mini",
                "llm_base_url": "https://api.openai.com/v1",
                "home_dir": str(home),
                "vision": {"enabled": True, "model_id": "smolvlm2-2.2b"},
            }
        )
        ms = (time.perf_counter() - t0) * 1000
    assert body["vision_enabled"] is True
    assert body["vision_model_id"] == "smolvlm2-2.2b"
    assert ms < 2000, f"settings snapshot took {ms:.0f}ms"


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


def test_connected_providers_fast_without_ollama(tmp_path: Path, monkeypatch):
    """Connected classification must not burn a 1.5s Ollama timeout (Go owns HTTP)."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.interfaces.config import (
        classify_provider_connection,
        detect_ollama,
        public_provider_catalog,
    )
    from remedy.interfaces.model_discovery import invalidate_ollama_detect_cache

    invalidate_ollama_detect_cache()
    t0 = time.perf_counter()
    ollama = detect_ollama()
    catalog = public_provider_catalog({})
    items = []
    for p in catalog:
        connected, reason = classify_provider_connection(
            p["id"],
            cfg={},
            keys={},
            keys_set={},
            ollama_available=bool(ollama.get("available")),
            xai_connected=False,
        )
        items.append({**p, "connected": connected, "connect_reason": reason})
    ms = (time.perf_counter() - t0) * 1000
    ids = {p["id"] for p in items}
    assert "demo" in ids
    demo = next(p for p in items if p["id"] == "demo")
    assert demo["connected"] is True
    # Closed Ollama + cache/precheck should be tens of ms, not ~1500.
    assert ms < 800, f"connected providers classify took {ms:.0f}ms"



def test_is_running_neg_cache_skips_port_probe(tmp_path: Path):
    """Closed-port miss must be cached longer than the old 2.5s TTL.

    Live desktop polls vision/status every few seconds; without a longer
    negative cache each poll re-does a ~150ms Windows TCP connect.
    """
    home = tmp_path / "remedy-home"
    home.mkdir()
    import remedy.vision.runtime as rt
    from remedy.vision.config import save_vision_json

    save_vision_json(
        {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 18741,
            "base_url": "http://127.0.0.1:18741/v1",
        },
        home,
    )
    invalidate_running_cache()
    with patch.object(rt, "_port_open", return_value=False) as port_open:
        assert is_running(home, force=True) is False
        assert port_open.call_count == 1
        assert is_running(home, force=False) is False
        assert port_open.call_count == 1  # neg cache hit
        # Within neg TTL still cached.
        rt._running_cache["ts"] = time.time() - (rt._RUNNING_CACHE_NEG_TTL_S - 1.0)
        assert is_running(home, force=False) is False
        assert port_open.call_count == 1
        # Past neg TTL → probe again.
        rt._running_cache["ts"] = time.time() - (rt._RUNNING_CACHE_NEG_TTL_S + 0.5)
        assert is_running(home, force=False) is False
        assert port_open.call_count == 2

