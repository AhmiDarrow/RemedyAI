"""The catalog API: slash-command palette, custom command/agent files, model list.

This module is what the Settings screen and the status-bar model picker read.
When it is wrong the user sees a model list that is empty, full of image
generators, or full of another vendor's models -- and picking one produces a
chat that 401s. Three things have to hold:

* Model discovery is a *best effort*. A provider that is down, unauthenticated,
  or slow must degrade to the curated catalog, never to a 500 and never to an
  empty picker.
* Discovery must not be attempted when it cannot possibly work (no key, no
  base URL, live models switched off) -- a pointless probe costs seconds at
  boot and then caches an empty answer.
* Demo is a shared guest gateway. Its /models dump contains image, video and
  paid-key models; none of it may reach the picker.

The custom command/agent routes read markdown out of ~/.remedy. The name comes
from the URL, so a name must never be able to walk out of that directory, and a
file that is missing or malformed must be reported rather than raised.
"""

from __future__ import annotations

import time
from pathlib import Path

import aiohttp
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remedy.interfaces.model_discovery import DiscoveryResult, looks_like_chat_model
from remedy.interfaces.routes import catalog as catalog_mod
from remedy.interfaces.routes.catalog import (
    _demo_model_allowed,
    register_catalog_routes,
)

# --- doubles ------------------------------------------------------------------


class Runtime:
    """The bits of the agent runtime that the models route reads."""

    def __init__(self, provider=None, model=None, base_url=None, api_key=None):
        self._llm_provider = provider
        self._llm_model = model
        self._llm_base_url = base_url
        self._llm_api_key = api_key


class _FakeResponse:
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
    """Stand-in for aiohttp.ClientSession that records every probe."""

    def __init__(self):
        self.requests: list[dict] = []
        self.sessions = 0
        self._rules: list[tuple] = []

    def on(self, substring, *, status=200, payload=None, error=None):
        self._rules.append((substring, status, payload, error))

    def respond(self, url):
        for sub, status, payload, error in self._rules:
            if sub in url:
                if error is not None:
                    raise error
                return _FakeResponse(status, payload if payload is not None else {})
        # Unmatched probes answer "no models" so the route takes its soft-fail
        # path; tests assert on ``requests`` to prove a probe did not happen.
        return _FakeResponse(200, {"data": []})

    @property
    def urls(self):
        return [r["url"] for r in self.requests]


