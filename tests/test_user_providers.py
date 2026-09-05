"""Saved custom endpoints become providers of their own.

Everything runs against a temp REMEDY_HOME; no sockets are opened (discovery
is replaced with a stub). HTTP ``/api/providers*`` is Go-owned.
"""
from __future__ import annotations

import tomllib

import pytest
from fastapi import FastAPI

from remedy.interfaces import api_support
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    classify_provider_connection,
    normalize_llm_settings,
    provider_credentials_ready,
    public_provider_catalog,
    validate_provider_model,
)
from remedy.interfaces.model_discovery import DiscoveryResult
from remedy.interfaces.user_providers import (
    provider_id_for,
    remove_spec,
    slugify,
    specs_from_config,
    sync_catalog,
    upsert_spec,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / ".remedy"))
    api_support.invalidate_config_cache()
    sync_catalog({})
    yield tmp_path / ".remedy"
    sync_catalog({})
    api_support.invalidate_config_cache()


@pytest.fixture()
def discovery(monkeypatch):
    state = {"result": DiscoveryResult(attempted=True, ok=False, error="down")}

    async def fake(base_url, api_key="", **kw):
        state["calls"] = state.get("calls", 0) + 1
        state["last"] = (base_url, api_key, kw.get("provider_hint"))
        return state["result"]

    state["fake"] = fake
    monkeypatch.setattr("remedy.interfaces.model_discovery.discover_models", fake)
    return state


# --- pure helpers -------------------------------------------------------------


def test_ids_are_slugged_and_never_collide_with_builtins():
    assert slugify("My GPU Box!") == "my-gpu-box"
    assert provider_id_for("OpenAI") == "custom-openai"
    assert provider_id_for("x", {"custom-x"}) == "custom-x-2"


def test_upsert_and_remove_keep_the_catalog_in_step():
    cfg, pid = upsert_spec({}, name="GPU box", base_url="http://gpu:8000/v1/")
    assert pid == "custom-gpu-box"
    meta = PROVIDER_CATALOG[pid]
    assert meta["user_defined"] is True
    assert meta["base_url"] == "http://gpu:8000/v1"
    assert meta["label"] == "GPU box"
    assert meta["auth"] == ["api_key"]
    assert meta["models"] == []
    cfg = remove_spec(cfg, pid)
    assert pid not in PROVIDER_CATALOG
    assert specs_from_config(cfg) == {}


def test_hand_edited_config_is_tolerated():
    specs = specs_from_config(
        {
            "custom_providers": {
                "custom-ok": {"base_url": "http://a/v1", "flavour": "weird"},
                "custom-no-url": {"label": "nope"},
                "openai": {"base_url": "http://evil/v1"},  # not a user id
            }
        }
    )
    assert list(specs) == ["custom-ok"]
    assert specs["custom-ok"]["flavour"] == "openai"
    assert specs["custom-ok"]["label"] == "ok"


# --- persistence --------------------------------------------------------------


def test_specs_round_trip_through_config_toml(home):
    cfg, pid = upsert_spec(
        {"llm_provider": "openai"},
        name="Work proxy",
        base_url="https://proxy.corp/v1",
        flavour="anthropic",
        auth="none",
    )
    path = api_support._default_config_path()
    api_support._write_config(path, cfg)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    assert raw["custom_providers"][pid] == {
        "label": "Work proxy",
        "base_url": "https://proxy.corp/v1",
        "flavour": "anthropic",
        "auth": "none",
    }
    # A fresh load registers it in the catalog.
    sync_catalog({})
    assert pid not in PROVIDER_CATALOG
    loaded = api_support.load_config()
    assert pid in PROVIDER_CATALOG
    assert loaded["custom_providers"][pid]["flavour"] == "anthropic"


# --- behaviour of a saved endpoint across the system ---------------------------


def test_saved_endpoint_is_flexible_connected_and_ready():
    cfg, pid = upsert_spec({}, name="Local box", base_url="http://127.0.0.1:1234/v1", auth="none")
    # Any model id the host serves is fine; nothing snaps to a catalog default.
    assert validate_provider_model(pid, "qwen2.5-coder-7b") == "qwen2.5-coder-7b"
    prov, mid, url = normalize_llm_settings(pid, "claude-opus-5", None)
    assert (prov, mid, url) == (pid, "claude-opus-5", "http://127.0.0.1:1234/v1")
    ok, reason = classify_provider_connection(pid, cfg=cfg, keys={}, keys_set={})
    assert ok and reason == "saved_endpoint"
    assert provider_credentials_ready({**cfg, "llm_provider": pid}) is True


def test_keyed_endpoint_needs_its_key():
    cfg, pid = upsert_spec({}, name="Paid proxy", base_url="https://p.example/v1")
    ok, _ = classify_provider_connection(pid, cfg=cfg, keys={}, keys_set={})
    assert ok is False
    ok, reason = classify_provider_connection(pid, cfg=cfg, keys={pid: "sk"}, keys_set={})
    assert ok and reason == "api_key"


