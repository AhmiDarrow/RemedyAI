"""Free options list + zero-setup demo provider."""

from __future__ import annotations

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
    assert meta.get("live_models") is False
    ids = [m["id"] for m in (meta.get("models") or [])]
    assert "codestral-latest" in ids
    # Never ship image/promo/foreign paid brands in the curated demo list
    for bad in ("deepseek", "flux", "kling", "seedance", "gpt-image", "veo"):
        assert not any(bad in i.lower() for i in ids)


def test_poe_in_catalog():
    assert "poe" in PROVIDER_CATALOG
    meta = PROVIDER_CATALOG["poe"]
    assert "api.poe.com" in str(meta.get("base_url") or "")
    assert "POE_API_KEY" in (meta.get("env_keys") or [])
    ids = [m["id"] for m in (meta.get("models") or [])]
    assert "Claude-Sonnet-4.6" in ids
    p, m, u = normalize_llm_settings("poe", "Grok-4", None)
    assert p == "poe"
    assert m == "Grok-4"
    assert "api.poe.com" in u
    # Flexible: foreign-looking ids stay (Poe bot names)
    p2, m2, _ = normalize_llm_settings("poe", "Some-Community-Bot", None)
    assert p2 == "poe"
    assert m2 == "Some-Community-Bot"
    by_id = {i["id"]: i for i in public_provider_catalog()}
    assert by_id["poe"]["auth"] == ["api_key"]
    assert by_id["poe"]["default_model"]


def test_demo_model_allowlist_blocks_junk():
    from remedy.interfaces.routes.catalog import _demo_model_allowed

    catalog = PROVIDER_CATALOG["demo"]["models"]
    assert _demo_model_allowed("codestral-latest", catalog)
    assert not _demo_model_allowed("deepseek-v4-flash", catalog)
    assert not _demo_model_allowed("flux-kontext-max", catalog)
    assert not _demo_model_allowed("kling-v3.0-pro", catalog)


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
        "POE_API_KEY",
        "REMEDY_POE_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
        "MISTRAL_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
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
    for k in (
        "XAI_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "REMEDY_LLM_API_KEY",
        "POE_API_KEY",
        "REMEDY_POE_API_KEY",
        "OPENROUTER_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
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