@pytest.fixture(autouse=True)
def http(monkeypatch):
    """No test may open a real socket, not even to localhost."""
    fake = FakeHTTP()

    class _Session:
        def __init__(self, *args, **kwargs):
            fake.sessions += 1

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, headers=None, ssl=None):
            fake.requests.append({"url": url, "headers": headers or {}, "ssl": ssl})
            return fake.respond(url)

        def post(self, url, json=None, headers=None, ssl=None):
            fake.requests.append(
                {"url": url, "headers": headers or {}, "ssl": ssl, "json": json}
            )
            return fake.respond(url)

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)
    return fake


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """The custom-command routes read the Remedy home -- point it at tmp."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / ".remedy"))
    return tmp_path


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "REMEDY_LLM_PROVIDER",
        "REMEDY_LLM_MODEL",
        "REMEDY_LLM_BASE_URL",
        "REMEDY_LLM_API_KEY",
        "REMEDY_LIVE_MODELS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def build(monkeypatch):
    def _build(cfg=None, runtime=None, memory=None):
        monkeypatch.setattr(catalog_mod, "load_config", lambda: dict(cfg or {}))
        app = FastAPI()
        register_catalog_routes(app, runtime=runtime, gateway=None, memory=memory)
        return TestClient(app)

    return _build


@pytest.fixture()
def client(build):
    return build()


def models(client, **params):
    r = client.get("/api/models", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def ids(payload):
    return [m["id"] for m in payload["models"]]


# --- pure helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    "mid",
    [
        "flux-1-schnell",
        "dall-e-3",
        "sdxl-turbo",
        "kling-v2",
        "seedance-1",
        "veo-3",
        "gpt-image-1",
        "whisper-large",
        "text-embedding-3-small",
        "omni-moderation-latest",
        "video-gen",
        "tts-1",
    ],
)
def test_demo_refuses_every_media_and_utility_model_id(mid):
    """These ids exist on the guest gateway and would 4xx or bill a real key."""
    assert _demo_model_allowed(mid, []) is False


@pytest.mark.parametrize("mid", ["", "   ", None])
def test_demo_refuses_a_blank_model_id(mid):
    assert _demo_model_allowed(mid, []) is False


def test_demo_refuses_anything_outside_the_curated_catalog():
    catalog = [{"id": "codestral-latest"}]
    assert _demo_model_allowed("codestral-latest", catalog) is True
    assert _demo_model_allowed("deepseek-chat", catalog) is False


def test_demo_falls_back_to_substring_rules_when_the_catalog_is_empty():
    """An empty allowlist must not become an allow-everything."""
    assert _demo_model_allowed("some-chat-model", []) is True
    assert _demo_model_allowed("some-flux-model", []) is False


def test_demo_ignores_catalog_entries_without_an_id():
    assert _demo_model_allowed("anything", [{"name": "no id here"}]) is True


@pytest.mark.parametrize(
    "mid",
    [
        "FLUX.1-pro",
        "Imagine-Image",
        "imagine-video-01",
        "stable-diffusion-3",
        "dalle-3",
        "sora-2",
        "runway-gen3",
        "text-embedding-ada-002",
        "omni-moderation",
    ],
)
def test_media_only_bots_are_recognised(mid):
    assert looks_like_chat_model(mid) is False


@pytest.mark.parametrize("mid", ["claude-opus-5", "gpt-4o", "grok-4", "", "llama3"])
def test_chat_models_are_not_mistaken_for_media(mid):
    assert looks_like_chat_model(mid) is True


# --- /api/commands and /api/agents -------------------------------------------


def test_the_builtin_command_palette_is_served(client):
    body = client.get("/api/commands").json()
    assert body["commands"]
    assert all(c["name"].startswith("/") for c in body["commands"])


def test_the_builtin_agent_list_is_served(client):
    body = client.get("/api/agents").json()
    assert {a["name"] for a in body["agents"]} >= {"default", "remedy"}


def test_a_slash_command_result_is_returned_with_its_session_and_command(
    build, monkeypatch
):
    seen = {}

    async def fake(command, session_id, memory, runtime=None):
        seen.update(
            command=command, session_id=session_id, memory=memory, runtime=runtime
        )
        return {"ok": True, "output": "hi"}

    monkeypatch.setattr(catalog_mod, "handle_slash_command", fake)
    rt, mem = Runtime(), object()
    client = build(runtime=rt, memory=mem)

    body = client.post("/api/sessions/s1/command", json={"command": "/help"}).json()
    assert body == {"session_id": "s1", "command": "/help", "ok": True, "output": "hi"}
    # runtime and memory must be handed through, not re-derived.
    assert seen["runtime"] is rt and seen["memory"] is mem


def test_a_handler_may_overwrite_the_echoed_command(build, monkeypatch):
    """Documents the merge order: ``**result`` wins over the echoed fields."""

    async def fake(command, session_id, memory, runtime=None):
        return {"command": "/rewritten", "session_id": "other"}

    monkeypatch.setattr(catalog_mod, "handle_slash_command", fake)
    body = build().post("/api/sessions/s1/command", json={"command": "/help"}).json()
    assert body == {"command": "/rewritten", "session_id": "other"}


def test_a_command_request_without_a_command_is_rejected(client):
    assert client.post("/api/sessions/s1/command", json={}).status_code == 422


def test_a_failing_slash_command_is_not_swallowed(build, monkeypatch):
    """A broken handler must surface, not return a cheerful empty result."""

    async def boom(command, session_id, memory, runtime=None):
        raise RuntimeError("handler exploded")

    monkeypatch.setattr(catalog_mod, "handle_slash_command", boom)
    with pytest.raises(RuntimeError, match="handler exploded"):
        build().post("/api/sessions/s1/command", json={"command": "/help"})


# --- custom commands / agents on disk -----------------------------------------


def write_md(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(text, encoding="utf-8")


@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_a_missing_directory_lists_nothing_instead_of_failing(client, kind):
    assert client.get(f"/api/{kind}/custom").json()[kind] == []


def test_custom_commands_are_listed_in_name_order(client, home):
    for stem in ("zeta", "alpha", "mid"):
        write_md(home / ".remedy" / "commands", stem, "body")
    body = client.get("/api/commands/custom").json()
    assert [c["name"] for c in body["commands"]] == ["alpha", "mid", "zeta"]
    assert all(c["file"].endswith(".md") for c in body["commands"])


def test_non_markdown_files_are_ignored(client, home):
    d = home / ".remedy" / "commands"
    write_md(d, "real", "body")
    (d / "notes.txt").write_text("ignore me", encoding="utf-8")
    (d / "README").write_text("ignore me", encoding="utf-8")
    listed = client.get("/api/commands/custom").json()["commands"]
    assert [c["name"] for c in listed] == ["real"]


def test_a_custom_agent_takes_its_name_from_frontmatter(client, home):
    write_md(
        home / ".remedy" / "agents",
        "file-stem",
        "---\nname: Reviewer\ndescription: reviews code\n---\nbody",
    )
    agent = client.get("/api/agents/custom").json()["agents"][0]
    assert agent["name"] == "Reviewer"
    assert agent["description"] == "reviews code"


def test_a_custom_command_takes_its_name_from_the_name_field(client, home):
    """It read ``fm["description"]`` into *both* fields, so a command with
    frontmatter was listed under its description and its own name was lost."""
    write_md(
        home / ".remedy" / "commands",
        "deploy",
        "---\nname: Deploy\ndescription: ship it\n---\nbody",
    )
    cmd = client.get("/api/commands/custom").json()["commands"][0]
    assert cmd["name"] == "Deploy"
    assert cmd["description"] == "ship it"


def test_a_command_without_a_name_in_frontmatter_keeps_its_file_stem(client, home):
    write_md(
        home / ".remedy" / "commands",
        "deploy",
        "---\ndescription: ship it\n---\nbody",
    )
    cmd = client.get("/api/commands/custom").json()["commands"][0]
    assert cmd["name"] == "deploy"


@pytest.mark.parametrize(
    "text",
    [
        "---\n: : :\n\tbad: [unclosed\n---\nbody",  # unparseable YAML
        "---\njust a scalar\n---\nbody",  # frontmatter is not a mapping
        "--- no closing fence at all\nbody",  # split gives fewer than 3 parts
        "---\n---\n",  # empty frontmatter
    ],
)
@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_broken_frontmatter_falls_back_to_the_file_stem(client, home, text, kind):
    """A hand-edited markdown file must not take the whole listing down."""
    write_md(home / ".remedy" / kind, "stem", text)
    entry = client.get(f"/api/{kind}/custom").json()[kind][0]
    assert entry["name"] == "stem"
    assert entry["description"] == ""


@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_undecodable_bytes_are_replaced_not_raised(client, home, kind):
    d = home / ".remedy" / kind
    d.mkdir(parents=True, exist_ok=True)
    (d / "binary.md").write_bytes(b"---\nname: \xff\xfe bad\n---\nbody")
    assert client.get(f"/api/{kind}/custom").status_code == 200


@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_a_custom_file_is_served_by_name(client, home, kind):
    write_md(home / ".remedy" / kind, "hello", "# Hello\n")
    r = client.get(f"/api/{kind}/custom/hello")
    assert r.status_code == 200
    assert r.json()["content"] == "# Hello\n"


@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_an_unknown_name_is_a_404_not_a_crash(client, home, kind):
    (home / ".remedy" / kind).mkdir(parents=True, exist_ok=True)
    assert client.get(f"/api/{kind}/custom/nope").status_code == 404


@pytest.mark.parametrize("kind", ["commands", "agents"])
@pytest.mark.parametrize("name", ["%2E%2E", "%20", ".%20", "%2E"])
def test_a_name_that_is_not_a_filename_is_refused(client, home, kind, name):
    write_md(home / ".remedy" / kind, "hello", "secret")
    r = client.get(f"/api/{kind}/custom/{name}")
    assert r.status_code == 400
    assert "Invalid" in r.json()["detail"]


@pytest.mark.parametrize("kind", ["commands", "agents"])
@pytest.mark.parametrize(
    "name",
    [
        "..%2F..%2Fconfig.toml",
        "..%5C..%5Cconfig.toml",
        "%2Fetc%2Fpasswd",
        "....%2F%2Fconfig",
    ],
)
def test_a_traversing_name_never_reaches_a_file_outside_the_directory(
    client, home, kind, name
):
    """The guard is basename-then-safe_path; either refuse or miss, never leak."""
    (home / ".remedy").mkdir(parents=True, exist_ok=True)
    (home / ".remedy" / "config.toml").write_text(
        "api_key = 'TOPSECRET'", encoding="utf-8"
    )
    (home / ".remedy" / kind).mkdir(parents=True, exist_ok=True)
    r = client.get(f"/api/{kind}/custom/{name}")
    assert r.status_code in (400, 404)
    assert "TOPSECRET" not in r.text


@pytest.mark.parametrize("kind", ["commands", "agents"])
def test_the_md_suffix_is_appended_not_assumed(client, home, kind):
    """``.../hello.md`` looks for ``hello.md.md`` -- it must 404, not serve."""
    write_md(home / ".remedy" / kind, "hello", "secret")
    assert client.get(f"/api/{kind}/custom/hello.md").status_code == 404


# --- /api/models: what is probed ---------------------------------------------


def test_without_a_key_no_remote_probe_is_attempted(build, http):
    """A keyless probe would 401 and then cache an empty list for everyone."""
    client = build(runtime=Runtime("custom", "m", "https://box.test/v1", ""))
    models(client)
    assert http.requests == []


def test_the_demo_dummy_key_does_not_unlock_discovery(build, http):
    client = build(runtime=Runtime("custom", "m", "https://box.test/v1", "unused"))
    models(client)
    assert http.requests == []


def test_a_provider_with_no_base_url_is_not_probed(build, http, monkeypatch):
    # normalize_llm_settings would otherwise fill in a catalog URL.
    monkeypatch.setattr(
        catalog_mod, "normalize_llm_settings", lambda p, m, u: (p, m, "")
    )
    client = build(runtime=Runtime("custom", "m", "", "sk-real"))
    models(client)
    assert http.requests == []


@pytest.mark.parametrize("off", ["0", "false", "no", "off", "OFF"])
def test_live_models_can_be_switched_off_by_env(build, http, monkeypatch, off):
    monkeypatch.setenv("REMEDY_LIVE_MODELS", off)
    client = build(runtime=Runtime("custom", "m", "https://box.test/v1", "sk-real"))
    models(client)
    assert http.requests == []


def test_demo_is_probed_without_a_bearer_and_only_curated_ids_surface(build, http):
    """The guest gateway lists image/video/paid ids; we validate the curated
    allowlist against it instead of dumping it into the picker."""
    http.on(
        "llm7.io",
        payload={
            "data": [
                {"id": "codestral-latest"},
                {"id": "gpt-oss:20b"},
                {"id": "flux-schnell"},
                {"id": "claude-opus-5"},
            ]
        },
    )
    client = build(runtime=Runtime("demo", "codestral-latest", "", "unused"))
    body = models(client)
    assert http.urls == ["https://api.llm7.io/v1/models"]
    assert "Authorization" not in http.requests[0]["headers"]
    assert body["provider"] == "demo"
    ids = [m["id"] for m in body["models"]]
    assert "flux-schnell" not in ids and "claude-opus-5" not in ids
    # gemini-3.1-flash-lite is curated but the gateway stopped serving it.
    assert ids == ["codestral-latest", "gpt-oss:20b"]
    assert body["discovery"]["ok"] is True


def test_demo_falls_back_to_the_curated_list_when_the_gateway_is_down(build, http):
    http.on("llm7.io", error=OSError("down"))
    client = build(runtime=Runtime("demo", "codestral-latest", "", "unused"))
    body = models(client)
    ids = [m["id"] for m in body["models"]]
    assert "codestral-latest" in ids and "gemini-3.1-flash-lite" in ids
    assert body["discovery"]["ok"] is False
    assert body["default"] == "codestral-latest"


def test_a_local_endpoint_is_probed_without_a_key_and_without_ssl_checks(build, http):
    client = build(runtime=Runtime("custom", "", "http://127.0.0.1:5001/v1", ""))
    models(client)
    # A local custom host is also asked for LM Studio's richer listing.
    assert http.urls[0] == "http://127.0.0.1:5001/v1/models"
    assert http.requests[0]["ssl"] is False
    assert "Authorization" not in http.requests[0]["headers"]


def test_a_remote_endpoint_is_probed_with_a_bearer_token_and_ssl(build, http):
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    models(client)
    assert http.requests[0]["headers"]["Authorization"] == "Bearer sk-real"
    assert http.requests[0]["ssl"] is True


def test_the_literal_key_local_is_not_sent_as_a_bearer_token(build, http):
    client = build(runtime=Runtime("custom", "", "http://127.0.0.1:5001/v1", "local"))
    models(client)
    assert http.requests and "Authorization" not in http.requests[0]["headers"]


def test_anthropic_is_probed_with_its_own_header_not_a_bearer_token(build, http):
    http.on(
        "/models", payload={"data": [{"id": "claude-x", "display_name": "Claude X"}]}
    )
    client = build(runtime=Runtime("anthropic", "claude-opus-5", None, "sk-ant"))
    body = models(client)
    assert http.urls == ["https://api.anthropic.com/v1/models?limit=1000"]
    assert http.requests[0]["headers"]["x-api-key"] == "sk-ant"
    assert "Authorization" not in http.requests[0]["headers"]
    assert "claude-x" in ids(body)


def test_anthropic_without_a_key_is_not_probed(build, http):
    client = build(runtime=Runtime("anthropic", "claude-opus-5", None, ""))
    assert models(client)["models"]
    assert http.requests == []


# --- /api/models: failure paths ----------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_an_http_error_falls_back_to_the_curated_catalog(build, http, status):
    http.on("/models", status=status, payload={"error": "nope"})
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, "sk-real"))
    body = models(client)
    assert "deepseek-v4-flash" in ids(body)
    assert all(m["provider"] == "deepseek" for m in body["models"])


@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ClientConnectionError("refused"),
        TimeoutError("too slow"),
        ValueError("garbage json"),
    ],
)
def test_a_transport_failure_falls_back_to_the_curated_catalog(build, http, error):
    http.on("/models", error=error)
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, "sk-real"))
    assert "deepseek-v4-flash" in ids(models(client))


def test_a_payload_without_a_data_list_is_treated_as_no_models(build, http):
    http.on("/models", payload={"object": "list"})
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, "sk-real"))
    assert ids(models(client)) == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_entries_without_an_id_are_skipped(build, http):
    http.on(
        "/models",
        payload={"data": [{}, {"id": ""}, {"name": "named-only"}, {"id": "real"}]},
    )
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    got = ids(models(client))
    assert "real" in got and "named-only" in got
    assert "" not in got


def test_a_duplicate_id_is_listed_once_with_the_catalog_name(build, http):
    http.on("/models", payload={"data": [{"id": "deepseek-v4-flash"}]})
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, "sk-real"))
    body = models(client)
    assert ids(body).count("deepseek-v4-flash") == 1
    entry = next(m for m in body["models"] if m["id"] == "deepseek-v4-flash")
    assert entry["name"] != "deepseek-v4-flash"  # friendly catalog label wins


# --- /api/models: filtering ---------------------------------------------------


def test_a_foreign_model_id_is_dropped_from_a_closed_provider(build, http):
    """A Claude id served by DeepSeek's /models would 404 on the first chat."""
    http.on(
        "/models",
        payload={
            "data": [{"id": "claude-opus-5"}, {"id": "gpt-4o"}, {"id": "deepseek-r2"}]
        },
    )
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, "sk-real"))
    got = ids(models(client))
    assert "claude-opus-5" not in got and "gpt-4o" not in got
    assert "deepseek-r2" in got  # native prefix is trusted


