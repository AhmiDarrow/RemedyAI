"""In-process fan-out + SQLite append log under ~/.remedy/events.db."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from remedy.events.types import Event, EventType

_MEMORY_URI = ":memory:"


class EventBus:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._subs: list[Callable[[Event], None]] = []
        self._lock = threading.RLock()
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def _db(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        raw = self._db_path
        if raw is None:
            from remedy.core.security import get_home_dir

            path = get_home_dir() / "events.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            uri = str(path)
        elif str(raw) == _MEMORY_URI:
            uri = _MEMORY_URI
        else:
            path = Path(raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            uri = str(path)
        conn = sqlite3.connect(uri, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_turn ON events(turn_id)")
        conn.commit()
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            self._subs.append(fn)

    def emit(self, event: Event) -> None:
        from remedy.core.optimization_telemetry import span

        with span("event", event_type=str(event.event_type.value)):
            with self._lock:
                subs = list(self._subs)
                conn = self._db()
                conn.execute(
                    "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.session_id,
                        event.turn_id,
                        event.timestamp.isoformat(),
                        event.event_type.value,
                        event.actor,
                        json.dumps(dict(event.payload), default=str),
                    ),
                )
                conn.commit()
            for fn in subs:
                try:
                    fn(event)
                except Exception:
                    continue

    def emit_simple(
        self,
        event_type: EventType,
        *,
        session_id: str,
        turn_id: str,
        actor: str = "remedy",
        **payload: Any,
    ) -> Event:
        ev = Event(
            event_type=event_type,
            session_id=session_id,
            turn_id=turn_id,
            actor=actor,
            payload=payload,
        )
        self.emit(ev)
        return ev

    def prune_older_than_days(self, days: int) -> int:
        """Drop events older than *days*. 0 disables. Does not touch other homes."""
        if int(days or 0) <= 0:
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=int(days))).isoformat()
        with self._lock:
            conn = self._db()
            cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            conn.commit()
            return int(cur.rowcount or 0)

    def for_turn(self, turn_id: str) -> list[Event]:
        with self._lock:
            conn = self._db()
            rows = conn.execute(
                "SELECT event_id, session_id, turn_id, timestamp, event_type, actor, payload_json "
                "FROM events WHERE turn_id = ? ORDER BY timestamp",
                (turn_id,),
            ).fetchall()
        out: list[Event] = []
        for row in rows:
            loaded = json.loads(row[6] or "{}")
            payload: dict[str, Any] = loaded if isinstance(loaded, dict) else {"_raw": loaded}
            out.append(
                Event(
                    event_id=str(row[0]),
                    session_id=str(row[1]),
                    turn_id=str(row[2]),
                    timestamp=datetime.fromisoformat(str(row[3])),
                    event_type=EventType(str(row[4])),
                    actor=str(row[5]),
                    payload=payload,
                )
            )
        return out


_BUS: EventBus | None = None
_BUS_LOCK = threading.Lock()


def default_bus() -> EventBus:
    global _BUS
    with _BUS_LOCK:
        if _BUS is None:
            # Pytest must not append to the owner's ~/.remedy/events.db.
            path: Path | str | None = _MEMORY_URI if os.environ.get("PYTEST_CURRENT_TEST") else None
            _BUS = EventBus(db_path=path)
        return _BUS