def test_adapter_follows_the_saved_flavour():
    from remedy.core.providers import (
        AnthropicProvider,
        LlamaCppProvider,
        OpenAIProvider,
        get_provider,
    )

    _, a = upsert_spec({}, name="A", base_url="https://a/v1", flavour="anthropic")
    assert isinstance(get_provider(a), AnthropicProvider)
    _, o = upsert_spec({}, name="O", base_url="http://o:11434/v1", flavour="ollama")
    assert isinstance(get_provider(o), LlamaCppProvider)
    _, d = upsert_spec({}, name="D", base_url="https://d/v1")
    assert isinstance(get_provider(d), OpenAIProvider)


# --- helpers (HTTP /api/providers* is Go-owned) --------------------------------



def test_upsert_stores_a_provider_and_secret_under_its_id(discovery, home):
    from remedy.interfaces.secret_store import get_provider_secret, set_provider_secret

    discovery["result"] = DiscoveryResult(
        attempted=True, ok=True, status=200, url="u", flavour="lmstudio",
        models=[{"id": "qwen2.5", "name": "qwen2.5", "chat": True}, {"id": "emb", "chat": False}],
    )
    cfg, pid = upsert_spec(
        {},
        name="LM Studio",
        base_url="http://127.0.0.1:1234/v1/",
        flavour="openai",
    )
    assert pid == "custom-lm-studio"
    set_provider_secret(pid, "lm-secret", home=home)
    assert PROVIDER_CATALOG[pid]["user_defined"] is True
    assert PROVIDER_CATALOG[pid]["label"] == "LM Studio"
    assert get_provider_secret(pid, home=home) == "lm-secret"
    assert not get_provider_secret("custom", home=home)
    sync_catalog(cfg)
    listed = {p["id"]: p for p in public_provider_catalog(cfg)}
    assert pid in listed
    assert listed["custom"]["base_url"] == PROVIDER_CATALOG["custom"]["base_url"]


def test_upsert_without_a_key_saves_a_keyless_endpoint(discovery):
    cfg, pid = upsert_spec(
        {},
        name="Home server",
        base_url="http://192.168.1.9:8080/v1",
        auth="none",
    )
    assert PROVIDER_CATALOG[pid]["auth"] == ["none"]
    sync_catalog(cfg)


def test_upsert_can_replace_an_existing_saved_endpoint(discovery):
    cfg, first = upsert_spec({}, name="Box", base_url="http://a/v1")
    cfg, again = upsert_spec(
        cfg, name="Box", base_url="http://b/v1", flavour="anthropic", pid=first
    )
    assert again == first
    assert PROVIDER_CATALOG[first]["base_url"] == "http://b/v1"
    assert PROVIDER_CATALOG[first]["flavour"] == "anthropic"
    cfg, keyed = upsert_spec(cfg, name="Keyed", base_url="http://k/v1", auth="api_key")
    assert PROVIDER_CATALOG[keyed]["auth"] == ["api_key"]
    cfg, keyed2 = upsert_spec(
        cfg, name="Keyed", base_url="http://k2/v1", pid=keyed, auth="api_key"
    )
    assert keyed2 == keyed
    assert PROVIDER_CATALOG[keyed]["auth"] == ["api_key"]
    cfg, keyed3 = upsert_spec(
        cfg, name="Keyed", base_url="http://k2/v1", pid=keyed, auth="none"
    )
    assert keyed3 == keyed
    assert PROVIDER_CATALOG[keyed]["auth"] == ["none"]
    sync_catalog(cfg)
    ids = [p["id"] for p in public_provider_catalog(cfg)]
    assert ids.count(first) == 1


def test_bad_upsert_urls_are_rejected(discovery):
    with pytest.raises(ValueError):
        upsert_spec({}, name="x", base_url="")
    with pytest.raises(ValueError):
        upsert_spec({}, name="", base_url="http://a/v1")


def test_delete_removes_the_provider_and_its_key(discovery, home):
    from remedy.interfaces.secret_store import (
        clear_provider_secret,
        get_provider_secret,
        set_provider_secret,
    )

    cfg, pid = upsert_spec({}, name="Gone", base_url="http://g/v1", auth="api_key")
    set_provider_secret(pid, "k", home=home)
    cfg = remove_spec(cfg, pid)
    clear_provider_secret(pid, home=home)
    assert pid not in PROVIDER_CATALOG
    sync_catalog(cfg)
    ids = [p["id"] for p in public_provider_catalog(cfg)]
    assert pid not in ids
    assert not get_provider_secret(pid, home=home)

# test_models_route_discovers_against_the_saved_url: /api/models is Go-owned
# (providers.go / providers_test.go).
