"""Per-session provider switch must not keep the previous provider's API host."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from remedy.interfaces.api_support import _apply_llm_to_runtime, resolve_llm_slot


def test_slot_uses_session_provider_override(monkeypatch) -> None:
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

    provider, model, base_url, api_key = resolve_llm_slot(
        provider_override="xai",
        model_override="grok-4.5",
    )
    assert api_key == "key-for-xai"
    assert provider == "xai"
    assert "grok" in str(model).lower() or model == "grok-4.5"
    assert "deepseek" not in str(base_url or "").lower()

    runtime = MagicMock()
    runtime.reconfigure_llm = MagicMock()
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    kwargs = runtime.reconfigure_llm.call_args.kwargs
    assert kwargs["provider"] == "xai"
    assert "deepseek" not in str(kwargs.get("base_url") or "").lower()


def test_slot_without_override_keeps_global(monkeypatch) -> None:
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
    provider, model, base_url, api_key = resolve_llm_slot()
    assert provider == "deepseek"
    assert api_key == "ds-key"

    runtime = SimpleNamespace()
    runtime.reconfigure_llm = MagicMock()  # type: ignore[attr-defined]
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    kwargs = runtime.reconfigure_llm.call_args.kwargs
    assert kwargs["provider"] == "deepseek"
