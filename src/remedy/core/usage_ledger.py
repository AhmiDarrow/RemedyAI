"""Durable multiprovider usage ledger (local SQLite under ~/.remedy).

Stores per-turn usage events tagged by provider/model so mid-session provider
switches do not rewrite history. NanoToken calibrator still lives in-memory;
this is the time-series for dashboards.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    ts REAL NOT NULL,
    session_id TEXT,
    provider TEXT,
    model TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'estimate',
    run_id TEXT,
    turn_index INTEGER,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_id);
CREATE INDEX IF NOT EXISTS idx_usage_provider ON usage_events(provider);
"""

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_path: Path | None = None


def _db_path(home: Path | str | None = None) -> Path:
    if home is not None:
        base = Path(home).expanduser()
    else:
        try:
            from remedy.core.security import get_home_dir

            base = get_home_dir()
        except Exception:
            base = Path("~/.remedy").expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base / "usage.db"


def _get_conn(home: Path | str | None = None) -> sqlite3.Connection:
    global _conn, _path
    path = _db_path(home)
    with _lock:
        if _conn is not None and _path == path:
            return _conn
        if _conn is not None:
            with suppress(Exception):
                _conn.close()
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        _conn = conn
        _path = path
        return conn


def record_usage_event(
    *,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    source: str = "estimate",
    run_id: str | None = None,
    turn_index: int | None = None,
    meta: dict[str, Any] | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Append one usage event; returns the stored row as a dict."""
    from remedy.core.usage import estimate_cost_usd

    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    tt = max(0, int(total_tokens or 0)) or (pt + ct)
    cost = float(estimated_cost_usd or 0.0)
    meta = meta or {}
    if cost <= 0 and (pt or ct):
        cost = estimate_cost_usd(
            prompt_tokens=pt,
            completion_tokens=ct,
            model=model,
            provider=provider,
            cache_hit_tokens=int(meta.get("cache_hit_tokens") or 0),
            cache_miss_tokens=(
                int(meta["cache_miss_tokens"])
                if meta.get("cache_miss_tokens") is not None
                else None
            ),
        )
    row = {
        "id": str(uuid4()),
        "ts": time.time(),
        "session_id": (session_id or "").strip() or None,
        "provider": (provider or "").strip().lower() or None,
        "model": (model or "").strip() or None,
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt,
        "estimated_cost_usd": round(cost, 6),
        "source": source or "estimate",
        "run_id": run_id,
        "turn_index": turn_index,
        "meta": meta,
    }
    conn = _get_conn(home)
    with _lock:
        conn.execute(
            """
            INSERT INTO usage_events (
                id, ts, session_id, provider, model,
                prompt_tokens, completion_tokens, total_tokens,
                estimated_cost_usd, source, run_id, turn_index, meta
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                row["id"],
                row["ts"],
                row["session_id"],
                row["provider"],
                row["model"],
                row["prompt_tokens"],
                row["completion_tokens"],
                row["total_tokens"],
                row["estimated_cost_usd"],
                row["source"],
                row["run_id"],
                row["turn_index"],
                json.dumps(row["meta"], default=str),
            ),
        )
        conn.commit()
    return row


