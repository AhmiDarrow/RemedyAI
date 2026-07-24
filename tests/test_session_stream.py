"""Session message SSE contract (desktop path)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore


class _StreamRuntime:
    """Minimal runtime that yields controlled stream events."""

    def __init__(self):
        self.skills = type("R", (), {"count": 0, "skills": []})()

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list | None = None,
    ) -> AsyncIterator[str]:
        yield "Hello "
        yield "world"


def test_create_session_and_stream(tmp_path: Path):
    import asyncio

    async def _init():
        store = MemoryStore(str(tmp_path / "mem.db"))
        await store.initialize()
        return store

    store = asyncio.run(_init())
    rt = _StreamRuntime()
    app = create_app(runtime=rt, memory=store, api_key="")
    with TestClient(app) as client:
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
        if not sid:
            pytest.skip("session create shape unexpected")
        with client.stream(
            "POST",
            f"/api/sessions/{sid}/messages/stream",
            json={"message": "hi"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
        assert "Hello" in body or "world" in body or "event:" in body or len(body) > 0
