"""Tests for the memory store with FTS5 and handoff support."""

import asyncio
from datetime import UTC

import pytest

from remedy.memory.store import MemoryStore
from remedy.models import (
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    SessionSummary,
)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_memory.db"
    s = MemoryStore(db)
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


@pytest.mark.asyncio
async def test_initialize_creates_db(store):
    assert store._db is not None
    assert store.path.exists()


@pytest.mark.asyncio
async def test_upsert_and_get(store):
    entry = MemoryEntry(title="Hello", content="World", importance=0.8)
    saved = await store.upsert(entry)
    assert saved.title == "Hello"

    retrieved = await store.get(saved.id)
    assert retrieved is not None
    assert retrieved.title == "Hello"
    assert retrieved.content == "World"
    assert retrieved.importance == 0.8


@pytest.mark.asyncio
async def test_delete(store):
    entry = await store.upsert(MemoryEntry(title="To delete", content="Bye"))
    assert await store.delete(entry.id) is True
    assert await store.get(entry.id) is None
    assert await store.delete(entry.id) is False


@pytest.mark.asyncio
async def test_list_by_type(store):
    await store.upsert(MemoryEntry(title="N1", entry_type=MemoryEntryType.NOTE))
    await store.upsert(MemoryEntry(title="F1", entry_type=MemoryEntryType.USER_FACT))
    await store.upsert(MemoryEntry(title="N2", entry_type=MemoryEntryType.NOTE))

    notes = await store.list_by_type(MemoryEntryType.NOTE)
    assert len(notes) == 2


@pytest.mark.asyncio
async def test_list_recent(store):
    for i in range(5):
        await store.upsert(MemoryEntry(title=f"Entry {i}"))
    entries = await store.list_recent(limit=3)
    assert len(entries) == 3


@pytest.mark.asyncio
async def test_empty_search_like_panel_uses_recent_notes(store):
    """Memory panel opens with query='' — must not stay blank when notes exist."""
    from remedy.models import MemoryEntryType

    await store.upsert(
        MemoryEntry(
            title="Comfy note",
            content="video ready",
            entry_type=MemoryEntryType.NOTE,
        )
    )
    await store.upsert(
        MemoryEntry(
            title="Gateway event: heartbeat",
            content="{}",
            entry_type=MemoryEntryType.SYSTEM,
        )
    )
    # Route logic: empty query → list_recent, drop system
    raw = await store.list_recent(limit=30)
    notes = [
        e
        for e in raw
        if str(getattr(e.entry_type, "value", e.entry_type) or "") != "system"
    ]
    assert any(e.title == "Comfy note" for e in notes)
    assert not any("heartbeat" in (e.title or "") for e in notes)


@pytest.mark.asyncio
async def test_list_important(store):
    await store.upsert(MemoryEntry(title="Low", importance=0.2))
    await store.upsert(MemoryEntry(title="High", importance=0.9))
    await store.upsert(MemoryEntry(title="Mid", importance=0.5))

    important = await store.list_important(threshold=0.7)
    assert len(important) == 1
    assert important[0].title == "High"


@pytest.mark.asyncio
async def test_fts5_search(store):
    await store.upsert(MemoryEntry(title="Python errors", content="Handling TypeErrors in async code"))
    await store.upsert(MemoryEntry(title="Database tuning", content="SQLite WAL mode improves perf"))
    await store.upsert(MemoryEntry(title="API design", content="REST vs GraphQL tradeoffs"))

    results = await store.search("Python")
    assert len(results) == 1
    assert results[0].title == "Python errors"

    results = await store.search("SQLite OR async")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_search_simple_fallback(store):
    await store.upsert(MemoryEntry(title="Unique123", content="Special content here"))
    results = await store.search_simple("Unique123")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_handoff_crud(store):
    note = HandoffNote(
        title="Session Alpha to Beta",
        content="Completed Phase 0. Moving to Phase 1.",
        action_items=["Implement FTS5 search", "Add handoff API"],
        decisions=["Use aiosqlite for memory"],
        context_summary="Phase 0 is done.",
        from_session="alpha",
    )

    created = await store.create_handoff(note)
    assert created.id is not None

    retrieved = await store.get_handoff(created.id)
    assert retrieved is not None
    assert retrieved.title == "Session Alpha to Beta"
    assert len(retrieved.action_items) == 2
    assert not retrieved.acknowledged

    assert await store.ack_handoff(created.id)
    retrieved2 = await store.get_handoff(created.id)
    assert retrieved2.acknowledged


