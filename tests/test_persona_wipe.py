"""Persona wipe + session-delete memory cascade."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.memory.persona_wipe import CONFIRM_PHRASE, wipe_persona
from remedy.memory.profile import UserProfile
from remedy.memory.store import MemoryStore
from remedy.models import ChatSession, MemoryEntry, MemoryEntryType


@pytest.mark.asyncio
async def test_wipe_persona_requires_phrase(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    with pytest.raises(ValueError, match="WIPE"):
        await wipe_persona(store, home=tmp_path, confirm="please")
    await store.close()


@pytest.mark.asyncio
async def test_wipe_persona_clears_profile_and_facts(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    store = MemoryStore(db)
    await store.initialize()
    profile = UserProfile(user_id="default", display_name="Alex")
    profile.add_fact("likes tea", category="taste")
    await store.save_user_profile(profile)
    await store.upsert(
        MemoryEntry(
            title="tea",
            content="likes tea",
            entry_type=MemoryEntryType.USER_FACT,
        )
    )
    (tmp_path / "life_goals.json").write_text('{"goals":[{"title":"x"}]}', encoding="utf-8")
    soul = tmp_path / "soul"
    soul.mkdir()
    (soul / "field.json").write_text('{"relational":{"rapport":0.9}}', encoding="utf-8")

    stats = await wipe_persona(store, home=tmp_path, confirm=CONFIRM_PHRASE)
    assert stats["ok"] is True
    assert stats["profile_reset"] is True
    assert stats["user_fact_entries"] >= 1
    assert stats["life_goals_removed"] is True

    after = await store.get_or_create_profile()
    assert after.display_name is None
    assert after.facts == []
    leftover = await store.list_by_type(MemoryEntryType.USER_FACT, limit=10)
    assert leftover == []
    assert not (tmp_path / "life_goals.json").is_file()
    await store.close()


@pytest.mark.asyncio
async def test_delete_chat_session_drops_session_memory(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    sess = await store.create_chat_session(ChatSession(title="Work"))
    await store.upsert(
        MemoryEntry(
            title="note",
            content="session only",
            entry_type=MemoryEntryType.NOTE,
            session_id=sess.id,
        )
    )
    n = await store.delete_by_session(sess.id)
    assert n >= 1
    left = await store.list_by_session(sess.id)
    assert left == []
    ok = await store.delete_chat_session(sess.id)
    assert ok is True
    await store.close()
