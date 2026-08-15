"""Persistent memory backend with SQLite + FTS5 and explicit handoff support.

Supports cross-session search, user modeling, and structured handoff notes
critical for the Remedy/Reme companion experience.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
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

# Cap persisted tool bodies so mail/page text does not accumulate unbounded in memory.db.
_TOOL_RESULT_CONTENT_MAX = 4_000


def _cap_tool_results_for_storage(results: list[Any] | None) -> list[Any]:
    """Return a storage-safe copy of tool_results (size-capped, secret-scrubbed)."""
    if not results:
        return []
    try:
        from remedy.core.provider_sanitize import sanitize_message
    except Exception:
        sanitize_message = None  # type: ignore[assignment]

    out: list[Any] = []
    for item in results[:80]:
        if not isinstance(item, dict):
            out.append(item)
            continue
        row = dict(item)
        content = row.get("content")
        if isinstance(content, str) and len(content) > _TOOL_RESULT_CONTENT_MAX:
            row["content"] = content[: _TOOL_RESULT_CONTENT_MAX - 1] + "…"
            row["content_truncated"] = True
        if sanitize_message is not None:
            # Reuse provider scrub on a synthetic tool message.
            scrubbed = sanitize_message(
                {
                    "role": "tool",
                    "content": row.get("content") if isinstance(row.get("content"), str) else str(row.get("content") or ""),
                }
            )
            if isinstance(scrubbed.get("content"), str):
                row["content"] = scrubbed["content"]
        out.append(row)
    return out

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

CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id);
CREATE INDEX IF NOT EXISTS idx_user_facts_user_cat ON user_facts(user_id, category);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Session',
    model TEXT,
    agent TEXT,
    project_path TEXT,
    llm_provider TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    origin_channel TEXT,
    external_chat_id TEXT,
    external_user TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at);
-- idx_chat_sessions_origin is created in _migrate_schema after columns exist
-- (CREATE INDEX here breaks older DBs that lack origin_channel until ALTER).

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
        # Serialize multi-coroutine access (messenger + desktop concurrent writes).
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._db_path

    # -- lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        """Open the database and ensure the schema exists."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            # Optional SQLCipher (memory_encrypt) — no-op when unavailable / off
            with contextlib.suppress(Exception):
                from remedy.core.retention import apply_memory_encryption_pragma
                from remedy.interfaces.config import load_config

                apply_memory_encryption_pragma(self._db, load_config() or {})
            # Speed-oriented pragmas (safe for single-writer desktop agent use).
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA temp_store=MEMORY")
            self._db.execute("PRAGMA cache_size=-65536")  # ~64 MiB page cache
            self._db.execute("PRAGMA mmap_size=268435456")  # 256 MiB mmap when OS allows
            self._db.execute("PRAGMA busy_timeout=5000")
            self._db.executescript(_SCHEMA)
            self._migrate_schema()
            self._db.commit()

    def _migrate_schema(self) -> None:
        """Best-effort additive migrations for existing desktop DBs."""
        if self._db is None:
            return
        cols = {
            r[1]
            for r in self._db.execute("PRAGMA table_info(chat_sessions)").fetchall()
        }
        if "llm_provider" not in cols:
            self._db.execute(
                "ALTER TABLE chat_sessions ADD COLUMN llm_provider TEXT"
            )
        for col, decl in (
            ("origin_channel", "TEXT"),
            ("external_chat_id", "TEXT"),
            ("external_user", "TEXT"),
        ):
            if col not in cols:
                self._db.execute(
                    f"ALTER TABLE chat_sessions ADD COLUMN {col} {decl}"
                )
        # Index for messenger session lookup (safe to re-run)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_origin "
            "ON chat_sessions(origin_channel, external_chat_id)"
        )

    async def close(self) -> None:
        with self._locked():
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

    def _locked(self) -> threading.RLock:
        """Hold across multi-statement transactions (execute + commit)."""
        return self._lock

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
        entry.updated_at = datetime.now(UTC)
        with self._locked():
            db = self._ensure_db()
            return self._upsert_unlocked(db, entry)

    def _upsert_unlocked(self, db: sqlite3.Connection, entry: MemoryEntry) -> MemoryEntry:
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

    # Threshold: disable per-row FTS triggers and rebuild once after bulk write.
    _BULK_FTS_THRESHOLD = 50

    _FTS_TRIGGER_SQL = (
        """
        CREATE TRIGGER IF NOT EXISTS memory_fts_insert AFTER INSERT ON memory_entries BEGIN
            INSERT INTO memory_fts(rowid, title, content, tags)
            VALUES (new.rowid, new.title, new.content, new.tags);
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS memory_fts_delete AFTER DELETE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
            VALUES ('delete', old.rowid, old.title, old.content, old.tags);
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS memory_fts_update AFTER UPDATE ON memory_entries BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, title, content, tags)
            VALUES ('delete', old.rowid, old.title, old.content, old.tags);
            INSERT INTO memory_fts(rowid, title, content, tags)
            VALUES (new.rowid, new.title, new.content, new.tags);
        END;
        """,
    )

    def _drop_fts_triggers(self, db: sqlite3.Connection) -> None:
        for name in ("memory_fts_insert", "memory_fts_delete", "memory_fts_update"):
            db.execute(f"DROP TRIGGER IF EXISTS {name}")

    def _create_fts_triggers(self, db: sqlite3.Connection) -> None:
        for sql in self._FTS_TRIGGER_SQL:
            db.execute(sql)

    async def upsert_many(self, entries: list[MemoryEntry]) -> int:
        """Batch upsert in a single transaction (faster under high write volume).

        For batches larger than ``_BULK_FTS_THRESHOLD``, FTS triggers are dropped
        for the write, then the FTS index is rebuilt once (avoids N trigger
        updates). Smaller batches keep normal per-row triggers.
        Returns the number of entries written.
        """
        if not entries:
            return 0
        with self._locked():
            return self._upsert_many_unlocked(entries)

    def _upsert_many_unlocked(self, entries: list[MemoryEntry]) -> int:
        db = self._ensure_db()
        now = datetime.now(UTC)
        rows = []
        for entry in entries:
            entry.updated_at = now
            rows.append(
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
                )
            )
        bulk = len(rows) >= self._BULK_FTS_THRESHOLD
        try:
            if bulk:
                self._drop_fts_triggers(db)
            db.executemany(
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
                rows,
            )
            if bulk:
                db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
                self._create_fts_triggers(db)
            db.commit()
        except Exception:
            db.rollback()
            # Always try to restore triggers after a failed bulk path.
            if bulk:
                try:
                    self._create_fts_triggers(db)
                    db.commit()
                except Exception:
                    pass
            raise
        return len(rows)

    async def rebuild_fts(self) -> int:
        """Rebuild the FTS5 index from ``memory_entries`` (recovery / batch reindex).

        Useful after bulk imports or if the external-content FTS index drifts.
        Returns the number of rows re-indexed.
        """
        with self._locked():
            db = self._ensure_db()
            # FTS5 external-content rebuild from the content table.
            db.execute("INSERT INTO memory_fts(memory_fts) VALUES('rebuild')")
            db.commit()
            row = db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()
            return int(row[0]) if row else 0

    async def get(self, entry_id: str | UUID) -> MemoryEntry | None:
        db = self._ensure_db()
        row = db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (str(entry_id),)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_entry(row)

    async def delete(self, entry_id: str | UUID) -> bool:
        with self._locked():
            db = self._ensure_db()
            cursor = db.execute(
                "DELETE FROM memory_entries WHERE id = ?", (str(entry_id),)
            )
            db.commit()
            return cursor.rowcount > 0

    async def list_by_type(
        self, entry_type: MemoryEntryType, limit: int = 50, offset: int = 0
    ) -> list[MemoryEntry]:
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
                "SELECT * FROM memory_entries WHERE entry_type = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (entry_type.value, limit, offset),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def list_recent(self, limit: int = 50) -> list[MemoryEntry]:
        with self._locked():
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
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
                "SELECT * FROM memory_entries WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (session_id, limit, offset),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def list_important(self, threshold: float = 0.7, limit: int = 50) -> list[MemoryEntry]:
        with self._locked():
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
        """Full-text search across title, content, and tags.

        Falls back to :meth:`search_simple` when FTS5 MATCH rejects the query
        (e.g. special characters) so callers always get a safe result set.
        """
        query = sanitize_search_query(query, max_length=500)
        if not query.strip():
            return []
        type_filter = ""
        params: list[Any] = []

        if entry_type is not None:
            type_filter = "AND memory_entries.entry_type = ?"
            params = [query, entry_type.value, limit]
        else:
            params = [query, limit]

        try:
            with self._locked():
                db = self._ensure_db()
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
        except sqlite3.OperationalError as exc:
            # Invalid MATCH syntax or missing FTS — degrade gracefully (no recursion).
            logger = __import__("logging").getLogger(__name__)
            logger.debug(
                "FTS MATCH failed (%s); falling back to LIKE for query=%r",
                exc,
                query[:80],
            )
            hits = await self._search_like(query, limit=limit)
            if entry_type is None:
                return hits
            return [h for h in hits if h.entry_type == entry_type]

    async def _search_like(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        """Parameterized LIKE fallback (escapes % and _). Never uses FTS."""
        q = (query or "").strip()
        if not q:
            return []
        safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_q = f"%{safe}%"
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
            "SELECT * FROM memory_entries WHERE title LIKE ? ESCAPE '\\' "
            "OR content LIKE ? ESCAPE '\\' "
            "ORDER BY created_at DESC LIMIT ?",
                (like_q, like_q, limit),
            ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def search_simple(self, query: str, limit: int = 20) -> list[MemoryEntry]:
        """Tolerant search: try FTS MATCH, then parameterized LIKE.

        Prefer :meth:`search` when you want FTS ranking; this is the safe
        fallback path used by older callers.
        """
        q = (query or "").strip()
        if not q:
            return []
        try:
            hits = await self.search(q, limit=limit)
            if hits:
                return hits
        except Exception:
            pass
        return await self._search_like(q, limit=limit)

    # -- handoff notes -------------------------------------------------------

    async def create_handoff(self, note: HandoffNote) -> HandoffNote:
        """Persist a handoff note and optionally save it as a memory entry."""
        with self._locked():
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

        # Stable memory id = handoff id so re-saving the same note does not
        # duplicate rows in memory_entries.
        memory_entry = MemoryEntry(
            id=note.id if getattr(note, "id", None) is not None else uuid4(),
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
        q = (query or "").strip()
        safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like_q = f"%{safe}%"
        rows = db.execute(
            "SELECT * FROM handoff_notes WHERE title LIKE ? ESCAPE '\\' "
            "OR content LIKE ? ESCAPE '\\' "
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
        with self._locked():
            db = self._ensure_db()
            cursor = db.execute(
                "UPDATE handoff_notes SET acknowledged = 1 WHERE id = ?",
                (str(handoff_id),),
            )
            db.commit()
            return cursor.rowcount > 0

    # -- session summaries ---------------------------------------------------

    async def save_session_summary(self, summary: SessionSummary) -> SessionSummary:
        with self._locked():
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
        with self._locked():
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
        with self._locked():
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
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
                "SELECT * FROM user_facts WHERE user_id = ? AND (fact LIKE ? OR category LIKE ?) "
                "ORDER BY reference_count DESC LIMIT ?",
                (user_id, f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- chat sessions --------------------------------------------------------

    def _row_to_session(self, row: sqlite3.Row) -> ChatSession:
        keys = row.keys()
        return ChatSession(
            id=row["id"],
            title=row["title"],
            model=row["model"],
            agent=row["agent"],
            project_path=row["project_path"],
            llm_provider=row["llm_provider"] if "llm_provider" in keys else None,
            message_count=row["message_count"],
            origin_channel=row["origin_channel"] if "origin_channel" in keys else None,
            external_chat_id=row["external_chat_id"] if "external_chat_id" in keys else None,
            external_user=row["external_user"] if "external_user" in keys else None,
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
        session.created_at = datetime.now(UTC)
        session.updated_at = datetime.now(UTC)
        with self._locked():
            db = self._ensure_db()
            try:
                db.execute(
                    """INSERT INTO chat_sessions (id, title, model, agent, project_path,
                       llm_provider, message_count, origin_channel, external_chat_id,
                       external_user, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session.id, session.title, session.model, session.agent,
                        session.project_path, session.llm_provider, session.message_count,
                        session.origin_channel, session.external_chat_id, session.external_user,
                        session.created_at.isoformat(), session.updated_at.isoformat(),
                    ),
                )
                db.commit()
                return session
            except sqlite3.IntegrityError:
                # Concurrent create (messenger dual-delivery / race) — return existing.
                db.rollback()
        existing = await self.get_chat_session(session.id)
        if existing is not None:
            return existing
        raise sqlite3.IntegrityError(f"chat_session create race for {session.id}")

    async def get_chat_session(self, session_id: str) -> ChatSession | None:
        with self._locked():
            db = self._ensure_db()
            row = db.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    async def update_chat_session(self, session_id: str, **fields: Any) -> ChatSession | None:
        with self._locked():
            db = self._ensure_db()
            allowed = {
                "title",
                "model",
                "agent",
                "project_path",
                "llm_provider",
                "message_count",
                "origin_channel",
                "external_chat_id",
                "external_user",
            }
            updates: dict[str, Any] = {}
            for k, v in fields.items():
                if k not in allowed:
                    continue
                # project_path may be cleared to NULL (no-project sessions)
                if k == "project_path":
                    if v is None or str(v).strip() in ("", ".", "./"):
                        updates[k] = None
                    else:
                        updates[k] = v
                elif v is not None:
                    updates[k] = v
            if not updates:
                row = db.execute(
                    "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_session(row)
            updates["updated_at"] = datetime.now(UTC).isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [session_id]
            db.execute(
                f"UPDATE chat_sessions SET {set_clause} WHERE id = ?", values
            )
            db.commit()
            row = db.execute(
                "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    async def delete_chat_session(self, session_id: str) -> bool:
        with self._locked():
            db = self._ensure_db()
            db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            cursor = db.execute(
                "DELETE FROM chat_sessions WHERE id = ?", (session_id,)
            )
            db.commit()
            return cursor.rowcount > 0

    def purge_sessions_older_than_days(self, max_age_days: int) -> int:
        """Delete chat sessions whose updated_at is older than *max_age_days*.

        Synchronous for startup retention. Cascades messages + disk artifacts.
        Returns count removed.
        """
        if max_age_days <= 0:
            return 0
        from datetime import timedelta

        cutoff = (datetime.now(UTC) - timedelta(days=int(max_age_days))).isoformat()
        removed = 0
        purged_ids: list[str] = []
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
                "SELECT id FROM chat_sessions WHERE updated_at < ? OR "
                "(updated_at IS NULL AND created_at < ?)",
                (cutoff, cutoff),
            ).fetchall()
            ids = [
                str(row[0] if not isinstance(row, sqlite3.Row) else row["id"])
                for row in rows
            ]
            if ids:
                # Set-based deletes (faster than per-row for large histories).
                placeholders = ",".join("?" * len(ids))
                db.execute(
                    f"DELETE FROM chat_messages WHERE session_id IN ({placeholders})",
                    ids,
                )
                with contextlib.suppress(sqlite3.Error):
                    db.execute(
                        f"DELETE FROM session_summaries WHERE session_id IN ({placeholders})",
                        ids,
                    )
                cur = db.execute(
                    f"DELETE FROM chat_sessions WHERE id IN ({placeholders})",
                    ids,
                )
                removed = int(cur.rowcount or 0)
                purged_ids = ids
                if removed:
                    db.commit()
        # Disk cascade outside DB lock (attachments / plans / undo / middleman).
        if purged_ids:
            home = None
            with contextlib.suppress(Exception):
                home = self._db_path.expanduser().resolve().parent
            for sid in purged_ids:
                with contextlib.suppress(Exception):
                    from remedy.core.session_reset import purge_session_disk_artifacts

                    purge_session_disk_artifacts(sid, home)
                with contextlib.suppress(Exception):
                    from remedy.memory.middleman import forget_session_middleman

                    forget_session_middleman(sid)
        return removed

    async def clear_chat_messages(self, session_id: str) -> int:
        """Delete all messages in a session but keep the session row (in-place reset).

        Resets message_count to 0 and bumps updated_at. Does not change project,
        model, or provider. Returns number of messages removed.
        """
        now = datetime.now(UTC).isoformat()
        with self._locked():
            db = self._ensure_db()
            row = db.execute(
                "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return 0
            cursor = db.execute(
                "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
            )
            deleted = int(cursor.rowcount or 0)
            # Drop Memory Harness session summary for this chat (fresh slate).
            with contextlib.suppress(sqlite3.Error):
                db.execute(
                    "DELETE FROM session_summaries WHERE session_id = ?",
                    (session_id,),
                )
            db.execute(
                "UPDATE chat_sessions SET message_count = 0, updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            db.commit()
            return deleted

    async def list_chat_sessions(
        self, limit: int = 50, offset: int = 0
    ) -> list[ChatSession]:
        with self._locked():
            db = self._ensure_db()
            rows = db.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    async def find_session_by_external(
        self,
        origin_channel: str,
        external_chat_id: str,
    ) -> ChatSession | None:
        """Look up a messenger session by platform identity."""
        ch = str(origin_channel or "").strip().lower()
        ext = str(external_chat_id or "").strip()
        if not ch or not ext:
            return None
        with self._locked():
            db = self._ensure_db()
            row = db.execute(
                "SELECT * FROM chat_sessions WHERE origin_channel = ? AND external_chat_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (ch, ext),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    # -- chat messages --------------------------------------------------------

    async def add_chat_message(self, msg: ChatMessage) -> ChatMessage:
        msg.created_at = datetime.now(UTC)
        safe_results = _cap_tool_results_for_storage(
            list(msg.tool_results) if msg.tool_results else []
        )
        # Keep in-memory object aligned with what we persist.
        msg.tool_results = safe_results
        with self._locked():
            db = self._ensure_db()
            db.execute(
                """INSERT INTO chat_messages (id, session_id, role, content, thinking,
                   tool_calls, tool_results, model, agent, tokens, created_at, reverted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(msg.id), msg.session_id, msg.role.value, msg.content,
                    msg.thinking, json.dumps(msg.tool_calls),
                    json.dumps(safe_results), msg.model, msg.agent,
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
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        *,
        include_reverted: bool = False,
    ) -> list[ChatMessage]:
        """Return up to ``limit`` messages for a session in chronological order.

        Uses a newest-first window (then reorders ASC) so ``limit`` keeps the
        **latest** turns for long sessions — not the oldest page.
        ``offset`` skips that many newest messages (for older-page loads).
        """
        with self._locked():
            db = self._ensure_db()
            where = "session_id = ?"
            params: list[Any] = [session_id]
            if not include_reverted:
                where += " AND reverted = 0"
            # Nested select: take newest first, then chronological for callers.
            # Explicit rowid AS _rid — SELECT * does not expose rowid to the outer query.
            sql = (
                f"SELECT * FROM ("
                f"  SELECT *, rowid AS _rid FROM chat_messages WHERE {where} "
                f"  ORDER BY created_at DESC, rowid DESC "
                f"  LIMIT ? OFFSET ?"
                f") AS recent "
                f"ORDER BY created_at ASC, _rid ASC"
            )
            params.extend([limit, offset])
            rows = db.execute(sql, params).fetchall()
            return [self._row_to_message(r) for r in rows]

    async def get_chat_message(self, msg_id: str) -> ChatMessage | None:
        with self._locked():
            db = self._ensure_db()
            row = db.execute(
                "SELECT * FROM chat_messages WHERE id = ?", (msg_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_message(row)

    def _sync_session_message_count(
        self, db: sqlite3.Connection, session_id: str
    ) -> int:
        """Recount non-reverted messages and write message_count (caller holds lock)."""
        row = db.execute(
            "SELECT COUNT(*) AS c FROM chat_messages "
            "WHERE session_id = ? AND reverted = 0",
            (session_id,),
        ).fetchone()
        count = int(row["c"] if row is not None else 0)
        db.execute(
            "UPDATE chat_sessions SET message_count = ?, updated_at = ? WHERE id = ?",
            (count, datetime.now(UTC).isoformat(), session_id),
        )
        return count

    async def revert_message(self, msg_id: str) -> bool:
        """Soft-delete one message and resync session message_count."""
        with self._locked():
            db = self._ensure_db()
            row = db.execute(
                "SELECT session_id, reverted FROM chat_messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
            if row is None:
                return False
            if int(row["reverted"] or 0):
                return False
            sid = str(row["session_id"] or "")
            cursor = db.execute(
                "UPDATE chat_messages SET reverted = 1 WHERE id = ? AND reverted = 0",
                (msg_id,),
            )
            if cursor.rowcount <= 0:
                db.rollback()
                return False
            if sid:
                self._sync_session_message_count(db, sid)
            db.commit()
            return True

    async def revert_from(self, session_id: str, msg_id: str) -> int:
        """Soft-delete this message and all later messages; resync message_count.

        Time-travel / edit-and-resend rely on ``message_count`` matching the
        visible (non-reverted) transcript — previously the counter only ever
        increased, so session list badges stayed inflated after rollbacks.

        Cut uses ``(created_at, rowid)`` so same-timestamp bursts (common when
        several messages land in one second) still roll back from the chosen
        message forward, not the whole second.
        """
        with self._locked():
            db = self._ensure_db()
            target = db.execute(
                "SELECT created_at, rowid AS _rid FROM chat_messages "
                "WHERE id = ? AND session_id = ?",
                (msg_id, session_id),
            ).fetchone()
            if target is None:
                return 0
            cut_at = target["created_at"]
            cut_rid = int(target["_rid"])
            cursor = db.execute(
                "UPDATE chat_messages SET reverted = 1 "
                "WHERE session_id = ? AND reverted = 0 AND ("
                "  created_at > ? OR (created_at = ? AND rowid >= ?)"
                ")",
                (session_id, cut_at, cut_at, cut_rid),
            )
            n = int(cursor.rowcount or 0)
            if n > 0:
                self._sync_session_message_count(db, session_id)
            db.commit()
            return n
