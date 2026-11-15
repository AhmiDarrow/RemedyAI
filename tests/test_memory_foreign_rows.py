"""memory.db is shared with the Go runtime, which writes rows the Python
models do not describe (e.g. owner reaction feedback: id ``reaction:<msg>``,
entry_type ``reaction_feedback``). One such row must never break reads."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType


@pytest.fixture
def store(tmp_path):
    s = MemoryStore(tmp_path / "memory.db")
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


def _insert_go_reaction_row(store: MemoryStore, importance: float = 0.7) -> None:
    """Same columns and values the Go reactions path writes."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    db = store._ensure_db()
    db.execute(
        "INSERT INTO memory_entries (id, entry_type, title, content, tags, metadata, "
        "created_at, updated_at, session_id, importance) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "reaction:7f3c9a",
            "reaction_feedback",
            "Owner reacted 👎 (reconsider / try again)",
            "Here is the deploy plan you asked for.",
            json.dumps(["feedback", "reaction"]),
            json.dumps({"emoji": "👎", "source": "owner_reaction", "message_id": "7f3c9a"}),
            now,
            now,
            "sess-1",
            importance,
        ),
    )
    db.commit()


@pytest.mark.asyncio
async def test_go_reaction_row_does_not_break_memory_reads(store):
    keep = MemoryEntry(
        title="Deploy plan",
        content="deploy plan steps",
        entry_type=MemoryEntryType.NOTE,
        session_id="sess-1",
        importance=0.9,
    )
    await store.upsert(keep)
    _insert_go_reaction_row(store)

    recent = await store.list_recent(10)
    important = await store.list_important(0.7, 10)
    by_session = await store.list_by_session("sess-1")
    hits = await store.search("deploy plan")
    like = await store.search_simple("deploy plan")
    got = await store.get("reaction:7f3c9a")

    for rows in (recent, important, by_session, hits):
        assert [e.title for e in rows] == ["Deploy plan"], rows
    assert all(e.title == "Deploy plan" for e in like)
    assert got is None
