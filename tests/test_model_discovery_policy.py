"""Live provider /models discovery policy + legacy model id migration."""

from __future__ import annotations

from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    normalize_llm_settings,
)


def test_deepseek_catalog_uses_v4_ids() -> None:
    models = PROVIDER_CATALOG["deepseek"]["models"]
    ids = {m["id"] for m in models}
    assert "deepseek-v4-flash" in ids
    assert "deepseek-v4-pro" in ids
    assert "deepseek-chat" not in ids
    assert "deepseek-reasoner" not in ids


def test_xai_catalog_prefers_current_grok() -> None:
    models = PROVIDER_CATALOG["xai"]["models"]
    ids = {m["id"] for m in models}
    assert "grok-4.5" in ids or "grok-4.3" in ids
    assert "grok-3-mini" not in ids


def test_legacy_deepseek_chat_migrates() -> None:
    p, m, u = normalize_llm_settings(
        "deepseek", "deepseek-chat", "https://api.deepseek.com/v1"
    )
    assert p == "deepseek"
    assert m == "deepseek-v4-flash"
    assert "deepseek.com" in u


def test_legacy_deepseek_reasoner_migrates() -> None:
    _, m, _ = normalize_llm_settings("deepseek", "deepseek-reasoner", None)
    assert m == "deepseek-v4-flash"


def test_legacy_anthropic_catalog_ids_migrate() -> None:
    _, m, u = normalize_llm_settings(
        "anthropic", "claude-sonnet-4-0", "https://api.anthropic.com/v1"
    )
    assert m == "claude-sonnet-5"
    assert u.rstrip("/").endswith("anthropic.com/v1")
    _, m2, _ = normalize_llm_settings("anthropic", "claude-opus-4-1", None)
    assert m2 == "claude-opus-5"


def test_legacy_grok3_migrates() -> None:
    p, m, u = normalize_llm_settings("xai", "grok-3-mini", "https://api.x.ai/v1")
    assert p == "xai"
    assert m in ("grok-4.3", "grok-4.5", "grok-4")
    assert "x.ai" in u


def test_deepseek_allows_endpoint_style_ids() -> None:
    """Ids returned by live /models must not be snapped away."""
    _, m, _ = normalize_llm_settings("deepseek", "deepseek-v4-pro", None)
    assert m == "deepseek-v4-pro"


def test_xai_allows_endpoint_style_ids() -> None:
    _, m, _ = normalize_llm_settings("xai", "grok-4.5", None)
    assert m == "grok-4.5"


def test_garbage_model_snaps_to_default_on_closed_provider() -> None:
    _, m, _ = normalize_llm_settings("deepseek", "not-a-real-model-zzz", None)
    assert m == "deepseek-v4-flash"


def test_validate_provider_model_rejects_garbage() -> None:
    import pytest

    from remedy.interfaces.config import validate_provider_model

    with pytest.raises(ValueError, match="Unknown model"):
        validate_provider_model("deepseek", "not-a-real-model-zzz")
    assert validate_provider_model("deepseek", "deepseek-v4-flash") == "deepseek-v4-flash"
    assert validate_provider_model("deepseek", "deepseek-v4-pro") == "deepseek-v4-pro"
