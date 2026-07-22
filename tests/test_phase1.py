"""Tests for memory consolidator, auto-handoff, and repair tools."""

import asyncio

import pytest

from remedy.memory.consolidator import MemoryConsolidator
from remedy.memory.handoff import AutoHandoffManager
from remedy.memory.repair import MemoryRepair
from remedy.memory.store import MemoryStore
from remedy.models import (
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    Task,
    TaskStatus,
)


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test_phase1.db"
    s = MemoryStore(db)
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


class TestConsolidator:
    @pytest.mark.asyncio
    async def test_consolidate_session(self, store):
        for i in range(5):
            e = MemoryEntry(
                title=f"Entry {i}",
                content=f"Content {i}: working on project",
                entry_type=MemoryEntryType.NOTE,
                session_id="sess-1",
                tags=["project", "work"],
                importance=0.6,
            )
            await store.upsert(e)

        c = MemoryConsolidator(store)
        result = await c.consolidate_session("sess-1")
        assert result is not None
        assert result.entry_type == MemoryEntryType.SESSION
        assert "sess-1" in result.title
        assert "project" in result.tags

    @pytest.mark.asyncio
    async def test_consolidate_not_enough_entries(self, store):
        c = MemoryConsolidator(store)
        result = await c.consolidate_session("sess-empty")
        assert result is None

    @pytest.mark.asyncio
    async def test_deduplicate(self, store):
        e1 = MemoryEntry(title="Same Title", content="a", entry_type=MemoryEntryType.NOTE)
        e2 = MemoryEntry(title="Same Title", content="b", entry_type=MemoryEntryType.NOTE, tags=["dup"])
        await store.upsert(e1)
        await store.upsert(e2)

        c = MemoryConsolidator(store)
        removed = await c.deduplicate()
        assert removed >= 1

        remaining = await store.list_recent(limit=10)
        titles = [e.title for e in remaining]
        assert "Same Title" in titles


class TestAutoHandoff:
    @pytest.mark.asyncio
    async def test_generate_handoff(self, store):
        mgr = AutoHandoffManager(store)
        tasks = [
            Task(title="Write docs", status=TaskStatus.COMPLETED, result_summary="Done"),
        ]
        open_tasks = [
            Task(title="Add tests", status=TaskStatus.IN_PROGRESS),
        ]
        handoff = await mgr.generate_handoff(
            session_id="sess-1", tasks=tasks, open_tasks=open_tasks,
        )
        assert handoff is not None
        assert "Write docs" in handoff.content
        assert "Add tests" in handoff.content
        assert len(handoff.action_items) == 1

    @pytest.mark.asyncio
    async def test_pending_handoffs(self, store):
        mgr = AutoHandoffManager(store)
        note = HandoffNote(title="Pending", content="Test", acknowledged=False)
        await store.create_handoff(note)

        pending = await mgr.get_pending_handoffs()
        assert len(pending) == 1
        assert pending[0].title == "Pending"

    @pytest.mark.asyncio
    async def test_acknowledge_all(self, store):
        mgr = AutoHandoffManager(store)
        for i in range(3):
            note = HandoffNote(title=f"Note {i}", content="test", acknowledged=False)
            await store.create_handoff(note)

        count = await mgr.acknowledge_all()
        assert count == 3

        pending = await mgr.get_pending_handoffs()
        assert len(pending) == 0


class TestMemoryRepair:
    @pytest.mark.asyncio
    async def test_check_integrity(self, store):
        repair = MemoryRepair(store)
        info = await repair.check_integrity()
        assert "integrity" in info
        assert info["integrity"] == "ok"
        assert "entry_counts" in info

    @pytest.mark.asyncio
    async def test_vacuum(self, store):
        repair = MemoryRepair(store)
        result = await repair.vacuum()
        assert result["before_bytes"] >= 0
        assert result["after_bytes"] >= 0
        assert result["reclaimed_bytes"] >= 0

    @pytest.mark.asyncio
    async def test_backup(self, store, tmp_path):
        e = MemoryEntry(title="Important", content="Data", importance=0.9)
        await store.upsert(e)

        repair = MemoryRepair(store)
        backup_dir = tmp_path / "backups"
        backup_path = await repair.backup(backup_dir=backup_dir)
        assert backup_path.exists()
        assert backup_path.stat().st_size > 0
        assert backup_path.name.startswith("memory_backup_")

    @pytest.mark.asyncio
    async def test_checkpoint(self, store):
        repair = MemoryRepair(store)
        await repair.checkpoint()

    @pytest.mark.asyncio
    async def test_rebuild_fts(self, store):
        repair = MemoryRepair(store)
        e = MemoryEntry(title="Searchable", content="findme")
        await store.upsert(e)
        result = await repair.rebuild_fts()
        assert result is True


class TestUserPersistence:
    @pytest.mark.asyncio
    async def test_save_and_load_profile(self, store):
        from remedy.memory.profile import UserProfile
        profile = UserProfile(user_id="test-user", display_name="Test User")
        profile.set_trait("timezone", "UTC")
        profile.add_fact("Loves testing")

        await store.save_user_profile(profile)
        loaded = await store.load_user_profile("test-user")
        assert loaded is not None
        assert loaded.display_name == "Test User"
        assert loaded.get_trait("timezone") == "UTC"

    @pytest.mark.asyncio
    async def test_get_or_create_creates(self, store):
        profile = await store.get_or_create_profile("new-user")
        assert profile.user_id == "new-user"
        assert profile.stats["sessions_count"] == 0

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, store):
        from remedy.memory.profile import UserProfile
        profile = UserProfile(user_id="returning", display_name="Returning User")
        await store.save_user_profile(profile)

        loaded = await store.get_or_create_profile("returning")
        assert loaded.display_name == "Returning User"

    @pytest.mark.asyncio
    async def test_search_user_facts(self, store):
        from remedy.memory.profile import UserProfile
        profile = UserProfile(user_id="u1")
        profile.add_fact("Uses Python daily", category="tech")
        profile.add_fact("Enjoys hiking", category="personal")
        await store.save_user_profile(profile)

        results = await store.search_user_facts("Python", user_id="u1")
        assert len(results) == 1
        assert results[0]["fact"] == "Uses Python daily"

        results = await store.search_user_facts("nonexistent", user_id="u1")
        assert len(results) == 0
