"""Per-session provider+model bind resolution."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.session_llm import resolve_session_llm_bind, session_llm_update_fields


def test_rmb_live_reload_refused_while_any_stream_claimed(tmp_path, monkeypatch):
    import asyncio

    from fastapi.testclient import TestClient

    from remedy.core.agent import BasicRuntime
    from remedy.core.turn_context import (
        release_session_stream_claim,
        try_claim_session_stream,
    )
    from remedy.interfaces.api import create_app
    from remedy.memory.store import MemoryStore
    from remedy.models import AgentConfig, ChatSession

    async def _prep():
        store = MemoryStore(str(tmp_path / "llm.db"))
        await store.initialize()
        sess = await store.create_chat_session(ChatSession(title="rmb"))
        return store, sess.id

    store, sid = asyncio.run(_prep())
    cfg = AgentConfig(
        name="t",
        project_path="",
        llm_provider="rmb",
        llm_model="local",
        llm_api_key="x",
        llm_base_url="http://127.0.0.1:8787/v1",
        home_dir=str(tmp_path),
    )
    rt = BasicRuntime(cfg, memory=store)
    called: list[int] = []
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.apply_rmb_chat_model",
        lambda *a, **k: called.append(1) or {"ok": True, "model_path": "x.gguf"},
    )
    app = create_app(runtime=rt, memory=store, api_key="")
    other = "other-streaming-tab"
    assert try_claim_session_stream(other) is True
    try:
        with TestClient(app) as client:
            r = client.put(
                f"/api/sessions/{sid}/llm",
                json={"provider": "rmb", "model": "other-gguf"},
            )
            assert r.status_code == 409, r.text
            assert called == []
    finally:
        release_session_stream_claim(other)


def test_rmb_settings_and_start_409_while_stream_claimed(tmp_path, monkeypatch):
    """Settings/start must not restart llama-server under a live stream."""
    from fastapi.testclient import TestClient

    from remedy.core.agent import BasicRuntime
    from remedy.core.turn_context import (
        release_session_stream_claim,
        try_claim_session_stream,
    )
    from remedy.interfaces.api import create_app
    from remedy.models import AgentConfig

    cfg = AgentConfig(
        name="t",
        project_path="",
        llm_provider="rmb",
        llm_model="local",
        llm_api_key="x",
        llm_base_url="http://127.0.0.1:8787/v1",
        home_dir=str(tmp_path),
    )
    rt = BasicRuntime(cfg, memory=None)
    live_calls: list[bool] = []

    def _apply(patch, **kwargs):
        live_calls.append(bool(kwargs.get("live", True)))
        return {"ok": True, "saved": True}

    monkeypatch.setattr("remedy.runtime.rmb.service.apply_rmb_settings", _apply)
    app = create_app(runtime=rt, memory=None, api_key="")
    other = "streaming-tab"
    assert try_claim_session_stream(other) is True
    try:
        with TestClient(app) as client:
            r = client.post("/api/rmb/settings", json={"ctx_size": 16384})
            assert r.status_code == 409, r.text
            assert live_calls == [False]
            r2 = client.post("/api/rmb/start")
            assert r2.status_code == 409, r2.text
            r3 = client.post("/api/rmb/use")
            assert r3.status_code == 409, r3.text
    finally:
        release_session_stream_claim(other)


def test_put_session_llm_route_is_registered():
    """Desktop PUT /sessions/{id}/llm must not 404 (handler was previously unregistered)."""
    from remedy.interfaces.api import create_app

    app = create_app(runtime=None, memory=None, api_key="")
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/sessions/{session_id}/llm" in paths


def test_explicit_req_pair_wins():
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(
        session=sess, req_provider="deepseek", req_model="deepseek-v4-flash"
    )
    assert p == "deepseek"
    assert m == "deepseek-v4-flash"


def test_sticky_session_ignores_model_only_foreign_id():
    """Global UI may send deepseek model while this tab is still Grok-bound."""
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(
        session=sess, req_provider=None, req_model="deepseek-v4-flash"
    )
    # Keep sticky Grok bind — do not cross-wire to DeepSeek from model alone.
    assert p == "xai"
    assert m == "grok-4.5"


def test_sticky_session_keeps_pair_when_no_req():
    sess = SimpleNamespace(llm_provider="xai", model="grok-4.5")
    p, m = resolve_session_llm_bind(session=sess, req_provider=None, req_model=None)
    assert p == "xai"
    assert m == "grok-4.5"


def test_infer_provider_from_model_only():
    p, m = resolve_session_llm_bind(
        session=None, req_provider=None, req_model="deepseek-v4-flash"
    )
    assert p == "deepseek"
    assert m == "deepseek-v4-flash"


def test_resolve_does_not_persist_live_rmb_stem(monkeypatch):
    """Turn address may use the loaded GGUF; persist must keep the tab bind."""
    sess = SimpleNamespace(llm_provider="rmb", model="my-7b")
    monkeypatch.setattr(
        "remedy.runtime.rmb.config.merged_state_cached",
        lambda *a, **k: {"model_path": "C:/models/host-70b.gguf"},
    )
    live_p, live_m = resolve_session_llm_bind(
        session=sess, req_provider=None, req_model=None, use_live_rmb=True
    )
    persist_p, persist_m = resolve_session_llm_bind(
        session=sess, req_provider=None, req_model=None, use_live_rmb=False
    )
    assert live_p == "rmb"
    assert live_m == "host-70b"
    assert persist_p == "rmb"
    assert persist_m == "my-7b"


def test_session_llm_update_fields_pairs():
    f = session_llm_update_fields(provider=None, model="grok-4.5")
    assert f["llm_provider"] == "xai"
    assert f["model"] == "grok-4.5"


def test_binding_for_session_does_not_mutate_runtime():
    from types import SimpleNamespace

    from remedy.interfaces.api_support import binding_for_session

    rt = SimpleNamespace(
        _llm_provider="openai",
        _llm_model="gpt-4o-mini",
        _llm_base_url="https://api.openai.com/v1",
        _llm_api_key="sk-keep",
    )
    bind = binding_for_session("xai", "grok-4.5", runtime=rt)
    assert bind.provider == "xai"
    assert "grok" in (bind.model or "").lower()
    assert rt._llm_provider == "openai"
    assert rt._llm_model == "gpt-4o-mini"
    assert rt._llm_api_key == "sk-keep"


def test_resolve_llm_slot_falls_back_to_runtime_key(monkeypatch):
    """CI / clean home: Settings has no key; AgentConfig key must still bind."""
    from remedy.interfaces import api_support

    monkeypatch.setattr(api_support, "_load_config_cached", lambda: {})
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda _cfg, _provider: "",
    )
    for key in ("REMEDY_LLM_API_KEY", "REMEDY_LLM_PROVIDER", "REMEDY_LLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    rt = SimpleNamespace(
        _llm_provider="openai",
        _llm_model="gpt-test",
        _llm_base_url="http://127.0.0.1:9/v1",
        _llm_api_key="sk-test-runtime",
    )
    _p, _m, _url, key = api_support.resolve_llm_slot(runtime=rt)
    assert key == "sk-test-runtime"
    _p, _m, _url, key = api_support.resolve_llm_slot(
        provider_override="xai",
        model_override="grok-4.5",
        runtime=rt,
    )
    assert key == "sk-test-runtime"
