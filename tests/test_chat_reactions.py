"""Message reactions persist with chat turns (shared schema with the Go runtime)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from remedy.memory.store import MemoryStore, normalize_reaction
from remedy.models import REACTION_SET, ChatMessage, ChatMessageRole, ChatSession


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "reactions.db")
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


def _seed(store: MemoryStore) -> tuple[str, str, str]:
    sess = asyncio.run(store.create_chat_session(ChatSession(title="T")))
    user = asyncio.run(
        store.add_chat_message(
            ChatMessage(session_id=sess.id, role=ChatMessageRole.USER, content="Fix it")
        )
    )
    reply = asyncio.run(
        store.add_chat_message(
            ChatMessage(session_id=sess.id, role=ChatMessageRole.ASSISTANT, content="Fixed.")
        )
    )
    return sess.id, str(user.id), str(reply.id)


def test_reaction_set_is_exactly_four() -> None:
    assert REACTION_SET == ("👍", "👎", "❤️", "💔")


def test_normalize_reaction() -> None:
    assert normalize_reaction("❤") == "❤️"
    assert normalize_reaction("👍🏽") == "👍"
    assert normalize_reaction("broken_heart") == "💔"
    assert normalize_reaction("") == ""
    with pytest.raises(ValueError):
        normalize_reaction("🎉")


def test_owner_and_agent_reactions_persist_one_per_side(store: MemoryStore) -> None:
    sid, user_id, reply_id = _seed(store)
    assert asyncio.run(store.set_chat_message_reaction(reply_id, "user", "👍")) == {"user": "👍"}
    assert asyncio.run(store.set_chat_message_reaction(reply_id, "user", "❤")) == {"user": "❤️"}
    assert asyncio.run(store.set_chat_message_reaction(user_id, "agent", "👍")) == {"agent": "👍"}
    msgs = asyncio.run(store.get_chat_messages(sid))
    by_id = {str(m.id): m for m in msgs}
    assert by_id[reply_id].reactions == {"user": "❤️"}
    assert by_id[user_id].reactions == {"agent": "👍"}
    assert asyncio.run(store.set_chat_message_reaction(reply_id, "user", None)) == {}
    assert asyncio.run(store.get_chat_message(reply_id)).reactions == {}


def test_sides_only_react_to_the_other_side(store: MemoryStore) -> None:
    _sid, user_id, reply_id = _seed(store)
    assert asyncio.run(store.set_chat_message_reaction(user_id, "user", "👍")) is None
    assert asyncio.run(store.set_chat_message_reaction(reply_id, "agent", "👍")) is None
    assert asyncio.run(store.set_chat_message_reaction("missing", "user", "👍")) is None


def test_old_database_gains_reactions_column(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, title TEXT, model TEXT, agent TEXT,
          project_path TEXT, message_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE chat_messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user', content TEXT NOT NULL DEFAULT '', thinking TEXT,
          tool_calls TEXT NOT NULL DEFAULT '[]', tool_results TEXT NOT NULL DEFAULT '[]',
          model TEXT, agent TEXT, tokens INTEGER, created_at TEXT NOT NULL,
          reverted INTEGER NOT NULL DEFAULT 0);
        """
    )
    db.commit()
    db.close()
    s = MemoryStore(path)
    asyncio.run(s.initialize())
    try:
        cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(chat_messages)")}
        assert "reactions" in cols
    finally:
        asyncio.run(s.close())
