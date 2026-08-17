"""Free options list + zero-setup demo provider."""

from __future__ import annotations

import pytest

from remedy.interfaces.config import (
    DEMO_DUMMY_API_KEY,
    PROVIDER_CATALOG,
    apply_env_provider_bootstrap,
    classify_provider_connection,
    normalize_llm_settings,
    provider_credentials_ready,
    public_provider_catalog,
    resolve_provider_api_key,
    validate_provider_model,
)
from remedy.interfaces.provider_catalog import free_options_public


def test_frozen_connected_allowlist_is_ignored():
    from remedy.interfaces.config import effective_provider_allowlist

    catalog = {"demo", "xai", "anthropic", "openai", "deepseek", "google"}
    # Buggy save: only what was connected that day
    frozen = {"demo", "xai"}
    assert (
        effective_provider_allowlist(
            list(frozen),
            catalog_ids=catalog,
            connected_ids={"demo", "xai"},
        )
        is None
    )
    # Explicit hide (user unchecked several, list is still large) is honored
    hidden = {"demo", "xai", "anthropic", "openai"}
    got = effective_provider_allowlist(
        list(hidden),
        catalog_ids=catalog,
        connected_ids={"demo", "xai", "anthropic"},
    )
    assert got == hidden


def test_anthropic_models_payload_and_url():
    from remedy.interfaces.config import (
        anthropic_auth_headers,
        anthropic_models_url,
        parse_anthropic_models_payload,
    )

    assert anthropic_models_url("https://api.anthropic.com/v1").endswith("/v1/models")
    headers = anthropic_auth_headers("sk-ant-test")
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"]
    rows = parse_anthropic_models_payload(
        {
            "data": [
                {"id": "claude-sonnet-4-0", "display_name": "Claude Sonnet 4"},
                {"id": "claude-opus-4-1", "display_name": "Claude Opus 4.1"},
                {"id": "", "display_name": "skip"},
            ]
        }
    )
    assert [r["id"] for r in rows] == ["claude-sonnet-4-0", "claude-opus-4-1"]
    assert rows[0]["source"] == "endpoint"


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


def test_free_options_hides_demo_when_disabled(monkeypatch):
    """Air-gapped / enterprise: REMEDY_DEMO_DISABLED must not offer guest demo."""
    monkeypatch.setenv("REMEDY_DEMO_DISABLED", "1")
    opts = free_options_public()
    ids = {o["id"] for o in opts}
    assert "demo" not in ids
    assert "ollama" in ids
    assert "google" in ids
    monkeypatch.delenv("REMEDY_DEMO_DISABLED", raising=False)
    opts2 = free_options_public()
    assert "demo" in {o["id"] for o in opts2}


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


def test_custom_placeholder_is_not_connected():
    """Unused custom/RMB catalog defaults must not appear in the picker."""
    ok, reason = classify_provider_connection(
        "custom",
        cfg={"llm_provider": "deepseek", "llm_base_url": "https://api.deepseek.com/v1"},
        keys={},
        keys_set={},
        ollama_available=False,
    )
    assert ok is False
    assert reason == "no_credentials"
    ok_rmb, reason_rmb = classify_provider_connection(
        "rmb",
        cfg={"llm_provider": "demo"},
        keys={},
        keys_set={},
        ollama_available=False,
    )
    assert ok_rmb is False
    assert reason_rmb == "no_credentials"


def test_custom_active_local_is_connected():
    ok, reason = classify_provider_connection(
        "custom",
        cfg={
            "llm_provider": "custom",
            "llm_base_url": "http://127.0.0.1:5001/v1",
        },
        keys={},
        keys_set={},
        ollama_available=False,
    )
    assert ok is True
    assert reason == "active_local"


def test_probe_demo_and_missing_key(monkeypatch):
    import asyncio

    from fastapi import FastAPI

    from remedy.interfaces.routes.auth import ProviderProbeRequest, register_auth_routes

    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda *a, **k: "",
    )
    monkeypatch.setattr(
        "remedy.interfaces.routes.auth.load_config",
        lambda: {"llm_provider": "demo"},
    )
    app = FastAPI()
    register_auth_routes(app)

    probe = None
    for route in app.routes:
        if getattr(route, "path", "") == "/api/providers/probe":
            probe = route.endpoint
            break
    assert probe is not None
    demo = asyncio.run(probe(ProviderProbeRequest(provider="demo")))
    assert demo["ok"] is True
    missing = asyncio.run(probe(ProviderProbeRequest(provider="openai")))
    assert missing["ok"] is False
    assert "key" in (missing.get("error") or "").lower()


def test_stored_key_marks_provider_connected():
    ok, reason = classify_provider_connection(
        "deepseek",
        cfg={"llm_provider": "demo"},
        keys={"deepseek": "sk-test"},
        keys_set={},
        ollama_available=False,
    )
    assert ok is True
    assert reason == "api_key"


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


def test_normalize_demo_clamps_junk_models():
    """Guest free path must not keep image/foreign ids from a previous provider."""
    default = PROVIDER_CATALOG["demo"]["models"][0]["id"]
    p, m, u = normalize_llm_settings("demo", "deepseek-v4-flash", None)
    assert p == "demo"
    assert m == default
    assert "llm7.io" in u
    p2, m2, _ = normalize_llm_settings("demo", "flux-kontext-max", None)
    assert m2 == default
    p3, m3, _ = normalize_llm_settings("demo", "codestral-latest", None)
    assert m3 == "codestral-latest"
    with pytest.raises(ValueError, match="demo model"):
        validate_provider_model("demo", "kling-v3.0-pro")
    assert validate_provider_model("demo", "codestral-latest") == "codestral-latest"


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


def test_public_catalog_never_echoes_key_material():
    """GET /api/providers is static metadata — a configured key must never
    appear, and the only 'sk-' in the payload is help text (D-PROVIDERS-LEAK
    was a substring false positive on "do not paste sk-ant-oat tokens")."""
    import json
    import re

    from remedy.interfaces.config import public_provider_catalog

    fake = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    cfg = {
        "provider_keys": {"anthropic": fake, "openai": "sk-" + "Q" * 40},
        "llm_api_key": fake,
        "llm_provider": "anthropic",
    }
    blob = json.dumps(public_provider_catalog(cfg))
    assert fake not in blob
    assert "Q" * 40 not in blob
    assert not re.search(r"\b(sk|xai)-[A-Za-z0-9_\-]{20,}", blob)
