"""Live provider /models discovery policy: the endpoint decides, nothing compiled in."""

from __future__ import annotations

from remedy.interfaces.config import normalize_llm_settings


def test_deepseek_allows_endpoint_style_ids() -> None:
    """Ids returned by live /models must not be snapped away."""
    _, m, _ = normalize_llm_settings("deepseek", "deepseek-v4-pro", None)
    assert m == "deepseek-v4-pro"


def test_xai_allows_endpoint_style_ids() -> None:
    _, m, _ = normalize_llm_settings("xai", "grok-4.5", None)
    assert m == "grok-4.5"


def test_garbage_model_snaps_to_the_endpoint_default() -> None:
    from remedy.interfaces import model_discovery as md

    md.remember_live_models("deepseek", [{"id": "deepseek-flash"}, {"id": "deepseek-pro"}])
    try:
        _, m, _ = normalize_llm_settings("deepseek", "not-a-real-model-zzz", None)
        assert m == "deepseek-flash"
    finally:
        md.remember_live_models("deepseek", [])


def test_validate_provider_model_rejects_what_the_endpoint_does_not_list() -> None:
    import pytest

    from remedy.interfaces import model_discovery as md
    from remedy.interfaces.config import validate_provider_model

    md.remember_live_models("deepseek", [{"id": "deepseek-flash"}])
    try:
        with pytest.raises(ValueError, match="not offered"):
            validate_provider_model("deepseek", "not-a-real-model-zzz")
        assert validate_provider_model("deepseek", "deepseek-flash") == "deepseek-flash"
    finally:
        md.remember_live_models("deepseek", [])


