"""In-place full session reset: empty slate, same session id."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from remedy.core.session_quality import get_session_quality
from remedy.core.session_reset import full_reset_session
from remedy.interfaces.api_support import handle_slash_command
from remedy.memory.store import MemoryStore
from remedy.models import ChatMessage, ChatMessageRole, ChatSession, MemoryEntry, MemoryEntryType


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "reset_session.db"
    s = MemoryStore(db)
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


@pytest.mark.asyncio
async def test_clear_chat_messages_keeps_session(store: MemoryStore):
    sid = str(uuid4())
    await store.create_chat_session(
        ChatSession(
            id=sid,
            title="My work",
            project_path=r"C:\Work\App",
            message_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    for i in range(3):
        await store.add_chat_message(
            ChatMessage(
                id=str(uuid4()),
                session_id=sid,
                role=ChatMessageRole.USER if i % 2 == 0 else ChatMessageRole.ASSISTANT,
                content=f"msg {i}",
                created_at=datetime.now(UTC),
            )
        )
    before = await store.get_chat_session(sid)
    assert before is not None
    assert before.message_count == 3

    deleted = await store.clear_chat_messages(sid)
    assert deleted == 3

    sess = await store.get_chat_session(sid)
    assert sess is not None
    assert sess.id == sid
    assert sess.title == "My work"
    assert sess.project_path == r"C:\Work\App"
    assert sess.message_count == 0
    assert await store.get_chat_messages(sid) == []


@pytest.mark.asyncio
async def test_full_reset_wipes_context_keeps_session(store: MemoryStore, tmp_path: Path):
    sid = str(uuid4())
    home = tmp_path / "home"
    home.mkdir()
    await store.create_chat_session(
        ChatSession(
            id=sid,
            title="Deep work",
            project_path=r"C:\Work\App",
            message_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await store.add_chat_message(
        ChatMessage(
            id=str(uuid4()),
            session_id=sid,
            role=ChatMessageRole.USER,
            content="remember this context",
            created_at=datetime.now(UTC),
        )
    )
    await store.upsert(
        MemoryEntry(
            title="sess note",
            content="only for this session",
            entry_type=MemoryEntryType.NOTE,
            session_id=sid,
        )
    )
    # Plan + attachment on disk
    plans = home / "plans"
    plans.mkdir(parents=True)
    plans.joinpath("planreset1.json").write_text(
        json.dumps(
            {
                "id": "planreset1",
                "title": "T",
                "goal": "G",
                "session_id": sid,
                "status": "draft",
                "steps": [],
                "risks": [],
                "created_at": datetime.now(UTC).isoformat(),
                "updated_at": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    att = home / "attachments" / sid.replace(":", "_")[:80]
    att.mkdir(parents=True)
    (att / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    get_session_quality(sid).record_tool_result(success=True)

    class FakeBrief:
        def __init__(self) -> None:
            self.session_id = sid

    class FakeRuntime:
        def __init__(self) -> None:
            self._session_id = sid
            self._session_brief = FakeBrief()
            self._streaming_sessions = {sid}
            # Session-keyed + foreign entry (must not clear other tabs)
            self._tasks = {
                "mine": {"session_id": sid, "goal": "x"},
                "other": {"session_id": "other-tab", "goal": "y"},
            }
            self.config = type("C", (), {"home_dir": str(home)})()

    rt = FakeRuntime()
    stats = await full_reset_session(sid, store, runtime=rt, home_dir=home)
    assert stats["ok"] is True
    assert stats["messages"] == 1
    assert stats.get("plans", 0) >= 1
    assert stats.get("attachments_purged") is True
    assert await store.get_chat_messages(sid) == []
    sess = await store.get_chat_session(sid)
    assert sess is not None
    assert sess.id == sid
    assert sess.title == "New Session"
    assert sess.project_path == r"C:\Work\App"
    assert rt._session_brief is None
    assert sid not in rt._streaming_sessions
    assert "mine" not in rt._tasks
    assert "other" in rt._tasks  # other tab preserved
    assert not (plans / "planreset1.json").is_file()
    assert not att.is_dir() or not any(att.iterdir())
    # Session-scoped notes gone
    left = await store.list_by_session(sid, limit=50)
    assert left == []


@pytest.mark.asyncio
async def test_slash_reset_stays_on_session(store: MemoryStore):
    sid = str(uuid4())
    await store.create_chat_session(
        ChatSession(
            id=sid,
            title="T",
            message_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    await store.add_chat_message(
        ChatMessage(
            id=str(uuid4()),
            session_id=sid,
            role=ChatMessageRole.USER,
            content="hello",
            created_at=datetime.now(UTC),
        )
    )

    result = await handle_slash_command("/reset", sid, store)
    assert result.get("action") == "reset_session"
    assert "reset" in result.get("text", "").lower() or result.get("cleared") == 1
    assert await store.get_chat_messages(sid) == []
    # Same session still exists — not a new_session action
    assert result.get("action") != "new_session"
    still = await store.get_chat_session(sid)
    assert still is not None
    assert still.id == sid
    assert still.title == "New Session"

    # Alias
    await store.add_chat_message(
        ChatMessage(
            id=str(uuid4()),
            session_id=sid,
            role=ChatMessageRole.USER,
            content="again",
            created_at=datetime.now(UTC),
        )
    )
    r2 = await handle_slash_command("/clear", sid, store)
    assert r2.get("action") == "reset_session"
    assert await store.get_chat_messages(sid) == []
