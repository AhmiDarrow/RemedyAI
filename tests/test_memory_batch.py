"""Memory batch upsert, FTS rebuild, and search fallback tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType, HandoffNote


@pytest.mark.asyncio
async def test_upsert_many_and_rebuild_fts(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    await store.initialize()
    entries = [
        MemoryEntry(
            id=uuid4(),
            entry_type=MemoryEntryType.NOTE,
            title=f"note-{i}",
            content=f"payload alpha {i} unique{i}",
            importance=0.5,
        )
        for i in range(25)
    ]
    n = await store.upsert_many(entries)
    assert n == 25
    hits = await store.search("alpha", limit=10)
    assert len(hits) >= 1
    rebuilt = await store.rebuild_fts()
    assert rebuilt == 25
    hits2 = await store.search("unique3", limit=5)
    assert any("unique3" in h.content for h in hits2)
    await store.close()


@pytest.mark.asyncio
async def test_search_falls_back_on_bad_fts_syntax(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    await store.initialize()
    await store.upsert(
        MemoryEntry(
            id=uuid4(),
            title="special",
            content="contains C++ and 100% match",
            entry_type=MemoryEntryType.NOTE,
        )
    )
    # Characters that often break bare FTS MATCH should not raise.
    hits = await store.search("C++", limit=10)
    assert isinstance(hits, list)
    hits2 = await store.search_simple("100%", limit=10)
    assert any("100%" in h.content or "special" in h.title for h in hits2)
    await store.close()


@pytest.mark.asyncio
async def test_handoff_memory_stable_id(tmp_path) -> None:
    store = MemoryStore(tmp_path / "m.db")
    await store.initialize()
    note = HandoffNote(
        id=uuid4(),
        title="Phase N",
        content="context transfer",
        from_session="s1",
    )
    await store.create_handoff(note)
    await store.create_handoff(note)  # re-save same note
    mem = await store.list_by_type(MemoryEntryType.HANDOFF, limit=20)
    # One memory row for the handoff (upsert by stable id), not two.
    assert sum(1 for m in mem if m.metadata.get("handoff_id") == str(note.id)) == 1
    assert len(mem) >= 1
    await store.close()
