"""Persistent memory backend with SQLite + FTS5 and explicit handoff support.

Inspired by Hermes' memory system, adapted as a clean standalone module.
Supports cross-session search, user modeling, and structured handoff notes
critical for the Remedy/Reme companion experience.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from remedy.core.security import sanitize_search_query
from remedy.memory.profile import UserProfile
from remedy.models import (
    ChatMessage,
    ChatMessageRole,
    ChatSession,
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    SessionSummary,
)

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL DEFAULT 'note',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT,
    importance REAL NOT NULL DEFAULT 0.5
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    title, content, tags,
    content=memory_entries,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_entries BEGIN
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_entries BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
    VALUES ('delete', old.rowid, old.title, old.content, old.tags);
    INSERT INTO memory_fts(rowid, title, content, tags)
    VALUES (new.rowid, new.title, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS handoff_notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    from_session TEXT,
    to_session TEXT,
    context_summary TEXT,
    action_items TEXT NOT NULL DEFAULT '[]',
    decisions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session_summaries (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    skills_created INTEGER NOT NULL DEFAULT 0,
    skills_refined INTEGER NOT NULL DEFAULT 0,
    key_decisions TEXT NOT NULL DEFAULT '[]',
    open_items TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(entry_type);
CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_entries(session_id);
CREATE INDEX IF NOT EXISTS idx_memory_importance ON memory_entries(importance);
CREATE INDEX IF NOT EXISTS idx_memory_created ON memory_entries(created_at);

CREATE TABLE IF NOT EXISTS user_profile (
    user_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    fact TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    confidence REAL NOT NULL DEFAULT 0.7,
    source TEXT NOT NULL DEFAULT 'inferred',
    created_at TEXT NOT NULL,
    last_referenced TEXT NOT NULL,
    reference_count INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (user_id) REFERENCES user_profile(user_id)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Session',
    model TEXT,
    agent TEXT,
    project_path TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    thinking TEXT,
    tool_calls TEXT NOT NULL DEFAULT '[]',
    tool_results TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    agent TEXT,
    tokens INTEGER,
    created_at TEXT NOT NULL,
    reverted INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at);

CREATE TABLE IF NOT EXISTS user_traits (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'inferred',
    first_observed TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (user_id, key),
    FOREIGN KEY (user_id) REFERENCES user_profile(user_id)
);
"""


