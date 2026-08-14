"""Eternal content-addressed store — the machine memory, not a journal.

Human designs persist *prose* (notes, summaries, recency lists). This store
persists *objects*: immutable SHA-256 bodies + provenance edges. Restart does
not invent a new memory. The same bytes are the same key forever.

Layout (under ``{home}/cas/``):

- ``objects.db`` — WAL SQLite. Rows are never updated in place except
  provenance merge and tombstones. Bodies are not rewritten.
- Hot path stays in ``middleman.MiddlemanMemory`` (RAM indexes). This module
  is the cold / eternal plane: write-through on put, hydrate on session open,
  FTS only on RAM miss.

Forget is a tombstone. Compaction drops old *tool* objects; facts and life
never expire unless tombstoned.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.memory.middleman import MemoryItem, content_key, tokenize

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA cache_size = -8000;

CREATE TABLE IF NOT EXISTS objects (
    key TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'note',
    body TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    ts REAL NOT NULL,
    tombstone INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cas_session
    ON objects(session_id) WHERE tombstone = 0;
CREATE INDEX IF NOT EXISTS idx_cas_kind
    ON objects(kind) WHERE tombstone = 0;
CREATE INDEX IF NOT EXISTS idx_cas_ts
    ON objects(ts DESC);
CREATE INDEX IF NOT EXISTS idx_cas_path
    ON objects(path) WHERE tombstone = 0 AND path != '';

CREATE VIRTUAL TABLE IF NOT EXISTS objects_fts USING fts5(
    body, path, tool, kind,
    content=objects,
    content_rowid=rowid
);

CREATE TRIGGER IF NOT EXISTS cas_fts_insert AFTER INSERT ON objects BEGIN
    INSERT INTO objects_fts(rowid, body, path, tool, kind)
    VALUES (new.rowid, new.body, new.path, new.tool, new.kind);
END;
CREATE TRIGGER IF NOT EXISTS cas_fts_delete AFTER DELETE ON objects BEGIN
    INSERT INTO objects_fts(objects_fts, rowid, body, path, tool, kind)
    VALUES ('delete', old.rowid, old.body, old.path, old.tool, old.kind);
END;
CREATE TRIGGER IF NOT EXISTS cas_fts_update AFTER UPDATE ON objects BEGIN
    INSERT INTO objects_fts(objects_fts, rowid, body, path, tool, kind)
    VALUES ('delete', old.rowid, old.body, old.path, old.tool, old.kind);
    INSERT INTO objects_fts(rowid, body, path, tool, kind)
    VALUES (new.rowid, new.body, new.path, new.tool, new.kind);
END;

CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

# Tool bodies are ephemeral evidence. Facts/life stay.
_TOOL_MAX_AGE_DAYS = 30.0
_TOOL_MAX_COUNT = 8_000
_MAX_OBJECTS = 80_000
_COMPACT_EVERY_S = 86_400.0
_MAX_BODY = 8_000

_cas: EternalCAS | None = None
_cas_lock = threading.Lock()


class EternalCAS:
    """Append-mostly content-addressed object store on disk."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).expanduser()
        self.dir = self.home / "cas"
        self.path = self.dir / "objects.db"
        self._db: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._snap: dict[str, Any] | None = None
        self._open()

    def _open(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.executescript(_SCHEMA)
        db.commit()
        self._db = db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                with suppress(sqlite3.Error):
                    self._db.close()
                self._db = None

    def _invalidate_snap(self) -> None:
        self._snap = None

    def _db_req(self) -> sqlite3.Connection:
        if self._db is None:
            self._open()
        assert self._db is not None
        return self._db

    def put_item(self, item: MemoryItem) -> str:
        """Insert-or-ignore. Same key never rewrites the body. Returns the key."""
        key = item.key or content_key(item.body)
        body = (item.body or "")[:_MAX_BODY]
        if not body.strip():
            return ""
        tags = json.dumps(list(item.tags)[:16], ensure_ascii=False)
        with self._lock:
            db = self._db_req()
            db.execute(
                """
                INSERT OR IGNORE INTO objects
                    (key, kind, body, path, tool, session_id, tags, ts, tombstone)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    key,
                    (item.kind or "note")[:32],
                    body,
                    (item.path or "")[:400],
                    (item.tool or "")[:64],
                    (item.session_id or "")[:80],
                    tags,
                    float(item.ts or time.time()),
                ),
            )
            db.commit()
            self._invalidate_snap()
        return key

    def get(self, key: str) -> MemoryItem | None:
        k = (key or "").strip()
        if not k:
            return None
        if len(k) < 64:
            # short handle prefix
            with self._lock:
                row = self._db_req().execute(
                    "SELECT * FROM objects WHERE key LIKE ? AND tombstone = 0 LIMIT 1",
                    (k + "%",),
                ).fetchone()
        else:
            with self._lock:
                row = self._db_req().execute(
                    "SELECT * FROM objects WHERE key = ? AND tombstone = 0",
                    (k,),
                ).fetchone()
        return _row_item(row) if row else None

    def tombstone(self, key: str) -> bool:
        k = (key or "").strip()
        if not k:
            return False
        with self._lock:
            cur = self._db_req().execute(
                "UPDATE objects SET tombstone = 1 WHERE key = ? AND tombstone = 0",
                (k,),
            )
            self._db_req().commit()
            if cur.rowcount:
                self._invalidate_snap()
            return cur.rowcount > 0

    def fetch_hot(
        self,
        *,
        session_id: str = "",
        limit: int = 2000,
        facts_limit: int = 400,
    ) -> list[MemoryItem]:
        """Session slice + recent eternal facts/life. Newest first."""
        sid = (session_id or "").strip()
        out: list[MemoryItem] = []
        seen: set[str] = set()
        with self._lock:
            db = self._db_req()
            if sid:
                rows = db.execute(
                    """
                    SELECT * FROM objects
                    WHERE tombstone = 0 AND session_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (sid, max(1, int(limit))),
                ).fetchall()
                for row in rows:
                    item = _row_item(row)
                    if item.key not in seen:
                        seen.add(item.key)
                        out.append(item)
            rows = db.execute(
                """
                SELECT * FROM objects
                WHERE tombstone = 0 AND kind IN ('fact', 'life')
                ORDER BY ts DESC
                LIMIT ?
                """,
                (max(1, int(facts_limit)),),
            ).fetchall()
            for row in rows:
                item = _row_item(row)
                if item.key not in seen:
                    seen.add(item.key)
                    out.append(item)
        return out

    def search_fts(
        self,
        query: str,
        *,
        limit: int = 8,
        exclude: Iterable[str] = (),
        kinds: Iterable[str] | None = None,
    ) -> list[MemoryItem]:
        """Cold recall. FTS5, LIKE fallback. Never raises."""
        from remedy.core.security import sanitize_search_query

        try:
            q = sanitize_search_query(query or "", max_length=200)
        except Exception:
            q = " ".join((query or "").split())[:200]
        toks = tokenize(q)
        # OR: a turn about "outline" should hit a fact that also mentions it.
        # AND of chat words misses; ranking below prefers overlap.
        fts_q = " OR ".join(toks[:8]) if toks else q.strip()
        if not fts_q:
            return []
        skip = {s for s in exclude if s}
        kind_set = {k for k in (kinds or []) if k}
        items: list[MemoryItem] = []
        with self._lock:
            db = self._db_req()
            rows: list[sqlite3.Row] = []
            with suppress(sqlite3.OperationalError):
                rows = db.execute(
                    """
                    SELECT objects.* FROM objects
                    JOIN objects_fts ON objects.rowid = objects_fts.rowid
                    WHERE objects.tombstone = 0 AND objects_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (fts_q, max(1, int(limit) + len(skip))),
                ).fetchall()
            if not rows and toks:
                clauses = []
                params: list[Any] = []
                for tok in toks[:4]:
                    safe = tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    clauses.append("body LIKE ? ESCAPE '\\'")
                    params.append(f"%{safe}%")
                params.append(max(1, int(limit) + len(skip)))
                rows = db.execute(
                    f"""
                    SELECT * FROM objects
                    WHERE tombstone = 0 AND ({' OR '.join(clauses)})
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
        scored: list[tuple[int, MemoryItem]] = []
        for row in rows:
            item = _row_item(row)
            if item.key in skip:
                continue
            if kind_set and item.kind not in kind_set:
                continue
            low = (item.body or "").lower()
            overlap = sum(1 for t in toks if t in low)
            scored.append((overlap, item))
        scored.sort(key=lambda p: p[0], reverse=True)
        for _n, item in scored:
            items.append(item)
            if len(items) >= limit:
                break
        return items

    def count(self, *, include_tombstones: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM objects"
        if not include_tombstones:
            sql += " WHERE tombstone = 0"
        with self._lock:
            row = self._db_req().execute(sql).fetchone()
        return int(row[0]) if row else 0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            if self._snap is not None:
                return dict(self._snap)
            db = self._db_req()
            n = int(db.execute("SELECT COUNT(*) FROM objects WHERE tombstone = 0").fetchone()[0])
            kinds: dict[str, int] = {}
            for row in db.execute(
                "SELECT kind, COUNT(*) AS c FROM objects WHERE tombstone = 0 GROUP BY kind"
            ):
                kinds[str(row["kind"])] = int(row["c"])
            self._snap = {"count": n, "kinds": kinds, "path": str(self.path)}
            return dict(self._snap)

    def _meta(self, key: str) -> str:
        with self._lock:
            row = self._db_req().execute(
                "SELECT v FROM meta WHERE k = ?", (key,)
            ).fetchone()
        return str(row["v"]) if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db_req().execute(
                "INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)",
                (key, value),
            )
            self._db_req().commit()

    def due_compact(self) -> bool:
        raw = self._meta("last_compact_at")
        try:
            last = float(raw or 0)
        except (TypeError, ValueError):
            last = 0.0
        if last <= 0:
            return True
        return (time.time() - last) >= _COMPACT_EVERY_S

    def compact(
        self,
        *,
        tool_max_age_days: float = _TOOL_MAX_AGE_DAYS,
        tool_max_count: int = _TOOL_MAX_COUNT,
        max_objects: int = _MAX_OBJECTS,
    ) -> dict[str, int]:
        """Drop old tool objects. Facts/life stay. Hard-delete tombstones."""
        now = time.time()
        cutoff = now - max(1.0, float(tool_max_age_days)) * 86400.0
        dropped = 0
        purged = 0
        with self._lock:
            db = self._db_req()
            cur = db.execute(
                """
                UPDATE objects SET tombstone = 1
                WHERE tombstone = 0 AND kind = 'tool' AND ts < ?
                """,
                (cutoff,),
            )
            dropped += int(cur.rowcount or 0)
            # Cap tool count: tombstone oldest extras
            n_tools = int(
                db.execute(
                    "SELECT COUNT(*) FROM objects WHERE tombstone = 0 AND kind = 'tool'"
                ).fetchone()[0]
            )
            extra = n_tools - int(tool_max_count)
            if extra > 0:
                keys = [
                    str(r["key"])
                    for r in db.execute(
                        """
                        SELECT key FROM objects
                        WHERE tombstone = 0 AND kind = 'tool'
                        ORDER BY ts ASC
                        LIMIT ?
                        """,
                        (extra,),
                    )
                ]
                if keys:
                    db.executemany(
                        "UPDATE objects SET tombstone = 1 WHERE key = ?",
                        [(k,) for k in keys],
                    )
                    dropped += len(keys)
            n_all = int(
                db.execute("SELECT COUNT(*) FROM objects WHERE tombstone = 0").fetchone()[0]
            )
            overflow = n_all - int(max_objects)
            if overflow > 0:
                keys = [
                    str(r["key"])
                    for r in db.execute(
                        """
                        SELECT key FROM objects
                        WHERE tombstone = 0 AND kind = 'tool'
                        ORDER BY ts ASC
                        LIMIT ?
                        """,
                        (overflow,),
                    )
                ]
                if keys:
                    db.executemany(
                        "UPDATE objects SET tombstone = 1 WHERE key = ?",
                        [(k,) for k in keys],
                    )
                    dropped += len(keys)
            cur = db.execute("DELETE FROM objects WHERE tombstone = 1")
            purged = int(cur.rowcount or 0)
            db.commit()
            self._invalidate_snap()
        self._set_meta("last_compact_at", str(now))
        return {"tombstoned": dropped, "purged": purged}

    def hydrate(self, mm: Any, *, session_id: str = "") -> int:
        """Load hot slice into a MiddlemanMemory without write-through."""
        n = 0
        for item in self.fetch_hot(session_id=session_id):
            if mm.load_item(item):
                n += 1
        return n


def _row_item(row: sqlite3.Row) -> MemoryItem:
    try:
        tags_raw = row["tags"]
    except (KeyError, IndexError):
        tags_raw = "[]"
    try:
        tags = tuple(str(t) for t in json.loads(tags_raw or "[]") if t)
    except (TypeError, ValueError, json.JSONDecodeError):
        tags = ()
    return MemoryItem(
        key=str(row["key"]),
        kind=str(row["kind"] or "note"),
        body=str(row["body"] or ""),
        path=str(row["path"] or ""),
        tool=str(row["tool"] or ""),
        session_id=str(row["session_id"] or ""),
        tags=tags,
        ts=float(row["ts"] or 0),
    )


def _default_home() -> Path | None:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    try:
        from remedy.interfaces.config import load_config

        h = (load_config() or {}).get("home_dir")
        if h:
            return Path(str(h)).expanduser()
    except Exception:
        pass
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        return Path.home() / ".remedy"


def configure_cas(home: str | Path | None) -> EternalCAS | None:
    """Bind (or clear) the process-wide eternal store."""
    global _cas
    with _cas_lock:
        if _cas is not None:
            _cas.close()
            _cas = None
        if home is None:
            return None
        _cas = EternalCAS(home)
        return _cas


def ensure_cas(home: str | Path | None = None) -> EternalCAS | None:
    """Return the bound CAS. Opens *home* only outside pytest.

    Tests must call :func:`configure_cas` explicitly so they never touch
    the real ``~/.remedy/cas``.
    """
    existing = get_cas()
    if existing is not None:
        return existing
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    if home:
        return configure_cas(home)
    return get_cas()


def get_cas() -> EternalCAS | None:
    """Process-wide CAS. None in pytest unless :func:`configure_cas` was called."""
    global _cas
    if _cas is not None:
        return _cas
    home = _default_home()
    if home is None:
        return None
    with _cas_lock:
        if _cas is None:
            try:
                _cas = EternalCAS(home)
            except OSError:
                return None
        return _cas



