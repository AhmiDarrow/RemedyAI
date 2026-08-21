"""Interrupted turns always leave a durable assistant row (Stop / supersede / disconnect)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.interfaces.routes.sessions.stream import (
    STOP_NOTE,
    SUPERSEDE_NOTE,
    interrupted_turn_content,
)
from remedy.memory.store import MemoryStore


class _AbortWithToolsRuntime:
    """Partial text, one tool call + result, then cooperative abort."""

    def __init__(self):
        self.skills = type("R", (), {"count": 0, "skills": []})()
        self._streaming_sessions: set[str] = set()

    async def stream_response(
        self, message: str, session_id: str | None = None, **kwargs
    ) -> AsyncIterator[str]:
        yield "Looking at the repo "
        yield '@@tool_call:{"name": "file_read", "args": {"path": "a.py"}}'
        yield '@@tool_result:{"name": "file_read", "preview": "print(1)", "ok": true}'
        yield "@@aborted\n"


class _SlowRuntime:
    """Streams forever (until cancelled) so the client can drop the connection."""

    def __init__(self):
        self.skills = type("R", (), {"count": 0, "skills": []})()
        self._streaming_sessions: set[str] = set()
        self.cancelled = asyncio.Event()

    async def stream_response(
        self, message: str, session_id: str | None = None, **kwargs
    ) -> AsyncIterator[str]:
        yield "half an answer "
        yield '@@tool_call:{"name": "list_dir", "args": {"path": "."}}'
        try:
            for _ in range(200):
                await asyncio.sleep(0.05)
                yield ""
        finally:
            self.cancelled.set()


def _make_store(tmp_path: Path) -> MemoryStore:
    async def _init():
        store = MemoryStore(str(tmp_path / "mem.db"))
        await store.initialize()
        return store

    return asyncio.run(_init())


def _create_session(client: TestClient) -> str:
    r = client.post("/api/sessions", json={"title": "Abort Persist"})
    assert r.status_code in (200, 201)
    data = r.json()
    sid = data.get("id") or data.get("session_id") or (data.get("session") or {}).get("id")
    assert sid
    return str(sid)


def _assistant_rows(client: TestClient, sid: str) -> list[dict]:
    r = client.get(f"/api/sessions/{sid}/messages")
    assert r.status_code == 200
    msgs = r.json()
    if isinstance(msgs, dict):
        msgs = msgs.get("messages") or msgs.get("items") or []
    return [m for m in msgs if m.get("role") == "assistant"]


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        lambda *a, **k: "fake-key",
    )


def test_interrupted_turn_content_wording():
    assert interrupted_turn_content("", None, has_tools=False) == STOP_NOTE
    assert interrupted_turn_content("", "supersede", has_tools=False) == SUPERSEDE_NOTE
    body = interrupted_turn_content("partial text", "supersede", has_tools=True)
    assert body.startswith("partial text")
    assert body.endswith(SUPERSEDE_NOTE)
    tools_only = interrupted_turn_content("", "stop", has_tools=True)
    assert "Used tools" in tools_only and tools_only.endswith(STOP_NOTE)


def test_stop_abort_persists_partial_text_and_tool_calls(tmp_path: Path, fake_key):
    store = _make_store(tmp_path)
    app = create_app(runtime=_AbortWithToolsRuntime(), memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST", f"/api/sessions/{sid}/messages/stream", json={"message": "look"}
        ) as resp:
            body = "".join(resp.iter_text())
        assert "event: aborted" in body
        rows = _assistant_rows(client, sid)
    assert len(rows) == 1, rows
    row = rows[0]
    assert "Looking at the repo" in row["content"]
    assert STOP_NOTE in row["content"]
    assert SUPERSEDE_NOTE not in row["content"]
    assert [c.get("name") for c in (row.get("tool_calls") or [])] == ["file_read"]
    assert [r.get("name") for r in (row.get("tool_results") or [])] == ["file_read"]


def test_supersede_abort_words_the_row_for_next_message(tmp_path: Path, fake_key):
    """POST /abort?reason=supersede before the turn dies → 'interrupted by your next message'."""
    from remedy.core.turn_context import set_abort_reason

    class _Rt(_AbortWithToolsRuntime):
        async def stream_response(self, message, session_id=None, **kwargs):
            yield "partial "
            # The desktop's send-while-busy path posts /abort?reason=supersede
            # while the turn is running; simulate the recorded verdict.
            set_abort_reason(str(session_id), "supersede")
            yield "@@aborted\n"

    store = _make_store(tmp_path)
    app = create_app(runtime=_Rt(), memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        with client.stream(
            "POST", f"/api/sessions/{sid}/messages/stream", json={"message": "go"}
        ) as resp:
            "".join(resp.iter_text())
        rows = _assistant_rows(client, sid)
        assert len(rows) == 1
        assert rows[0]["content"].startswith("partial")
        assert SUPERSEDE_NOTE in rows[0]["content"]

        # A fresh turn on the same session must not inherit the verdict.
        from remedy.core.turn_context import (
            peek_abort_reason,
            release_session_stream_claim,
            try_claim_session_stream,
        )

        assert try_claim_session_stream(sid)
        try:
            assert peek_abort_reason(sid) is None
        finally:
            release_session_stream_claim(sid)


def test_abort_endpoint_records_reason(tmp_path: Path, fake_key):
    from remedy.core.turn_context import (
        abort_session,
        normalize_abort_reason,
        peek_abort_reason,
    )

    assert normalize_abort_reason(None) == "stop"
    assert normalize_abort_reason("bogus") == "stop"
    assert normalize_abort_reason("supersede") == "supersede"
    abort_session("sess-reason-x", reason="supersede")
    assert peek_abort_reason("sess-reason-x") == "supersede"
    # Internal disconnect abort (reason=None) keeps the client's verdict.
    abort_session("sess-reason-x")
    assert peek_abort_reason("sess-reason-x") == "supersede"

    store = _make_store(tmp_path)
    app = create_app(runtime=_AbortWithToolsRuntime(), memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        r = client.post(f"/api/sessions/{sid}/abort?reason=supersede")
        assert r.status_code == 200
        assert r.json().get("reason") == "supersede"
        assert peek_abort_reason(sid) == "supersede"
        r = client.post(f"/api/sessions/{sid}/abort")
        assert r.json().get("reason") == "stop"


def _stream_endpoint(app):
    for route in app.router.routes:
        if getattr(route, "path", "") == "/api/sessions/{session_id}/messages/stream":
            return route.endpoint
    raise AssertionError("stream route not registered")


def test_client_disconnect_mid_turn_does_not_stop_the_job(tmp_path: Path, fake_key):
    """A dropped SSE is not Stop: the ReAct worker keeps going until /abort."""
    from remedy.core.turn_context import abort_session
    from remedy.interfaces.api_models import SendMessageRequest

    store = _make_store(tmp_path)
    rt = _SlowRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)

    endpoint = _stream_endpoint(app)

    async def _drive():
        resp = await endpoint(sid, SendMessageRequest(message="slow"))
        it = resp.body_iterator
        seen = ""
        async for chunk in it:
            seen += chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
            if "list_dir" in seen:
                break
        await it.aclose()
        await asyncio.sleep(0.15)
        after_drop = [
            m for m in await store.get_chat_messages(sid) if m.role.value == "assistant"
        ]
        still_running = not rt.cancelled.is_set()
        abort_session(sid, reason="stop")
        rows = []
        for _ in range(100):
            rows = [
                m for m in await store.get_chat_messages(sid) if m.role.value == "assistant"
            ]
            if rows:
                break
            await asyncio.sleep(0.02)
        return after_drop, still_running, rows

    after_drop, still_running, rows = asyncio.run(_drive())
    assert after_drop == []
    assert still_running is True
    assert len(rows) == 1, rows
    assert "half an answer" in rows[0].content
    assert STOP_NOTE in rows[0].content
    assert [c.get("name") for c in (rows[0].tool_calls or [])] == ["list_dir"]


def test_asgi_cancel_mid_turn_persists_partial_row(tmp_path: Path, fake_key):
    """Task cancellation (uvicorn on connection reset) takes the CancelledError
    branch — row still lands, with a superseding verdict when one was recorded."""
    from remedy.core.turn_context import set_abort_reason
    from remedy.interfaces.api_models import SendMessageRequest

    store = _make_store(tmp_path)
    rt = _SlowRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
    endpoint = _stream_endpoint(app)

    async def _drive():
        resp = await endpoint(sid, SendMessageRequest(message="slow"))

        async def _consume():
            async for _ in resp.body_iterator:
                pass

        task = asyncio.ensure_future(_consume())
        await asyncio.sleep(0.3)
        set_abort_reason(sid, "supersede")
        from remedy.core.turn_context import abort_session as _abort

        _abort(sid, reason="supersede")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        for _ in range(100):
            rows = [
                m for m in await store.get_chat_messages(sid) if m.role.value == "assistant"
            ]
            if rows:
                return rows
            await asyncio.sleep(0.02)
        return []

    rows = asyncio.run(_drive())
    assert len(rows) == 1, rows
    assert "half an answer" in rows[0].content
    assert SUPERSEDE_NOTE in rows[0].content
    assert [c.get("name") for c in (rows[0].tool_calls or [])] == ["list_dir"]
