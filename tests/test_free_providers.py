"""Free options list + zero-setup demo provider."""

from __future__ import annotations

import os

from remedy.interfaces.config import (
    DEMO_DUMMY_API_KEY,
    PROVIDER_CATALOG,
    apply_env_provider_bootstrap,
    free_options_public,
    normalize_llm_settings,
    provider_credentials_ready,
    public_provider_catalog,
    resolve_provider_api_key,
)


def test_demo_in_catalog():
    assert "demo" in PROVIDER_CATALOG
    meta = PROVIDER_CATALOG["demo"]
    assert meta.get("free_tier") == "instant"
    assert meta.get("auth") == ["none"]
    assert "llm7.io" in str(meta.get("base_url") or "")


def test_free_options_public_includes_demo_and_free_keys():
    opts = free_options_public()
    ids = {o["id"] for o in opts}
    assert "demo" in ids
    assert "google" in ids
    assert "groq" in ids
    assert "openrouter" in ids
    assert "mistral" in ids
    assert "ollama" in ids
    demo = next(o for o in opts if o["id"] == "demo")
    assert demo["tier"] == "instant"
    assert demo["default_model"]


def test_public_catalog_exposes_free_tier():
    items = public_provider_catalog()
    by_id = {i["id"]: i for i in items}
    assert by_id["demo"]["free_tier"] == "instant"
    assert by_id["demo"]["auth"] == ["none"]
    assert by_id["google"]["free_tier"] == "free_key"
    assert by_id["groq"]["key_docs_url"]
    assert by_id["ollama"]["free_tier"] == "local"


def test_demo_credentials_ready():
    assert provider_credentials_ready({"llm_provider": "demo"}) is True
    assert provider_credentials_ready({"llm_provider": "ollama"}) is True
    assert provider_credentials_ready({"llm_provider": "openai"}) is False


def test_demo_disabled_via_env(monkeypatch):
    monkeypatch.setenv("REMEDY_DEMO_DISABLED", "1")
    assert provider_credentials_ready({"llm_provider": "demo"}) is False
    monkeypatch.delenv("REMEDY_DEMO_DISABLED", raising=False)


def test_resolve_demo_api_key():
    assert resolve_provider_api_key({"llm_provider": "demo"}, "demo") == DEMO_DUMMY_API_KEY


def test_normalize_demo_settings():
    p, m, u = normalize_llm_settings("demo", None, None)
    assert p == "demo"
    assert m
    assert "llm7.io" in u


def test_bootstrap_prefers_demo_when_empty(monkeypatch):
    # Clear keys that would take priority
    for k in (
        "XAI_API_KEY",
        "REMEDY_XAI_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "REMEDY_LLM_API_KEY",
        "REMEDY_PREFER_OLLAMA",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("REMEDY_DEMO_DISABLED", raising=False)
    monkeypatch.setenv("REMEDY_PREFER_DEMO", "1")
    cfg = apply_env_provider_bootstrap(
        {"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_api_key": ""}
    )
    assert cfg["llm_provider"] == "demo"
    assert "llm7.io" in str(cfg.get("llm_base_url") or "")


def test_bootstrap_skips_demo_when_disabled(monkeypatch):
    for k in ("XAI_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "REMEDY_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REMEDY_DEMO_DISABLED", "1")
    cfg = apply_env_provider_bootstrap(
        {"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_api_key": ""}
    )
    assert cfg["llm_provider"] == "openai"


def test_get_provider_demo():
    from remedy.core.providers import get_provider

    p = get_provider("demo")
    headers = p.auth_headers("")
    assert "Authorization" in headers
    assert "Bearer" in headers["Authorization"]