def summary(
    *,
    range_days: float = 7.0,
    session_id: str | None = None,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Aggregate tokens/cost by provider (and model) for a time range."""
    since = time.time() - max(0.01, float(range_days)) * 86400.0
    conn = _get_conn(home)
    params: list[Any] = [since]
    where = "ts >= ?"
    if session_id:
        where += " AND session_id = ?"
        params.append(session_id)
    with _lock:
        rows = conn.execute(
            f"""
            SELECT provider, model,
                   SUM(prompt_tokens) AS pt,
                   SUM(completion_tokens) AS ct,
                   SUM(total_tokens) AS tt,
                   SUM(estimated_cost_usd) AS cost,
                   COUNT(*) AS n
            FROM usage_events
            WHERE {where}
            GROUP BY provider, model
            ORDER BY tt DESC
            """,
            params,
        ).fetchall()
        total_row = conn.execute(
            f"""
            SELECT SUM(prompt_tokens), SUM(completion_tokens),
                   SUM(total_tokens), SUM(estimated_cost_usd), COUNT(*)
            FROM usage_events WHERE {where}
            """,
            params,
        ).fetchone()
    by_provider: dict[str, dict[str, Any]] = {}
    by_model: list[dict[str, Any]] = []
    for r in rows:
        prov = r["provider"] or "unknown"
        mod = r["model"] or "unknown"
        entry = {
            "provider": prov,
            "model": mod,
            "prompt_tokens": int(r["pt"] or 0),
            "completion_tokens": int(r["ct"] or 0),
            "total_tokens": int(r["tt"] or 0),
            "estimated_cost_usd": round(float(r["cost"] or 0), 6),
            "events": int(r["n"] or 0),
        }
        by_model.append(entry)
        bucket = by_provider.setdefault(
            prov,
            {
                "provider": prov,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
                "events": 0,
            },
        )
        bucket["prompt_tokens"] += entry["prompt_tokens"]
        bucket["completion_tokens"] += entry["completion_tokens"]
        bucket["total_tokens"] += entry["total_tokens"]
        bucket["estimated_cost_usd"] = round(
            float(bucket["estimated_cost_usd"]) + float(entry["estimated_cost_usd"]), 6
        )
        bucket["events"] += entry["events"]
    return {
        "range_days": range_days,
        "session_id": session_id,
        "totals": {
            "prompt_tokens": int(total_row[0] or 0) if total_row else 0,
            "completion_tokens": int(total_row[1] or 0) if total_row else 0,
            "total_tokens": int(total_row[2] or 0) if total_row else 0,
            "estimated_cost_usd": round(float(total_row[3] or 0), 6) if total_row else 0.0,
            "events": int(total_row[4] or 0) if total_row else 0,
        },
        "by_provider": list(by_provider.values()),
        "by_model": by_model,
    }


def series(
    *,
    range_days: float = 30.0,
    group: str = "provider",
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Daily time series grouped by provider (or model)."""
    since = time.time() - max(0.01, float(range_days)) * 86400.0
    group_col = "provider" if group != "model" else "model"
    conn = _get_conn(home)
    with _lock:
        rows = conn.execute(
            f"""
            SELECT date(ts, 'unixepoch', 'localtime') AS day,
                   COALESCE({group_col}, 'unknown') AS grp,
                   SUM(total_tokens) AS tt,
                   SUM(estimated_cost_usd) AS cost,
                   COUNT(*) AS n
            FROM usage_events
            WHERE ts >= ?
            GROUP BY day, grp
            ORDER BY day ASC
            """,
            (since,),
        ).fetchall()
    points = [
        {
            "day": r["day"],
            "group": r["grp"],
            "total_tokens": int(r["tt"] or 0),
            "estimated_cost_usd": round(float(r["cost"] or 0), 6),
            "events": int(r["n"] or 0),
        }
        for r in rows
    ]
    return {"range_days": range_days, "group": group_col, "points": points}


def session_usage(
    session_id: str,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    return summary(range_days=3650.0, session_id=session_id, home=home)


def delete_session_events(
    session_id: str,
    *,
    home: Path | str | None = None,
) -> int:
    """Remove all usage events for a session (privacy cascade on chat delete).

    Returns the number of rows deleted. Empty/missing session_id is a no-op.
    """
    sid = (session_id or "").strip()
    if not sid:
        return 0
    conn = _get_conn(home)
    with _lock:
        cur = conn.execute(
            "DELETE FROM usage_events WHERE session_id = ?",
            (sid,),
        )
        conn.commit()
        return int(cur.rowcount or 0)


def close_conn() -> None:
    """Close the process-global connection (tests / home switch)."""
    global _conn, _path
    with _lock:
        if _conn is not None:
            with suppress(Exception):
                _conn.close()
        _conn = None
        _path = None
