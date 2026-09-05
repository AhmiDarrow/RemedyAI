"""Abort reason / epoch guards on TestClient; stream persist is Go-owned.

Interrupted-turn durable rows and SSE abort wording live in
``native/go/httpapi/stream.go`` (+ ``stream_test.go``). FastAPI
``/messages/stream`` is gone; keep ``POST /abort`` + turn_context coverage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore


class _AbortWithToolsRuntime:
    """Stub runtime for abort endpoint tests (no stream route needed)."""

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


@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.api_support._sync_runtime_llm_from_config",
        lambda *a, **k: "fake-key",
    )


def test_session_stream_http_absent_from_testclient():
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    assert "/api/sessions/{session_id}/messages/stream" not in paths


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


def test_abort_stale_epoch_does_not_kill_a_newer_turn(tmp_path: Path, fake_key):
    """Family: old Stop, current Stop, omit-epoch CLI still works."""
    from remedy.core.turn_context import (
        begin_turn,
        end_turn,
        is_turn_aborted,
        release_session_stream_claim,
        stream_claim_epoch,
        try_claim_session_stream,
    )

    store = _make_store(tmp_path)
    app = create_app(runtime=_AbortWithToolsRuntime(), memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        assert try_claim_session_stream(sid)
        e1 = stream_claim_epoch(sid)
        release_session_stream_claim(sid, epoch=e1)
        assert try_claim_session_stream(sid)
        e2 = stream_claim_epoch(sid)
        assert e2 != e1
        toks = begin_turn(sid, project_raw=None, active_path=".")
        try:
            stale = client.post(
                f"/api/sessions/{sid}/abort?reason=stop&epoch={e1}"
            )
            assert stale.status_code == 200
            body = stale.json()
            assert body.get("status") == "ignored"
            assert body.get("notified") == 0
            assert is_turn_aborted() is False

            live = client.post(
                f"/api/sessions/{sid}/abort?reason=stop&epoch={e2}"
            )
            assert live.status_code == 200
            assert live.json().get("status") == "aborted"
            assert live.json().get("notified") == 1
            assert is_turn_aborted() is True
        finally:
            end_turn(sid, *toks)
            release_session_stream_claim(sid, epoch=e2)


def test_abort_without_epoch_still_stops_current(tmp_path: Path, fake_key):
    """CLI / delete omit epoch — abort whatever is current (back-compat)."""
    from remedy.core.turn_context import (
        begin_turn,
        end_turn,
        is_turn_aborted,
        release_session_stream_claim,
        stream_claim_epoch,
        try_claim_session_stream,
    )

    store = _make_store(tmp_path)
    app = create_app(runtime=_AbortWithToolsRuntime(), memory=store, api_key="")
    with TestClient(app) as client:
        sid = _create_session(client)
        assert try_claim_session_stream(sid)
        epoch = stream_claim_epoch(sid)
        toks = begin_turn(sid, project_raw=None, active_path=".")
        try:
            r = client.post(f"/api/sessions/{sid}/abort")
            assert r.status_code == 200
            assert r.json().get("status") == "aborted"
            assert r.json().get("notified") == 1
            assert is_turn_aborted() is True
        finally:
            end_turn(sid, *toks)
            release_session_stream_claim(sid, epoch=epoch)
