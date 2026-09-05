"""Session stream HTTP is Go-owned; keep TestClient CRUD/message guards.

Production ``POST /api/sessions/{id}/messages/stream`` lives in
``native/go/httpapi`` (``stream.go`` / ``stream_test.go``) via
CognitionTurnRunner. The FastAPI twin is gone.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore


class _StreamRuntime:
    """Minimal runtime stub for non-stream session route guards."""

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


def test_session_stream_http_absent_from_testclient():
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    assert "/api/sessions/{session_id}/messages/stream" not in paths


def test_stream_missing_session_is_404(tmp_path: Path):
    """Absent FastAPI twin → unmatched path (404/405), not a Python 404 body."""
    store = _make_store(tmp_path)
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        r = client.post(
            "/api/sessions/does-not-exist/messages/stream",
            json={"message": "hi"},
        )
        assert r.status_code in (404, 405)


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