def test_open_weight_ids_served_by_a_closed_provider_are_kept(build, http):
    """Groq/Google really serve llama/qwen/gemma ids; a prefix guess must not
    throw away what the provider's own endpoint returned."""
    http.on(
        "/models",
        payload={
            "data": [
                {"id": "qwen-qwq-32b"},
                {"id": "gemma2-9b-it"},
                {"id": "deepseek-r1-distill-llama-70b"},
                {"id": "llama-3.3-70b-versatile"},
            ]
        },
    )
    client = build(runtime=Runtime("groq", "llama-3.3-70b-versatile", None, "sk-real"))
    got = ids(models(client))
    for mid in (
        "qwen-qwq-32b",
        "gemma2-9b-it",
        "deepseek-r1-distill-llama-70b",
        "llama-3.3-70b-versatile",
    ):
        assert mid in got


def test_a_multi_vendor_gateway_keeps_foreign_ids(build, http):
    http.on(
        "/models",
        payload={"data": [{"id": "anthropic/claude-opus-5"}, {"id": "openai/gpt-4o"}]},
    )
    client = build(runtime=Runtime("openrouter", "openrouter/auto", None, "sk-real"))
    got = ids(models(client))
    assert "anthropic/claude-opus-5" in got and "openai/gpt-4o" in got


