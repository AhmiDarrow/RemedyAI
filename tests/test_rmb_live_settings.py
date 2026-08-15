"""RMB settings must affect the live process (not disk-only)."""

from __future__ import annotations

from remedy.nanoswarm.token_nanobot import (
    clear_context_window_cache,
    get_cached_context_window,
)
from remedy.runtime.rmb.config import load_rmb_json, merge_state, save_rmb_json
from remedy.runtime.rmb.service import apply_rmb_settings


def test_apply_rmb_settings_updates_ctx_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    clear_context_window_cache()
    home = str(tmp_path)
    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "ctx_size": 8192,
                "model_id": "qwen25-coder-7b",
                "base_url": "http://127.0.0.1:8787/v1",
            }
        ),
        home,
    )

    # Don't actually spawn llama-server in unit tests
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: False,
    )
    starts: list[dict] = []

    def _fake_start(**kwargs):
        starts.append(kwargs)
        return {"ok": True, "ctx_size": 32768, "base_url": "http://127.0.0.1:8787/v1"}

    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        _fake_start,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: {"ok": True},
    )

    out = apply_rmb_settings(
        {"ctx_size": 32768, "enabled": True},
        home_dir=home,
        live=True,
        wait_s=1.0,
    )
    st = merge_state(load_rmb_json(home))
    assert int(st["ctx_size"]) == 32768
    # Cache must reflect live config so next turn budgets at 32k
    hit = get_cached_context_window("http://127.0.0.1:8787/v1", "qwen25-coder-7b")
    assert hit == 32768
    assert out.get("live_apply", {}).get("ctx_size_config") == 32768
    # Not running → start with new settings
    assert starts, "should start RMB when enabled and not running"
    assert out.get("live_apply", {}).get("started") is True


def test_apply_rmb_settings_restarts_when_running(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    save_rmb_json(
        merge_state({"enabled": True, "ctx_size": 8192, "model_id": "qwen25-coder-7b"}),
        home,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: True,
    )
    stops: list[int] = []
    starts: list[int] = []
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: stops.append(1) or {"ok": True},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: starts.append(1)
        or {"ok": True, "ctx_size": 16384, "base_url": "http://127.0.0.1:8787/v1"},
    )

    out = apply_rmb_settings(
        {"ctx_size": 16384},
        home_dir=home,
        live=True,
        wait_s=1.0,
    )
    assert stops and starts, "running process must be restarted for ctx_size"
    assert out["live_apply"]["restarted"] is True
    assert "ctx_size" in out["live_apply"]["process_keys_changed"]
    assert out["live_apply"]["ctx_size_live"] == 16384
    assert "restarted" in (out.get("live_note") or "").lower() or out.get("live_note")


def test_apply_rmb_settings_live_false_skips_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    save_rmb_json(merge_state({"enabled": True, "ctx_size": 8192}), home)
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: True,
    )
    starts: list[int] = []
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda **k: starts.append(1) or {"ok": True},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.stop_rmb_server",
        lambda **k: {"ok": True},
    )
    apply_rmb_settings({"ctx_size": 32768}, home_dir=home, live=False)
    assert not starts
    assert int(merge_state(load_rmb_json(home))["ctx_size"]) == 32768


def test_apply_rmb_does_not_steal_provider_or_approval(tmp_path, monkeypatch):
    """Enabled RMB apply must not force llm_provider or flip approval_mode."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    home = str(tmp_path)
    gguf = tmp_path / "local-model.gguf"
    gguf.write_bytes(b"x" * 80)
    (tmp_path / "config.toml").write_text(
        'llm_provider = "openai"\n'
        'llm_model = "gpt-4o-mini"\n'
        'llm_base_url = "https://api.openai.com/v1"\n'
        'approval_mode = "ask"\n',
        encoding="utf-8",
    )
    save_rmb_json(
        merge_state(
            {
                "enabled": True,
                "ctx_size": 8192,
                "model_id": "qwen25-coder-7b",
                "model_path": str(gguf),
            }
        ),
        home,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.managed_process_alive",
        lambda: False,
    )
    modes: list[str] = []
    monkeypatch.setattr(
        "remedy.core.approvals.APPROVALS.set_mode",
        lambda mode: modes.append(str(mode)),
        raising=False,
    )
    from remedy.interfaces import api_support

    api_support.invalidate_config_cache()
    apply_rmb_settings(
        {"ctx_size": 16384, "enabled": True},
        home_dir=home,
        live=False,
    )
    cfg = api_support.load_config()
    assert str(cfg.get("llm_provider") or "").lower() == "openai"
    assert str(cfg.get("approval_mode") or "ask").lower() == "ask"
    assert modes == []
