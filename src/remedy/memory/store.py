"""Persistent memory backend with SQLite + FTS5 and explicit handoff support.

Inspired by Hermes' memory system, adapted as a clean standalone module.
Supports cross-session search, user modeling, and structured handoff notes
critical for the Remedy/Reme companion experience.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from remedy.models import (
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
        self._db: Optional[sqlite3.Connection] = None

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
        entry.updated_at = datetime.utcnow()

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

    async def get(self, entry_id: str | UUID) -> Optional[MemoryEntry]:
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
        self, query: str, limit: int = 20, entry_type: Optional[MemoryEntryType] = None
    ) -> list[MemoryEntry]:
        """Full-text search across title, content, and tags."""
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

    async def get_handoff(self, handoff_id: str | UUID) -> Optional[HandoffNote]:
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

    async def get_session_summary(self, session_id: str) -> Optional[SessionSummary]:
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
