"""Sleev gateway routing unit tests (no live gateway required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.sleev import (
    SLEEV_DEFAULT_GATEWAY,
    SLEEV_HARNESS_ID,
    apply_sleev_routing,
    discover_sleev_gateway_url,
    is_sleev_enabled,
    prepare_llm_http,
    should_route_via_sleev,
    sleev_headers,
    sleev_status,
    upstream_base_url,
)
from remedy.core.providers import get_provider


def test_sleev_disabled_by_default():
    assert is_sleev_enabled({}) is False
    assert is_sleev_enabled({"sleev_enabled": False}) is False
    assert is_sleev_enabled({"sleev_enabled": True}) is True


def test_should_not_route_local_providers():
    cfg = {"sleev_enabled": True}
    for p in ("ollama", "rmb", "llamacpp", "demo"):
        assert should_route_via_sleev(p, "http://127.0.0.1:11434/v1", cfg=cfg) is False


def test_should_route_cloud_when_enabled():
    cfg = {"sleev_enabled": True}
    assert should_route_via_sleev("xai", "https://api.x.ai/v1", cfg=cfg) is True
    assert should_route_via_sleev("deepseek", "https://api.deepseek.com/v1", cfg=cfg) is True
    assert should_route_via_sleev("openai", "https://api.openai.com/v1", cfg=cfg) is True
    assert should_route_via_sleev("anthropic", "https://api.anthropic.com", cfg=cfg) is True


def test_sleev_headers_builtin_vs_base_url():
    h = sleev_headers("openai")
    assert h["sleev-harness"] == SLEEV_HARNESS_ID
    assert h["sleev-provider"] == "openai"
    assert "sleev-base-url" not in h

    h2 = sleev_headers("xai", base_url="https://api.x.ai/v1")
    assert h2["sleev-harness"] == SLEEV_HARNESS_ID
    assert h2["sleev-base-url"] == "https://api.x.ai/v1"
    assert "sleev-provider" not in h2

    h3 = sleev_headers("deepseek", base_url="https://api.deepseek.com/v1")
    assert h3["sleev-base-url"] == "https://api.deepseek.com/v1"


def test_apply_sleev_routing_rewrites_base_and_headers():
    cfg = {"sleev_enabled": True, "sleev_gateway_url": "http://127.0.0.1:17321"}
    base, headers = apply_sleev_routing(
        provider="xai",
        base_url="https://api.x.ai/v1",
        headers={"Authorization": "Bearer secret", "Content-Type": "application/json"},
        cfg=cfg,
    )
    assert base == "http://127.0.0.1:17321"
    assert headers["Authorization"] == "Bearer secret"
    assert headers["sleev-harness"] == "remedy"
    assert headers["sleev-base-url"] == "https://api.x.ai/v1"


def test_apply_sleev_routing_noop_when_disabled():
    base, headers = apply_sleev_routing(
        provider="xai",
        base_url="https://api.x.ai/v1",
        headers={"Authorization": "Bearer k"},
        cfg={"sleev_enabled": False},
    )
    assert base == "https://api.x.ai/v1"
    assert "sleev-harness" not in headers


def test_prepare_llm_http_openai_compat():
    adapter = get_provider("xai")
    cfg = {"sleev_enabled": True, "sleev_gateway_url": "http://127.0.0.1:17321"}
    endpoint, headers = prepare_llm_http(
        provider="xai",
        base_url="https://api.x.ai/v1",
        api_key="xai-test",
        adapter=adapter,
        cfg=cfg,
    )
    assert endpoint == "http://127.0.0.1:17321/chat/completions"
    assert headers["sleev-harness"] == "remedy"
    assert headers["sleev-base-url"] == "https://api.x.ai/v1"
    assert "Bearer xai-test" in headers["Authorization"]


def test_prepare_llm_http_anthropic_path():
    adapter = get_provider("anthropic")
    cfg = {"sleev_enabled": True, "sleev_gateway_url": "http://127.0.0.1:17321"}
    endpoint, headers = prepare_llm_http(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-test",
        adapter=adapter,
        cfg=cfg,
    )
    assert endpoint == "http://127.0.0.1:17321/v1/messages"
    assert headers["sleev-provider"] == "anthropic"
    assert headers["sleev-harness"] == "remedy"


def test_discover_from_install_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_dir = tmp_path / "sleev"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"proxy": {"host": "127.0.0.1", "port": 19999}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.delenv("REMEDY_SLEEV_GATEWAY", raising=False)
    url = discover_sleev_gateway_url({})
    assert url == "http://127.0.0.1:19999"


def test_discover_explicit_override():
    url = discover_sleev_gateway_url({"sleev_gateway_url": "http://10.0.0.5:17321/v1"})
    assert url == "http://10.0.0.5:17321"


def test_upstream_ignores_sleev_gateway():
    cfg = {"sleev_enabled": True, "sleev_gateway_url": "http://127.0.0.1:17321"}
    # Stored URL already points at Sleev — fall back to catalog
    up = upstream_base_url("xai", "http://127.0.0.1:17321", cfg=cfg)
    assert "x.ai" in up


def test_sleev_status_shape():
    st = sleev_status({"sleev_enabled": True})
    assert st["enabled"] is True
    assert st["harness"] == SLEEV_HARNESS_ID
    assert "gateway_url" in st
    assert st["gateway_url"]  # non-empty default


def test_default_gateway_constant():
    assert SLEEV_DEFAULT_GATEWAY == "http://127.0.0.1:17321"


def test_setup_phrase_enables_sleev():
    from remedy.interfaces.settings_apply import resolve_setup_phrase

    for phrase in (
        "configure sleev",
        "enable sleev",
        "setup sleev",
        "sleev",
        "save tokens",
        "route via sleev",
    ):
        patch = resolve_setup_phrase(phrase)
        assert patch is not None, phrase
        assert patch.get("sleev_enabled") is True, phrase

    off = resolve_setup_phrase("disable sleev")
    assert off is not None and off.get("sleev_enabled") is False
    off2 = resolve_setup_phrase("sleev off")
    assert off2 is not None and off2.get("sleev_enabled") is False


def test_openrouter_not_routed_to_openai_default():
    """OpenRouter/Poe share OpenAIProvider — must not send sleev-base-url=openai.com."""
    adapter = get_provider("openrouter")
    cfg = {"sleev_enabled": True, "sleev_gateway_url": "http://127.0.0.1:17321"}
    # Simulate a stale/wrong base_url left as the OpenAI default
    _ep, headers = prepare_llm_http(
        provider="openrouter",
        base_url="https://api.openai.com/v1",
        api_key="sk-or-test",
        adapter=adapter,
        cfg=cfg,
    )
    assert "openrouter.ai" in headers.get("sleev-base-url", "")
    assert "api.openai.com" not in headers.get("sleev-base-url", "")

    _ep2, headers2 = prepare_llm_http(
        provider="poe",
        base_url="https://api.openai.com/v1",
        api_key="k",
        adapter=get_provider("poe"),
        cfg=cfg,
    )
    assert "poe.com" in headers2.get("sleev-base-url", "")


def test_cfg_from_runtime_prefers_attrs():
    from remedy.core.sleev import cfg_from_runtime, prepare_llm_http
    from types import SimpleNamespace

    runtime = SimpleNamespace(
        _sleev_enabled=True,
        _sleev_gateway_url="http://127.0.0.1:17321",
        config=SimpleNamespace(sleev_enabled=True, sleev_gateway_url=""),
    )
    cfg = cfg_from_runtime(runtime)
    assert cfg is not None
    assert cfg.get("sleev_enabled") is True

    adapter = get_provider("deepseek")
    ep, headers = prepare_llm_http(
        provider="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key="sk-test",
        adapter=adapter,
        runtime=runtime,
    )
    assert "127.0.0.1:17321" in ep
    assert headers.get("sleev-harness") == "remedy"
    assert "deepseek.com" in headers.get("sleev-base-url", "")
