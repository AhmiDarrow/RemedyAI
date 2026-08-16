"""Per-session provider switch must not keep the previous provider's API host."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from remedy.interfaces.api_support import _sync_runtime_llm_from_config


def test_sync_uses_session_provider_override(monkeypatch) -> None:
    """Status-bar Grok switch while config still DeepSeek must not hit DeepSeek."""
    cfg = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "llm_base_url": "https://api.deepseek.com/v1",
        "approval_mode": "ask",
    }
    monkeypatch.setattr(
        "remedy.interfaces.api_support._load_config_cached",
        lambda: dict(cfg),
    )
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda _c, prov: f"key-for-{prov}",
    )

    runtime = MagicMock()
    runtime.reconfigure_llm = MagicMock()
    runtime._llm_api_key = ""

    key = _sync_runtime_llm_from_config(
        runtime,
        model_override="grok-4.5",
        provider_override="xai",
    )
    assert key == "key-for-xai"
    kwargs = runtime.reconfigure_llm.call_args.kwargs
    assert kwargs["provider"] == "xai"
    assert "grok" in str(kwargs["model"]).lower() or kwargs["model"] == "grok-4.5"
    # Must not keep DeepSeek host when provider changed
    base = str(kwargs.get("base_url") or "").lower()
    assert "deepseek" not in base


def test_sync_without_override_keeps_global(monkeypatch) -> None:
    cfg = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-v4-flash",
        "llm_base_url": "https://api.deepseek.com/v1",
        "approval_mode": "ask",
    }
    monkeypatch.setattr(
        "remedy.interfaces.api_support._load_config_cached",
        lambda: dict(cfg),
    )
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda _c, prov: "ds-key",
    )
    runtime = SimpleNamespace(_llm_api_key="")
    runtime.reconfigure_llm = MagicMock()  # type: ignore[attr-defined]

    _sync_runtime_llm_from_config(runtime, model_override=None, provider_override=None)
    kwargs = runtime.reconfigure_llm.call_args.kwargs
    assert kwargs["provider"] == "deepseek"