@pytest.mark.asyncio
async def test_list_handoffs(store):
    await store.create_handoff(HandoffNote(title="H1", content="First"))
    await store.create_handoff(HandoffNote(title="H2", content="Second"))

    handoffs = await store.list_handoffs()
    assert len(handoffs) == 2


@pytest.mark.asyncio
async def test_get_relevant_handoffs(store):
    await store.create_handoff(HandoffNote(title="Phase 0 complete", content="Scaffold done"))
    await store.create_handoff(HandoffNote(title="Bug fix", content="Fixed memory leak"))

    results = await store.get_relevant_handoffs("Phase")
    assert len(results) >= 1
    assert "Phase" in results[0].title


@pytest.mark.asyncio
async def test_session_summaries(store):
    from datetime import datetime

    summary = SessionSummary(
        session_id="sess-001",
        started_at=datetime.now(UTC),
        tasks_completed=5,
        skills_created=2,
        key_decisions=["Use SQLite"],
        summary="Great session.",
    )
    saved = await store.save_session_summary(summary)
    assert saved.session_id == "sess-001"

    retrieved = await store.get_session_summary("sess-001")
    assert retrieved is not None
    assert retrieved.tasks_completed == 5
    assert retrieved.skills_created == 2
    assert "Use SQLite" in retrieved.key_decisions


@pytest.mark.asyncio
async def test_list_sessions(store):
    from datetime import datetime

    await store.save_session_summary(
        SessionSummary(session_id="s1", started_at=datetime.now(UTC))
    )
    await store.save_session_summary(
        SessionSummary(session_id="s2", started_at=datetime.now(UTC))
    )
    sessions = await store.list_sessions()
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_context_manager(tmp_path):
    db = tmp_path / "ctx.db"
    async with MemoryStore(db) as s:
        await s.upsert(MemoryEntry(title="CM test"))
        entries = await s.list_recent()
        assert len(entries) == 1

    # Should be closed after context manager
    assert s._db is None


@pytest.mark.asyncio
async def test_revert_from_resyncs_message_count(store):
    """Time-travel soft-delete must drop session list badges, not leave +N forever."""
    from remedy.models import ChatMessage, ChatMessageRole, ChatSession

    sid = "sess-tt-1"
    await store.create_chat_session(ChatSession(id=sid, title="TT"))
    m1 = await store.add_chat_message(
        ChatMessage(session_id=sid, role=ChatMessageRole.USER, content="u1")
    )
    await store.add_chat_message(
        ChatMessage(session_id=sid, role=ChatMessageRole.ASSISTANT, content="a1")
    )
    m3 = await store.add_chat_message(
        ChatMessage(session_id=sid, role=ChatMessageRole.USER, content="u2")
    )
    await store.add_chat_message(
        ChatMessage(session_id=sid, role=ChatMessageRole.ASSISTANT, content="a2")
    )
    sess = await store.get_chat_session(sid)
    assert sess is not None
    assert sess.message_count == 4

    # Roll back to second user turn (soft-delete u2 + a2)
    n = await store.revert_from(sid, str(m3.id))
    assert n == 2
    sess2 = await store.get_chat_session(sid)
    assert sess2 is not None
    assert sess2.message_count == 2
    live = await store.get_chat_messages(sid, limit=50)
    assert len(live) == 2
    assert [m.content for m in live] == ["u1", "a1"]

    # Single-message revert also resyncs
    ok = await store.revert_message(str(m1.id))
    assert ok is True
    sess3 = await store.get_chat_session(sid)
    assert sess3 is not None
    # a1 remains (reverted only m1); count = non-reverted rows
    assert sess3.message_count == 1
    live2 = await store.get_chat_messages(sid, limit=50)
    assert len(live2) == 1
    assert live2[0].content == "a1"