class MemoryStore:
    """Primary persistent memory backend.

    Features:
    - CRUD for memory entries with FTS5 full-text search
    - Structured handoff notes between sessions/tasks
    - Session summaries for continuity
    - High-importance entry filtering

    Usage:
        store = MemoryStore("~/.remedy/memory.db")
        await store.initialize()
        await store.upsert(MemoryEntry(title="...", content="..."))
        results = await store.search("query")
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self._db: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._db_path

    # -- lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        """Open the database and ensure the schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    async def __aenter__(self) -> MemoryStore:
        await self.initialize()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _ensure_db(self) -> sqlite3.Connection:
        if self._db is None:
            raise RuntimeError("MemoryStore not initialized. Call await initialize() first.")
        return self._db

    # -- memory entry CRUD ---------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            id=UUID(row["id"]),
            entry_type=MemoryEntryType(row["entry_type"]),
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            metadata=json.loads(row["metadata"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            session_id=row["session_id"],
            importance=row["importance"],
        )

    async def upsert(self, entry: MemoryEntry) -> MemoryEntry:
        """Insert or update a memory entry. Returns the saved entry."""
        db = self._ensure_db()
        entry.updated_at = datetime.now(UTC)

        db.execute(
            """
            INSERT INTO memory_entries (id, entry_type, title, content, tags, metadata,
                                        created_at, updated_at, session_id, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entry_type = excluded.entry_type,
                title = excluded.title,
                content = excluded.content,
                tags = excluded.tags,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at,
                session_id = excluded.session_id,
                importance = excluded.importance
            """,
            (
                str(entry.id),
                entry.entry_type.value,
                entry.title,
                entry.content,
                json.dumps(entry.tags),
                json.dumps(entry.metadata),
                entry.created_at.isoformat(),
                entry.updated_at.isoformat(),
                entry.session_id,
                entry.importance,
            ),
        )
        db.commit()
        return entry

    async def get(self, entry_id: str | UUID) -> MemoryEntry | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (str(entry_id),)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    async def delete(self, entry_id: str | UUID) -> bool:
        db = self._ensure_db()
        cursor = db.execute("DELETE FROM memory_entries WHERE id = ?", (str(entry_id),))
        db.commit()
        return cursor.rowcount > 0

    async def list_by_type(
        self, entry_type: MemoryEntryType, limit: int = 50, offset: int = 0
    ) -> list[MemoryEntry]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM memory_entries WHERE entry_type = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (entry_type.value, limit, offset),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def list_recent(self, limit: int = 50) -> list[MemoryEntry]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM memory_entries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def list_by_session(
        self, session_id: str, limit: int = 200, offset: int = 0
    ) -> list[MemoryEntry]:
        """Return memory entries belonging to a specific session."""
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM memory_entries WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def list_important(self, threshold: float = 0.7, limit: int = 50) -> list[MemoryEntry]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM memory_entries WHERE importance >= ? "
            "ORDER BY importance DESC LIMIT ?",
            (threshold, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # -- FTS5 search ---------------------------------------------------------

    async def search(
        self, query: str, limit: int = 20, entry_type: MemoryEntryType | None = None
    ) -> list[MemoryEntry]:
        """Full-text search across title, content, and tags."""
        query = sanitize_search_query(query, max_length=500)
        db = self._ensure_db()
        type_filter = ""
        params: list[Any] = []

        if entry_type is not None:
            type_filter = "AND memory_entries.entry_type = ?"
            params = [query, entry_type.value, limit]
        else:
            params = [query, limit]

        rows = db.execute(
            f"""
            SELECT memory_entries.* FROM memory_entries
            JOIN memory_fts ON memory_entries.rowid = memory_fts.rowid
            WHERE memory_fts MATCH ? {type_filter}
            ORDER BY rank
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def search_simple(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        """Simple LIKE-based search when FTS5 match syntax may fail."""
        db = self._ensure_db()
        like_q = f"%{query}%"
        rows = db.execute(
            "SELECT * FROM memory_entries WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like_q, like_q, limit),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    # -- handoff notes -------------------------------------------------------

    async def create_handoff(self, note: HandoffNote) -> HandoffNote:
        """Persist a handoff note and optionally save it as a memory entry."""
        db = self._ensure_db()
        db.execute(
            """
            INSERT OR REPLACE INTO handoff_notes
                (id, title, content, tags, from_session, to_session,
                 context_summary, action_items, decisions, created_at, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(note.id),
                note.title,
                note.content,
                json.dumps(note.tags),
                note.from_session,
                note.to_session,
                note.context_summary,
                json.dumps(note.action_items),
                json.dumps(note.decisions),
                note.created_at.isoformat(),
                int(note.acknowledged),
            ),
        )
        db.commit()

        memory_entry = MemoryEntry(
            id=uuid4(),
            entry_type=MemoryEntryType.HANDOFF,
            title=f"Handoff: {note.title}",
            content=note.content,
            tags=note.tags,
            metadata={
                "handoff_id": str(note.id),
                "from_session": note.from_session,
                "action_items": note.action_items,
                "decisions": note.decisions,
            },
            session_id=note.from_session,
            importance=0.9,
        )
        await self.upsert(memory_entry)
        return note

    async def get_handoff(self, handoff_id: str | UUID) -> HandoffNote | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT * FROM handoff_notes WHERE id = ?", (str(handoff_id),)
        ).fetchone()
        if row is None:
            return None
        return HandoffNote(
            id=UUID(row["id"]),
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            from_session=row["from_session"],
            to_session=row["to_session"],
            context_summary=row["context_summary"],
            action_items=json.loads(row["action_items"]),
            decisions=json.loads(row["decisions"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            acknowledged=bool(row["acknowledged"]),
        )

    async def get_relevant_handoffs(
        self, query: str, limit: int = 5
    ) -> list[HandoffNote]:
        """Find handoff notes relevant to a query."""
        db = self._ensure_db()
        like_q = f"%{query}%"
        rows = db.execute(
            "SELECT * FROM handoff_notes WHERE title LIKE ? OR content LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (like_q, like_q, limit),
        ).fetchall()
        return [
            HandoffNote(
                id=UUID(r["id"]),
                title=r["title"],
                content=r["content"],
                tags=json.loads(r["tags"]),
                from_session=r["from_session"],
                to_session=r["to_session"],
                context_summary=r["context_summary"],
                action_items=json.loads(r["action_items"]),
                decisions=json.loads(r["decisions"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                acknowledged=bool(r["acknowledged"]),
            )
            for r in rows
        ]

    async def list_handoffs(self, limit: int = 50) -> list[HandoffNote]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM handoff_notes ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            HandoffNote(
                id=UUID(r["id"]),
                title=r["title"],
                content=r["content"],
                tags=json.loads(r["tags"]),
                from_session=r["from_session"],
                to_session=r["to_session"],
                context_summary=r["context_summary"],
                action_items=json.loads(r["action_items"]),
                decisions=json.loads(r["decisions"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                acknowledged=bool(r["acknowledged"]),
            )
            for r in rows
        ]

    async def ack_handoff(self, handoff_id: str | UUID) -> bool:
        db = self._ensure_db()
        cursor = db.execute(
            "UPDATE handoff_notes SET acknowledged = 1 WHERE id = ?",
            (str(handoff_id),),
        )
        db.commit()
        return cursor.rowcount > 0

    # -- session summaries ---------------------------------------------------

    async def save_session_summary(self, summary: SessionSummary) -> SessionSummary:
        db = self._ensure_db()
        db.execute(
            """
            INSERT OR REPLACE INTO session_summaries
                (session_id, started_at, ended_at, tasks_completed, skills_created,
                 skills_refined, key_decisions, open_items, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                summary.session_id,
                summary.started_at.isoformat(),
                summary.ended_at.isoformat(),
                summary.tasks_completed,
                summary.skills_created,
                summary.skills_refined,
                json.dumps(summary.key_decisions),
                json.dumps(summary.open_items),
                summary.summary,
            ),
        )
        db.commit()
        return summary

    async def get_session_summary(self, session_id: str) -> SessionSummary | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return SessionSummary(
            session_id=row["session_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            ended_at=datetime.fromisoformat(row["ended_at"]),
            tasks_completed=row["tasks_completed"],
            skills_created=row["skills_created"],
            skills_refined=row["skills_refined"],
            key_decisions=json.loads(row["key_decisions"]),
            open_items=json.loads(row["open_items"]),
            summary=row["summary"],
        )

    async def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM session_summaries ORDER BY ended_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SessionSummary(
                session_id=r["session_id"],
                started_at=datetime.fromisoformat(r["started_at"]),
                ended_at=datetime.fromisoformat(r["ended_at"]),
                tasks_completed=r["tasks_completed"],
                skills_created=r["skills_created"],
                skills_refined=r["skills_refined"],
                key_decisions=json.loads(r["key_decisions"]),
                open_items=json.loads(r["open_items"]),
                summary=r["summary"],
            )
            for r in rows
        ]

    # -- user profile ---------------------------------------------------------

    async def save_user_profile(self, profile: UserProfile) -> None:
        db = self._ensure_db()
        now = datetime.now(UTC).isoformat()
        db.execute(
            "INSERT OR REPLACE INTO user_profile (user_id, profile_json, updated_at) VALUES (?, ?, ?)",
            (profile.user_id, profile.model_dump_json(indent=2), now),
        )

        db.execute(
            "DELETE FROM user_facts WHERE user_id = ?", (profile.user_id,)
        )
        db.execute(
            "DELETE FROM user_traits WHERE user_id = ?", (profile.user_id,)
        )

        for fact in profile.facts:
            db.execute(
                "INSERT OR REPLACE INTO user_facts (id, user_id, fact, category, confidence, "
                "source, created_at, last_referenced, reference_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(fact.id), profile.user_id, fact.fact, fact.category, fact.confidence,
                 fact.source, fact.created_at.isoformat(), fact.last_referenced.isoformat(),
                 fact.reference_count),
            )

        for key, trait in profile.traits.items():
            import json as _json
            db.execute(
                "INSERT OR REPLACE INTO user_traits (user_id, key, value_json, confidence, "
                "source, first_observed, last_updated, observation_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (profile.user_id, key, _json.dumps(trait.value, default=str),
                 trait.confidence, trait.source,
                 trait.first_observed.isoformat(), trait.last_updated.isoformat(),
                 trait.observation_count),
            )

        db.commit()

    async def load_user_profile(self, user_id: str = "default") -> UserProfile | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT profile_json FROM user_profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["profile_json"])
        profile = UserProfile(**data)
        return profile

    async def get_or_create_profile(self, user_id: str = "default") -> UserProfile:
        profile = await self.load_user_profile(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            await self.save_user_profile(profile)
        return profile

    async def search_user_facts(self, query: str, user_id: str = "default", limit: int = 10) -> list[dict]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM user_facts WHERE user_id = ? AND (fact LIKE ? OR category LIKE ?) "
            "ORDER BY reference_count DESC LIMIT ?",
            (user_id, f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- chat sessions --------------------------------------------------------

    def _row_to_session(self, row: sqlite3.Row) -> ChatSession:
        return ChatSession(
            id=row["id"],
            title=row["title"],
            model=row["model"],
            agent=row["agent"],
            project_path=row["project_path"],
            message_count=row["message_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_message(self, row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            id=UUID(row["id"]),
            session_id=row["session_id"],
            role=ChatMessageRole(row["role"]),
            content=row["content"],
            thinking=row["thinking"],
            tool_calls=json.loads(row["tool_calls"]),
            tool_results=json.loads(row["tool_results"]),
            model=row["model"],
            agent=row["agent"],
            tokens=row["tokens"],
            created_at=datetime.fromisoformat(row["created_at"]),
            reverted=bool(row["reverted"]),
        )

    async def create_chat_session(self, session: ChatSession) -> ChatSession:
        db = self._ensure_db()
        session.created_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        db.execute(
            """INSERT INTO chat_sessions (id, title, model, agent, project_path,
               message_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id, session.title, session.model, session.agent,
                session.project_path, session.message_count,
                session.created_at.isoformat(), session.updated_at.isoformat(),
            ),
        )
        db.commit()
        return session

    async def get_chat_session(self, session_id: str) -> ChatSession | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    async def update_chat_session(self, session_id: str, **fields: Any) -> ChatSession | None:
        db = self._ensure_db()
        allowed = {"title", "model", "agent", "project_path", "message_count"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return await self.get_chat_session(session_id)
        updates["updated_at"] = datetime.now(UTC).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [session_id]
        db.execute(
            f"UPDATE chat_sessions SET {set_clause} WHERE id = ?", values
        )
        db.commit()
        return await self.get_chat_session(session_id)

    async def delete_chat_session(self, session_id: str) -> bool:
        db = self._ensure_db()
        db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        cursor = db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        db.commit()
        return cursor.rowcount > 0

    async def list_chat_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> list[ChatSession]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # -- chat messages --------------------------------------------------------

    async def add_chat_message(self, msg: ChatMessage) -> ChatMessage:
        db = self._ensure_db()
        msg.created_at = datetime.now(UTC)
        db.execute(
            """INSERT INTO chat_messages (id, session_id, role, content, thinking,
               tool_calls, tool_results, model, agent, tokens, created_at, reverted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(msg.id), msg.session_id, msg.role.value, msg.content,
                msg.thinking, json.dumps(msg.tool_calls),
                json.dumps(msg.tool_results), msg.model, msg.agent,
                msg.tokens, msg.created_at.isoformat(), int(msg.reverted),
            ),
        )
        db.execute(
            "UPDATE chat_sessions SET message_count = message_count + 1, "
            "updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), msg.session_id),
        )
        db.commit()
        return msg

    async def get_chat_messages(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> list[ChatMessage]:
        db = self._ensure_db()
        rows = db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? "
            "ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (session_id, limit, offset),
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    async def revert_message(self, msg_id: str) -> bool:
        db = self._ensure_db()
        cursor = db.execute(
            "UPDATE chat_messages SET reverted = 1 WHERE id = ?", (msg_id,)
        )
        db.commit()
        return cursor.rowcount > 0

    async def revert_from(self, session_id: str, msg_id: str) -> int:
        db = self._ensure_db()
        target = db.execute(
            "SELECT created_at FROM chat_messages WHERE id = ?", (msg_id,)
        ).fetchone()
        if target is None:
            return 0
        cursor = db.execute(
            "UPDATE chat_messages SET reverted = 1 "
            "WHERE session_id = ? AND created_at >= ?",
            (session_id, target["created_at"]),
        )
        db.commit()
        return cursor.rowcount
