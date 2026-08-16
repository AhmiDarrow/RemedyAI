"""Anthropic token classification (Max OAuth vs Console API)."""

from __future__ import annotations

import pytest

from remedy.core.react_loop.errors import is_fatal_llm_api_error
from remedy.interfaces.anthropic_auth import (
    classify_anthropic_secret,
    is_subscription_oauth_token,
    reject_if_subscription_token,
)
from remedy.interfaces.config import normalize_llm_settings, set_provider_key


def test_classify_console_vs_oauth() -> None:
    assert classify_anthropic_secret("sk-ant-api03-abcdef") == "console_api"
    assert classify_anthropic_secret("sk-ant-oat01-abcdef") == "oauth_subscription"
    assert is_subscription_oauth_token("sk-ant-oat01-x") is True
    assert is_subscription_oauth_token("sk-ant-api03-x") is False


def test_reject_subscription_token() -> None:
    with pytest.raises(ValueError, match="Claude Code"):
        reject_if_subscription_token("sk-ant-oat01-secret")
    reject_if_subscription_token("sk-ant-api03-ok")


def test_resolve_skips_env_oat_token(tmp_path, monkeypatch) -> None:
    from remedy.interfaces.config import resolve_provider_api_key

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-from-env")
    monkeypatch.delenv("REMEDY_LLM_API_KEY", raising=False)
    got = resolve_provider_api_key(
        {"llm_provider": "anthropic", "home_dir": str(tmp_path)},
        "anthropic",
        home=tmp_path,
    )
    assert got == ""


def test_set_provider_key_rejects_oat(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="Max"):
        set_provider_key({}, "anthropic", "sk-ant-oat01-nope", home=tmp_path)


def test_retired_claude_code_provider_snaps_to_anthropic() -> None:
    prov, model, url = normalize_llm_settings(
        "claude_code", "claude-sonnet-5", "claude-code://local"
    )
    assert prov == "anthropic"
    assert model.startswith("claude")
    assert "anthropic.com" in url


def test_claude_code_only_error_is_fatal() -> None:
    body = '{"error":{"message":"This credential is only authorized for use with Claude Code"}}'
    assert is_fatal_llm_api_error(403, body) is True
