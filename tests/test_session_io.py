"""Tests for plain-text session export / import."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.memory.session_io import (
    format_session_markdown,
    format_session_txt,
    parse_session_text,
)
from remedy.memory.store import MemoryStore
from remedy.models import ChatMessage, ChatMessageRole, ChatSession


def test_roundtrip_native_txt():
    messages = [
        {"role": "user", "content": "Hello\nworld", "model": None, "agent": None},
        {
            "role": "assistant",
            "content": "Hi there.",
            "model": "grok-3",
            "agent": "default",
            "created_at": "2026-07-24T12:00:00",
        },
    ]
    text = format_session_txt(
        title="Demo Chat",
        session_id="abc-123",
        messages=messages,
        model="grok-3",
        agent="default",
    )
    assert text.startswith("# Remedy Session")
    assert "===== USER =====" in text
    assert "===== ASSISTANT =====" in text
    assert "Hello\nworld" in text

    parsed = parse_session_text(text)
    assert parsed.title == "Demo Chat"
    assert parsed.model == "grok-3"
    assert len(parsed.messages) == 2
    assert parsed.messages[0].role == "user"
    assert parsed.messages[0].content == "Hello\nworld"
    assert parsed.messages[1].role == "assistant"
    assert parsed.messages[1].content == "Hi there."


def test_parse_legacy_markdown():
    md = format_session_markdown(
        title="Old Export",
        session_id="sid-1",
        messages=[
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1", "model": "m1"},
        ],
    )
    parsed = parse_session_text(md)
    assert parsed.title == "Old Export"
    assert len(parsed.messages) == 2
    assert parsed.messages[0].content == "Q1"
    assert parsed.messages[1].content == "A1"


def test_parse_freeform():
    parsed = parse_session_text("# My Notes\n\nJust some text.")
    assert parsed.title == "My Notes"
    assert len(parsed.messages) == 1
    assert parsed.messages[0].role == "user"
    assert "Just some text" in parsed.messages[0].content


def test_empty_raises():
    with pytest.raises(ValueError):
        parse_session_text("   \n  ")


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "test.db")
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


@pytest.mark.asyncio
async def test_store_import_via_format(store: MemoryStore):
    text = format_session_txt(
        title="Roundtrip",
        session_id="x",
        messages=[
            {"role": "user", "content": "ping"},
            {"role": "assistant", "content": "pong"},
        ],
    )
    parsed = parse_session_text(text)
    session = ChatSession(title=parsed.title)
    saved = await store.create_chat_session(session)
    for pm in parsed.messages:
        await store.add_chat_message(
            ChatMessage(
                session_id=saved.id,
                role=ChatMessageRole(pm.role),
                content=pm.content,
            )
        )
    msgs = await store.get_chat_messages(saved.id, limit=100)
    assert len(msgs) == 2
    assert msgs[0].content == "ping"
    assert msgs[1].content == "pong"


@pytest.fixture
def client(tmp_path: Path):
    s = MemoryStore(tmp_path / "api.db")
    asyncio.run(s.initialize())
    app = create_app(memory=s)
    with TestClient(app) as c:
        yield c
    asyncio.run(s.close())


def test_api_export_import_txt(client: TestClient):
    # Create session + messages through store is hard; use import then export.
    sample = format_session_txt(
        title="API Session",
        session_id="n/a",
        messages=[
            {"role": "user", "content": "hello api"},
            {"role": "assistant", "content": "hi api"},
        ],
        model="test-model",
    )
    r = client.post("/api/sessions/import", json={"text": sample})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "API Session"
    assert body["imported_messages"] == 2
    sid = body["id"]

    r2 = client.get(f"/api/sessions/{sid}/export?format=txt")
    assert r2.status_code == 200
    data = r2.json()
    assert data["format"] == "txt"
    assert data["filename"].endswith(".txt")
    assert "===== USER =====" in data["text"]
    assert "hello api" in data["text"]
    assert "markdown" in data  # backward-compat key

    r3 = client.get(f"/api/sessions/{sid}/export?format=md")
    assert r3.status_code == 200
    assert r3.json()["format"] == "md"
    assert r3.json()["filename"].endswith(".md")

    # Re-import exported body → new session
    r4 = client.post("/api/sessions/import", json={"text": data["text"]})
    assert r4.status_code == 200
    assert r4.json()["imported_messages"] == 2
    assert r4.json()["id"] != sid
