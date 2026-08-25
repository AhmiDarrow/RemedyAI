"""GET /api/sessions/{session_id}/turns/{turn_id}/explain."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from remedy.events.bus import EventBus
from remedy.events.types import EventType
from remedy.interfaces.routes.sessions.explain import register_explain_routes
from remedy.models import ChatSession


class Memory:
    def __init__(self, sessions=None) -> None:
        self.sessions = {s.id: s for s in (sessions or [])}

    async def get_chat_session(self, session_id):
        return self.sessions.get(session_id)


def _client(*, memory=None) -> TestClient:
    app = FastAPI()
    register_explain_routes(app, memory=memory)
    return TestClient(app)


def test_explain_turn_returns_summary(monkeypatch, tmp_path):
    bus = EventBus(db_path=tmp_path / "events.db")
    monkeypatch.setattr("remedy.events.bus.default_bus", lambda: bus)
    bus.emit_simple(
        EventType.TOOL_COMPLETED, session_id="s1", turn_id="t1", tool="file_read"
    )
    bus.emit_simple(
        EventType.VERIFICATION_COMPLETED,
        session_id="s1",
        turn_id="t1",
        reason="file exists",
    )
    mem = Memory([ChatSession(id="s1", title="T", created_at=datetime.now(UTC))])
    r = _client(memory=mem).get("/api/sessions/s1/turns/t1/explain")
    assert r.status_code == 200
    body = r.json()
    assert "file_read" in body["what"]
    assert "file exists" in body["verified"]
    assert "why" in body and "remains" in body
    bus.close()


def test_explain_turn_404_when_no_events(monkeypatch, tmp_path):
    bus = EventBus(db_path=tmp_path / "events.db")
    monkeypatch.setattr("remedy.events.bus.default_bus", lambda: bus)
    mem = Memory([ChatSession(id="s1", title="T", created_at=datetime.now(UTC))])
    r = _client(memory=mem).get("/api/sessions/s1/turns/missing/explain")
    assert r.status_code == 404
    bus.close()


def test_explain_turn_404_unknown_session(monkeypatch, tmp_path):
    bus = EventBus(db_path=tmp_path / "events.db")
    monkeypatch.setattr("remedy.events.bus.default_bus", lambda: bus)
    bus.emit_simple(
        EventType.TOOL_COMPLETED, session_id="s1", turn_id="t1", tool="file_read"
    )
    r = _client(memory=Memory()).get("/api/sessions/nope/turns/t1/explain")
    assert r.status_code == 404
    bus.close()


def test_explain_turn_works_without_memory(monkeypatch, tmp_path):
    bus = EventBus(db_path=tmp_path / "events.db")
    monkeypatch.setattr("remedy.events.bus.default_bus", lambda: bus)
    bus.emit_simple(
        EventType.GOAL_FAILED, session_id="s1", turn_id="t2", reason="blocked"
    )
    r = _client(memory=None).get("/api/sessions/s1/turns/t2/explain")
    assert r.status_code == 200
    assert "blocked" in r.json()["remains"]
    bus.close()
