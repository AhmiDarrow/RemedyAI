"""Generic endpoint model discovery (remedy.interfaces.model_discovery).

No test opens a socket: aiohttp.ClientSession is replaced with a recorder that
answers from per-URL rules.
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from remedy.interfaces import model_discovery as md
from remedy.interfaces.config import (
    default_model_for_provider,
    normalize_llm_settings,
    validate_provider_model,
)


class _Resp:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    @property
    def ok(self):
        return 200 <= self.status < 400

    async def json(self, **_kw):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeHTTP:
    def __init__(self):
        self.requests: list[dict] = []
        self._rules: list[tuple] = []

    def on(self, substring, *, status=200, payload=None, error=None):
        self._rules.append((substring, status, payload, error))

    def respond(self, url):
        for sub, status, payload, error in self._rules:
            if sub in url:
                if error is not None:
                    raise error
                return _Resp(status, payload if payload is not None else {})
        return _Resp(404, {"error": "nope"})

    @property
    def urls(self):
        return [r["url"] for r in self.requests]


@pytest.fixture()
def http(monkeypatch):
    fake = FakeHTTP()

    class _Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, headers=None, ssl=None):
            fake.requests.append({"url": url, "headers": headers or {}, "ssl": ssl})
            return fake.respond(url)

        def post(self, url, json=None, headers=None, ssl=None):
            fake.requests.append({"url": url, "headers": headers or {}, "json": json})
            return fake.respond(url)

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    monkeypatch.setattr(md, "_PRECHECK_LOCAL_LISTEN", False)
    return fake


def discover(*a, **k):
    return asyncio.run(md.discover_models(*a, **k))


# --- flavours -----------------------------------------------------------------


def test_openai_style_listing_is_parsed_and_remembered(http):
    http.on("/v1/models", payload={"data": [{"id": "a"}, {"id": "b"}]})
    res = discover("https://api.example.com/v1", "sk-x", provider_hint="openai")
    assert res.ok and res.flavour == "openai"
    assert [m["id"] for m in res.models] == ["a", "b"]
    assert http.requests[0]["headers"]["Authorization"] == "Bearer sk-x"
    assert set(md.live_known_models("openai")) == {"a", "b"}


def test_a_bare_list_body_is_accepted(http):
    http.on("/models", payload=["m1", {"id": "m2"}])
    res = discover("http://127.0.0.1:5001/v1", "", provider_hint="custom")
    assert res.ok
    assert [m["id"] for m in res.models][:2] == ["m1", "m2"]


def test_anthropic_uses_its_own_header_and_follows_pages(http):
    http.on(
        "after_id=p1",
        payload={"data": [{"id": "claude-old", "display_name": "Old"}], "has_more": False},
    )
    http.on(
        "anthropic.com/v1/models",
        payload={
            "data": [{"id": "claude-sonnet-5", "display_name": "Sonnet 5"}],
            "has_more": True,
            "last_id": "p1",
        },
    )
    res = discover("https://api.anthropic.com/v1", "sk-ant", provider_hint="anthropic")
    assert res.ok and res.flavour == "anthropic"
    assert [m["id"] for m in res.models] == ["claude-sonnet-5", "claude-old"]
    assert res.models[0]["name"] == "Sonnet 5"
    h = http.requests[0]["headers"]
    assert h["x-api-key"] == "sk-ant" and "Authorization" not in h
    assert "limit=1000" in http.urls[0]


def test_ollama_tags_strip_latest_as_a_suffix_not_a_charset(http):
    http.on(
        "/api/tags",
        payload={
            "models": [
                {"name": "mistral:latest"},
                {"name": "llava:latest"},
                {"name": "qwen2.5:7b"},
            ]
        },
    )
    http.on("/api/ps", payload={"models": [{"name": "qwen2.5:7b"}]})
    http.on(
        "/api/show",
        payload={
            "capabilities": ["completion", "tools"],
            "model_info": {"llama.context_length": 32768},
        },
    )
    res = discover("http://127.0.0.1:11434/v1", "", provider_hint="ollama")
    assert res.ok and res.flavour == "ollama"
    ids = [m["id"] for m in res.models]
    assert ids == ["mistral", "llava", "qwen2.5:7b"]  # not "mistr" / "llav"
    assert res.loaded == ["qwen2.5:7b"]
    by = {m["id"]: m for m in res.models}
    assert by["qwen2.5:7b"]["loaded"] is True
    assert by["mistral"]["context_window"] == 32768
    assert by["mistral"]["tools"] is True
    assert md.choose_default(res.models, loaded=res.loaded) == "qwen2.5:7b"


def test_ollama_embedding_models_are_not_chat(http):
    http.on("/api/tags", payload={"models": [{"name": "nomic-embed-text:latest"}]})
    http.on("/api/show", payload={"capabilities": ["embedding"]})
    res = discover("http://127.0.0.1:11434/v1", "", provider_hint="ollama")
    assert res.ok and res.models[0]["chat"] is False


def test_custom_local_host_falls_through_openai_to_ollama(http):
    http.on("/v1/models", status=404, payload={"error": "not found"})
    http.on("/api/tags", payload={"models": [{"name": "phi3:latest"}]})
    res = discover("http://192.168.1.20:11000/v1", "", provider_hint="custom")
    assert res.ok and res.flavour == "ollama"
    assert [m["id"] for m in res.models] == ["phi3"]


def test_gemini_native_listing_enriches_the_openai_bridge(http):
    http.on("/openai/models", payload={"data": [{"id": "gemini-3.1-flash"}]})
    http.on(
        "/v1beta/models?key=",
        payload={
            "models": [
                {
                    "name": "models/gemini-3.1-flash",
                    "displayName": "Gemini 3.1 Flash",
                    "inputTokenLimit": 1048576,
                    "supportedGenerationMethods": ["generateContent"],
                },
                {
                    "name": "models/embedding-001",
                    "supportedGenerationMethods": ["embedContent"],
                },
            ]
        },
    )
    res = discover(
        "https://generativelanguage.googleapis.com/v1beta/openai", "g-key", provider_hint="google"
    )
    assert res.ok
    by = {m["id"]: m for m in res.models}
    assert by["gemini-3.1-flash"]["context_window"] == 1048576
    assert by["gemini-3.1-flash"]["name"] == "Gemini 3.1 Flash"
    assert by["embedding-001"]["chat"] is False


# --- capability fields --------------------------------------------------------


def test_openrouter_rows_carry_modalities_pricing_and_context(http):
    http.on(
        "/models",
        payload={
            "data": [
                {
                    "id": "vendor/vision-x",
                    "context_length": 200000,
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                    },
                    "supported_parameters": ["tools", "reasoning"],
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "vendor/img-gen",
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                },
            ]
        },
    )
    res = discover("https://openrouter.ai/api/v1", "sk-or", provider_hint="openrouter")
    by = {m["id"]: m for m in res.models}
    v = by["vendor/vision-x"]
    assert v["vision"] is True and v["tools"] is True and v["reasoning"] is True
    assert v["context_window"] == 200000
    assert v["pricing"] == {"prompt": "0.000001", "completion": "0.000002"}
    assert by["vendor/img-gen"]["chat"] is False


@pytest.mark.parametrize(
    ("row", "ctx"),
    [
        ({"id": "m", "meta": {"n_ctx_train": 8192}}, 8192),  # llama.cpp
        ({"id": "m", "max_context_length": 131072}, 131072),  # Mistral / LM Studio
        ({"id": "m", "max_model_len": 4096}, 4096),  # vLLM
        ({"id": "m", "context_window": "lots"}, None),
    ],
)
def test_context_window_keys_from_different_hosts(http, row, ctx):
    http.on("/models", payload={"data": [row]})
    res = discover("https://box.test/v1", "sk", provider_hint="custom")
    assert res.models[0].get("context_window") == ctx


def test_mistral_capabilities_block_is_read(http):
    http.on(
        "/models",
        payload={
            "data": [
                {
                    "id": "pixtral-large-latest",
                    "capabilities": {"completion_chat": True, "function_calling": True, "vision": True},
                },
                {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
            ]
        },
    )
    res = discover("https://api.mistral.ai/v1", "sk", provider_hint="mistral")
    by = {m["id"]: m for m in res.models}
    assert by["pixtral-large-latest"]["vision"] is True
    assert by["mistral-embed"]["chat"] is False


# --- failure reporting --------------------------------------------------------


def test_a_rejected_key_reports_status_and_message_and_stops(http):
    http.on(
        "/models", status=401, payload={"error": {"message": "Incorrect API key provided"}}
    )
    res = discover("https://api.openai.com/v1", "sk-bad", provider_hint="openai")
    assert not res.ok and res.status == 401
    assert "Incorrect API key" in res.error
    assert len(http.requests) == 1


def test_closed_local_port_fails_fast_without_http_wait():
    """Live 2026-08-27 boot: GET /api/models waited 2s on RMB refused and stalled the UI."""
    import time

    t0 = time.perf_counter()
    res = asyncio.run(md.discover_models("http://127.0.0.1:9/v1", "", provider_hint="rmb"))
    elapsed = time.perf_counter() - t0
    assert not res.ok
    assert "not listening" in (res.error or "").lower()
    assert elapsed < 0.8


def test_closed_port_listen_is_cached_for_the_next_probe():
    """Second GET /api/models in the same chrome tick must not pay another 150ms TCP."""
    import time

    md.invalidate_ollama_detect_cache()
    first = asyncio.run(md.discover_models("http://127.0.0.1:9/v1", "", provider_hint="rmb"))
    assert "not listening" in (first.error or "").lower()
    t0 = time.perf_counter()
    second = asyncio.run(md.discover_models("http://127.0.0.1:9/v1", "", provider_hint="rmb"))
    elapsed = time.perf_counter() - t0
    assert "not listening" in (second.error or "").lower()
    assert elapsed < 0.05


def test_concurrent_closed_port_probes_share_one_tcp_wait():
    import time

    md.invalidate_ollama_detect_cache()

    async def both():
        return await asyncio.gather(
            md.discover_models("http://127.0.0.1:9/v1", "", provider_hint="rmb"),
            md.discover_models("http://127.0.0.1:9/v1", "", provider_hint="ollama"),
        )

    t0 = time.perf_counter()
    a, b = asyncio.run(both())
    elapsed = time.perf_counter() - t0
    assert "not listening" in (a.error or "").lower()
    assert "not listening" in (b.error or "").lower()
    # One 150ms SYN wait, not two serialized on the loop.
    assert elapsed < 0.8


def test_an_unreachable_host_reports_the_connection_error(http):
    http.on("/models", error=aiohttp.ClientConnectorError(None, OSError("refused")))
    res = discover("http://127.0.0.1:9/v1", "", provider_hint="rmb")
    assert not res.ok and res.status is None
    assert "connection failed" in res.error


def test_an_empty_listing_is_not_a_success(http):
    http.on("/models", payload={"data": []})
    res = discover("https://api.groq.com/openai/v1", "gsk", provider_hint="groq")
    assert res.ok is False
    assert "no models" in res.error


def test_no_base_url_is_not_attempted(http):
    res = discover("", "sk", provider_hint="openai")
    assert res.attempted is False and http.requests == []




def test_xai_language_models_run_with_the_openai_listing(http):
    """First live xAI discovery must not stack /models then /language-models RTTs."""
    http.on(
        "/language-models",
        payload={
            "models": [
                {
                    "id": "grok-4.5",
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                    "aliases": ["grok-4"],
                }
            ]
        },
    )
    http.on("/models", payload={"data": [{"id": "grok-4.5"}]})
    res = discover("https://api.x.ai/v1", "xai-key", provider_hint="xai")
    assert res.ok
    ids = [m["id"] for m in res.models]
    assert "grok-4.5" in ids and "grok-4" in ids
    by = {m["id"]: m for m in res.models}
    assert by["grok-4.5"]["vision"] is True
    urls = http.urls
    assert any("/models" in u and "language-models" not in u for u in urls)
    assert any("language-models" in u for u in urls)

# --- helpers ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("0.0.0.0:11434", "http://127.0.0.1:11434/v1"),
        ("http://gpu-box:11435", "http://gpu-box:11435/v1"),
        ("gpu-box", "http://gpu-box:11434/v1"),
        ("", None),
    ],
)
def test_ollama_host_env_shapes(monkeypatch, raw, want):
    monkeypatch.setenv("OLLAMA_HOST", raw)
    assert md.ollama_base_url_from_env() == want


def test_choose_default_prefers_listed_preferred_then_loaded_then_chat():
    models = [
        {"id": "nomic-embed-text", "chat": False},
        {"id": "llama3.2", "chat": True},
        {"id": "qwen2.5:7b", "chat": True, "loaded": True},
    ]
    assert md.choose_default(models, preferred=["gone", "llama3.2"]) == "llama3.2"
    assert md.choose_default(models) == "qwen2.5:7b"
    assert md.choose_default(models[:2]) == "llama3.2"
    assert md.choose_default([{"id": "whisper-1"}]) == "whisper-1"
    assert md.choose_default([]) is None


# --- live registry is authoritative for validation ----------------------------


def test_validator_accepts_ids_the_endpoint_listed():
    with pytest.raises(ValueError):
        validate_provider_model("groq", "qwen-qwq-32b")
    md.remember_live_models("groq", [{"id": "qwen-qwq-32b"}])
    assert validate_provider_model("groq", "qwen-qwq-32b") == "qwen-qwq-32b"
    # Still rejects what nobody vouches for.
    with pytest.raises(ValueError) as exc:
        validate_provider_model("groq", "claude-opus-5")
    assert "qwen-qwq-32b" in str(exc.value)


def test_legacy_alias_only_applies_when_the_old_id_is_gone():
    assert validate_provider_model("xai", "grok-3") == "grok-4.3"
    assert normalize_llm_settings("xai", "grok-3", None)[1] == "grok-4.3"
    md.remember_live_models("xai", [{"id": "grok-3"}, {"id": "grok-4.5"}])
    assert validate_provider_model("xai", "grok-3") == "grok-3"
    assert normalize_llm_settings("xai", "grok-3", None)[1] == "grok-3"


def test_default_model_comes_from_the_endpoint_before_the_catalog():
    assert default_model_for_provider("ollama") == ""  # nothing known, no guess
    md.remember_live_models("ollama", [{"id": "nomic-embed-text", "chat": False}, {"id": "phi3"}])
    assert default_model_for_provider("ollama") == "phi3"
    assert normalize_llm_settings("ollama", "", None)[1] == "phi3"
    # Closed provider: curated first entry until the endpoint says otherwise.
    assert default_model_for_provider("openai") != ""


def test_ollama_never_defaults_to_an_openai_model_name():
    prov, mid, url = normalize_llm_settings("ollama", None, None)
    assert prov == "ollama"
    assert mid == ""
    assert url.endswith("11434/v1")