@pytest.mark.parametrize("provider", ["openrouter", "poe", "xai"])
def test_media_bots_are_hidden_from_the_chat_picker(build, http, provider):
    http.on(
        "/models",
        payload={"data": [{"id": "black-forest-labs/flux-pro"}, {"id": "chat-ok"}]},
    )
    client = build(runtime=Runtime(provider, "", None, "sk-real"))
    assert "black-forest-labs/flux-pro" not in ids(models(client))


def test_demo_ignores_the_gateway_dump_even_when_live_is_forced(build, http, monkeypatch):
    """REMEDY_LIVE_MODELS=1 may probe, but demo output stays curated."""
    monkeypatch.setenv("REMEDY_LIVE_MODELS", "1")
    http.on("/models", payload={"data": [{"id": "flux-1"}, {"id": "deepseek-chat"}]})
    client = build(runtime=Runtime("demo", "codestral-latest", None, "unused"))
    assert ids(models(client)) == [
        "codestral-latest",
        "gemini-3.1-flash-lite",
        "gpt-oss:20b",
    ]


# --- /api/models: context windows --------------------------------------------


@pytest.mark.parametrize(
    "field", ["context_window", "context_length", "max_model_len", "n_ctx"]
)
def test_an_advertised_context_window_is_cached(build, http, monkeypatch, field):
    seen = []
    monkeypatch.setattr(
        "remedy.nanoswarm.token_nanobot.cache_context_window",
        lambda base, mid, ctx: seen.append((base, mid, ctx)),
    )
    http.on("/models", payload={"data": [{"id": "big", field: 32768}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    entry = next(m for m in models(client)["models"] if m["id"] == "big")
    # Returned to the picker *and* cached for budgeting.
    assert entry["context_window"] == 32768
    assert seen == [("https://box.test/v1", "big", 32768)]


def test_a_nested_meta_context_window_is_read(build, http, monkeypatch):
    seen = []
    monkeypatch.setattr(
        "remedy.nanoswarm.token_nanobot.cache_context_window",
        lambda base, mid, ctx: seen.append(ctx),
    )
    http.on("/models", payload={"data": [{"id": "big", "meta": {"context_window": 8192}}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    models(client)
    assert seen == [8192]


@pytest.mark.parametrize("ctx", ["lots", {"n": 1}])
def test_an_unparseable_context_window_is_ignored(build, http, monkeypatch, ctx):
    monkeypatch.setattr(
        "remedy.nanoswarm.token_nanobot.cache_context_window",
        lambda *a: pytest.fail("must not cache a non-integer context window"),
    )
    http.on("/models", payload={"data": [{"id": "odd", "context_window": ctx}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    assert "odd" in ids(models(client))


# --- /api/models: caching -----------------------------------------------------


def test_a_successful_probe_is_reused_for_the_next_call(build, http):
    http.on("/models", payload={"data": [{"id": "m1"}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    first = ids(models(client))
    second = ids(models(client))
    assert first == second
    assert len(http.requests) == 1


def test_a_successful_probe_survives_well_past_a_minute(build, http):
    http.on("/models", payload={"data": [{"id": "m1"}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    models(client)
    cache = client.app.state._model_discovery_cache
    for key, (_, disc) in list(cache.items()):
        cache[key] = (time.time() - 100.0, disc)
    models(client)
    assert len(http.requests) == 1


def test_an_empty_probe_is_retried_soon_rather_than_locked_in(build, http):
    """A boot-time timeout must not pin the picker to curated stubs for minutes."""
    http.on("/models", payload={"data": []})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    models(client)
    cache = client.app.state._model_discovery_cache
    for key, (_, disc) in list(cache.items()):
        cache[key] = (time.time() - 30.0, disc)
    first_round = len(http.requests)
    models(client)
    # Probed again (every flavour tried again), not served from the cache.
    assert len(http.requests) == 2 * first_round


def test_a_cached_discovery_result_is_served_without_a_probe(build, http):
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    disc = DiscoveryResult(
        attempted=True, ok=True, status=200, url="https://box.test/v1/models",
        flavour="openai", models=[{"id": "cached-one", "name": "cached-one"}],
    )
    client.app.state._model_discovery_cache = {
        "custom|https://box.test/v1|7:real": (time.time(), disc)
    }
    body = models(client)
    assert "cached-one" in ids(body)
    assert body["discovery"]["cached"] is True
    assert http.requests == []


def test_a_stale_shaped_cache_entry_is_ignored_not_crashed_on(build, http):
    """Entries from an older process layout must simply trigger a fresh probe."""
    http.on("/models", payload={"data": [{"id": "fresh"}]})
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    client.app.state._model_discovery_cache = {
        "custom|https://box.test/v1|7:real": (time.time(), [{"id": "cached-one"}])
    }
    body = models(client)
    assert "fresh" in ids(body)
    assert len(http.requests) >= 1


# --- /api/models: single-flight ----------------------------------------------


class _Ready:
    """An awaitable stand-in for an in-flight discovery future."""

    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def __await__(self):
        if False:  # pragma: no cover - makes this a generator, i.e. awaitable
            yield
        if self.error is not None:
            raise self.error
        return self.value


def test_a_probe_already_in_flight_is_joined_rather_than_repeated(build, http):
    """Boot and the status bar ask at once; one HTTP call must serve both."""
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    client.app.state._model_discovery_inflight = {
        "custom|https://box.test/v1|7:real": _Ready(
            DiscoveryResult(
                attempted=True, ok=True, status=200, url="x", flavour="openai",
                models=[{"id": "shared-probe", "name": "shared-probe"}],
            )
        )
    }
    body = models(client)
    assert "shared-probe" in ids(body)
    assert http.requests == []


def test_a_failed_in_flight_probe_degrades_to_the_catalog(build, http):
    client = build(runtime=Runtime("custom", "", "https://box.test/v1", "sk-real"))
    client.app.state._model_discovery_inflight = {
        "custom|https://box.test/v1|7:real": _Ready(error=RuntimeError("peer gave up"))
    }
    body = models(client)
    assert body["models"]
    assert http.requests == []


# --- /api/models: selection and shape ----------------------------------------


def test_the_response_names_the_provider_and_base_url_actually_used(build):
    client = build(runtime=Runtime("deepseek", "deepseek-v4-flash", None, ""))
    body = models(client)
    assert body["provider"] == "deepseek"
    assert body["base_url"] == "https://api.deepseek.com/v1"


def test_exactly_one_model_is_flagged_default_and_it_is_the_reported_one(build):
    client = build(runtime=Runtime("openai", "gpt-4o", None, ""))
    body = models(client)
    flagged = [m["id"] for m in body["models"] if m["default"]]
    assert flagged == [body["default"]] == ["gpt-4o"]


def test_a_configured_model_missing_from_the_list_is_added_at_the_top(build):
    """Otherwise the picker silently shows a model other than the one in use."""
    client = build(runtime=Runtime("custom", "my-local.gguf", "https://box.test/v1", ""))
    body = models(client)
    assert body["models"][0]["id"] == "my-local.gguf"
    assert body["default"] == "my-local.gguf"


def test_the_picker_is_never_empty(build, http):
    http.on("/models", payload={"data": []})
    client = build(runtime=Runtime("deepseek", "", None, "sk-real"))
    body = models(client)
    assert body["models"]
    assert body["default"]


def test_config_supplies_the_provider_when_there_is_no_runtime(build):
    client = build(cfg={"llm_provider": "xai", "llm_model": "grok-4"}, runtime=None)
    body = models(client)
    assert body["provider"] == "xai"
    assert body["default"] == "grok-4"


def test_the_environment_is_the_last_resort_for_the_provider(build, monkeypatch):
    monkeypatch.setenv("REMEDY_LLM_PROVIDER", "mistral")
    assert models(build(runtime=None))["provider"] == "mistral"


def test_with_nothing_configured_the_default_is_openai(build):
    body = models(build(runtime=None))
    assert body["provider"] == "openai"
    assert body["default"] == "gpt-4o-mini"


def test_runtime_settings_beat_the_config_file(build):
    client = build(
        cfg={"llm_provider": "openai", "llm_model": "gpt-4o"},
        runtime=Runtime("deepseek", "deepseek-v4-pro", None, ""),
    )
    assert models(client)["provider"] == "deepseek"


# --- /api/models: ?provider= switch -------------------------------------------


def test_a_provider_query_previews_without_switching_the_runtime(build):
    """The status-bar switcher previews another provider; runtime is untouched."""
    rt = Runtime("openai", "gpt-4o", None, "")
    body = models(build(runtime=rt), provider="deepseek")
    assert body["provider"] == "deepseek"
    assert body["base_url"] == "https://api.deepseek.com/v1"
    assert rt._llm_provider == "openai" and rt._llm_model == "gpt-4o"


def test_a_provider_query_prefers_the_last_model_used_for_that_provider(build):
    client = build(cfg={"last_model_by_provider": {"deepseek": "deepseek-v4-pro"}})
    assert models(client, provider="deepseek")["default"] == "deepseek-v4-pro"


def test_a_malformed_last_model_map_is_ignored(build):
    client = build(cfg={"last_model_by_provider": "not-a-dict"})
    assert models(client, provider="deepseek")["default"] == "deepseek-v4-flash"


@pytest.mark.parametrize("provider", ["nope", "  ", "", "deepseek-v4"])
def test_an_unknown_provider_query_is_ignored(build, provider):
    """It falls back to the active provider rather than 400-ing the picker."""
    client = build(runtime=Runtime("openai", "gpt-4o", None, ""))
    assert models(client, provider=provider)["provider"] == "openai"


@pytest.mark.parametrize("provider", ["DeepSeek", " deepseek ", "DEEPSEEK"])
def test_a_provider_query_is_case_and_space_insensitive(build, provider):
    client = build(runtime=Runtime("openai", "gpt-4o", None, ""))
    assert models(client, provider=provider)["provider"] == "deepseek"


def test_a_provider_query_uses_that_providers_stored_key(build, http, monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda cfg, prov: "sk-stored" if prov == "deepseek" else "sk-other",
    )
    http.on("/models", payload={"data": [{"id": "deepseek-r2"}]})
    client = build(runtime=Runtime("openai", "gpt-4o", None, "sk-active"))
    models(client, provider="deepseek")
    assert http.requests[0]["headers"]["Authorization"] == "Bearer sk-stored"


def test_a_failing_key_lookup_does_not_break_the_listing(build, monkeypatch):
    def boom(cfg, prov):
        raise RuntimeError("keyring locked")

    monkeypatch.setattr("remedy.interfaces.config.resolve_provider_api_key", boom)
    assert models(build(), provider="deepseek")["models"]


# --- /api/models: ollama ------------------------------------------------------


def test_ollama_tags_are_merged_and_the_latest_suffix_is_trimmed(build, http):
    http.on(
        "/api/tags",
        payload={"models": [{"name": "llama3:latest"}, {"name": "qwen3:8b"}]},
    )
    client = build(runtime=Runtime("ollama", "", None, ""))
    got = ids(models(client))
    assert "llama3" in got and "qwen3:8b" in got


def test_an_ollama_tag_without_a_name_is_skipped(build, http):
    http.on("/api/tags", payload={"models": [{}, {"name": ""}, {"name": "ok"}]})
    client = build(runtime=Runtime("ollama", "", None, ""))
    assert "ok" in ids(models(client))


def test_a_dead_ollama_daemon_does_not_fail_the_request(build, http):
    http.on("/api/tags", error=aiohttp.ClientConnectionError("no daemon"))
    client = build(runtime=Runtime("ollama", "", None, ""))
    assert models(client)["models"]
