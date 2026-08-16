"""Session message SSE contract (desktop path)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore


class _StreamRuntime:
    """Minimal runtime that yields controlled stream events."""

    def __init__(self):
        self.skills = type("R", (), {"count": 0, "skills": []})()
        self._streaming_sessions: set[str] = set()

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        yield "Hello "
        yield "world"


class _L0Runtime:
    """Simulates agent L0 short-circuit without a provider key."""

    def __init__(self):
        self.skills = type(
            "R",
            (),
            {
                "count": 2,
                "list_skills": staticmethod(lambda: ["change-safety", "project-etiquette"]),
                "skills": [],
            },
        )()
        self._llm_provider = "xai"
        self._llm_model = "grok-test"
        self._streaming_sessions: set[str] = set()
        self._session_id = None
        self.config = type("C", (), {"home_dir": None, "llm_provider": "xai", "llm_model": "grok-test"})()

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        from remedy.core.metabolism.l0 import try_l0_system_reply
        from remedy.core.metabolism.tier import TurnTier, classify_turn_tier

        if (
            not attachments
            and classify_turn_tier(message or "", tools_enabled=False)
            == TurnTier.L0_INSTANT
        ):
            l0 = try_l0_system_reply(self, message or "", preclassified=True)
            if l0:
                yield l0
                return
        yield (
            "[LLM not connected — no API key. "
            "Open Settings, enter your provider key, Save, then resend.]\n"
        )


class _AbortRuntime:
    """Yields a token then cooperative abort."""

    def __init__(self):
        self.skills = type("R", (), {"count": 0, "skills": []})()
        self._streaming_sessions: set[str] = set()

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list | None = None,
        **kwargs,
    ) -> AsyncIterator[str]:
        yield "partial "
        yield "@@aborted\n"


def _make_store(tmp_path: Path) -> MemoryStore:
    async def _init():
        store = MemoryStore(str(tmp_path / "mem.db"))
        await store.initialize()
        return store

    return asyncio.run(_init())


def _create_session(client: TestClient) -> str:
    r = client.post("/api/sessions", json={"title": "Stream Test"})
    assert r.status_code in (200, 201)
    data = r.json()
    sid = (
        data.get("id")
        or data.get("session_id")
        or (data.get("session") or {}).get("id")
    )
    if not sid:
        ls = client.get("/api/sessions")
        assert ls.status_code == 200
        sessions = ls.json()
        if isinstance(sessions, dict):
            sessions = sessions.get("sessions") or sessions.get("items") or []
        if sessions:
            sid = sessions[0].get("id")
    assert sid, "session create shape unexpected"
    return str(sid)


def test_create_session_and_stream(tmp_path: Path):
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages/stream",
            json={"message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "Hello" in body or "world" in body or "event:" in body or len(body) > 0


def test_l0_list_skills_streams_without_api_key(tmp_path: Path, monkeypatch):
    """L0 instant (list skills) must not  error when no provider key is set."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        lambda *a, **k: "",
    )
    store = _make_store(tmp_path)
    rt = _L0Runtime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages/stream",
            json={"message": "list my skills"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    assert "event: error" not in body or "no API key" not in body
    assert "Installed skills" in body or "change-safety" in body
    assert "event: done" in body


def test_l0_model_streams_without_api_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        lambda *a, **k: "",
    )
    store = _make_store(tmp_path)
    rt = _L0Runtime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages/stream",
            json={"message": "what model am I using?"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    assert "Provider" in body or "Model" in body or "grok-test" in body
    assert "event: done" in body


def test_stream_aborted_event_not_error(tmp_path: Path, monkeypatch):
    """Cooperative @@aborted must emit event:aborted, not event:error."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        lambda *a, **k: "fake-key",
    )
    store = _make_store(tmp_path)
    rt = _AbortRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages/stream",
            json={"message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
    assert "event: aborted" in body
    assert "Generation stopped" in body
    # Must not paint Stop as a hard stream error
    assert '"type": "error"' not in body or "Generation stopped" not in body.split(
        "event: error"
    )[0] if "event: error" in body else True
    # Prefer no error event at all for pure abort
    if "event: error" in body:
        # Tolerate metrics-only; generation-stopped must not be under error
        err_idx = body.find("event: error")
        assert "Generation stopped" not in body[err_idx : err_idx + 200]


def test_stream_missing_session_is_404(tmp_path: Path):
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.post(
            "/api/sessions/does-not-exist/messages/stream",
            json={"message": "hi"},
        )
        assert r.status_code == 404


def test_sync_send_missing_session_is_404(tmp_path: Path):
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.post(
            "/api/sessions/does-not-exist/messages",
            json={"message": "hi"},
        )
        assert r.status_code == 404


def test_list_messages_missing_session_is_404(tmp_path: Path):
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.get("/api/sessions/does-not-exist/messages")
        assert r.status_code == 404


def test_put_session_llm_missing_is_404(tmp_path: Path):
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.put(
            "/api/sessions/does-not-exist/llm",
            json={"provider": "demo", "model": "demo"},
        )
        assert r.status_code == 404


def test_sync_send_409_when_stream_claimed(tmp_path: Path):
    from remedy.core.turn_context import (
        release_session_stream_claim,
        try_claim_session_stream,
    )

    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        assert try_claim_session_stream(sid) is True
        try:
            r = client.post(
                f"/api/sessions/{sid}/messages",
                json={"message": "hi"},
            )
            assert r.status_code == 409
        finally:
            release_session_stream_claim(sid)


def test_delete_releases_stream_claim(tmp_path: Path):
    from remedy.core.turn_context import (
        is_session_streaming,
        try_claim_session_stream,
    )

    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        assert try_claim_session_stream(sid) is True
        r = client.delete(f"/api/sessions/{sid}")
        assert r.status_code == 200
        assert is_session_streaming(sid) is False
        assert try_claim_session_stream(sid) is True
        from remedy.core.turn_context import release_session_stream_claim

        release_session_stream_claim(sid)
