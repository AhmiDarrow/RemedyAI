"""Durable life-task evidence — review after, resume a half-done drive.

Each drive writes one JSON file under ``~/.remedy/life_tasks/``. A crash
cannot tear it (atomic replace). The owner can read what was intended vs
observed; ``life_drive(task_id=…)`` continues from the first unfinished
step. Checkpoints stay checkpoints on resume — they never auto-run.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from remedy.core.atomic_json import write_json_atomic
from remedy.home import default_home

_SAFE_ID = re.compile(r"^[a-zA-Z0-9._-]{4,80}$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _dir(home: Path | str | None) -> Path:
    base = Path(home).expanduser() if home else default_home()
    d = base / "life_tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(task_id: str, home: Path | str | None) -> Path:
    tid = (task_id or "").strip()
    if not _SAFE_ID.match(tid):
        raise ValueError("invalid life-task id")
    return _dir(home) / f"{tid}.json"


def save_life_task(
    result: dict[str, Any],
    *,
    source_steps: list[dict[str, Any]] | None = None,
    session_id: str | None = None,
    home: Path | str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Write *result* (from drive_life_task) plus the original step specs."""
    tid = (task_id or "").strip() or ("lt_" + uuid4().hex[:12])
    rec = {
        "id": tid,
        "goal": str(result.get("goal") or ""),
        "status": str(result.get("status") or "blocked"),
        "ok": bool(result.get("ok")),
        "session_id": (session_id or "").strip() or None,
        "markdown": str(result.get("markdown") or ""),
        "steps": list(result.get("steps") or []),
        "source_steps": list(source_steps or []),
        "updated_at": _now(),
        "created_at": str(result.get("created_at") or _now()),
    }
    existing = load_life_task(tid, home=home)
    if existing and existing.get("created_at"):
        rec["created_at"] = existing["created_at"]
    write_json_atomic(_path(tid, home), rec, ensure_ascii=False)
    out = dict(result)
    out["task_id"] = tid
    out["created_at"] = rec["created_at"]
    return out


def load_life_task(
    task_id: str, *, home: Path | str | None = None
) -> dict[str, Any] | None:
    try:
        p = _path(task_id, home)
    except ValueError:
        return None
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def list_life_tasks(
    *,
    session_id: str | None = None,
    home: Path | str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    folder = _dir(home)
    for p in sorted(folder.glob("lt_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        rec = load_life_task(p.stem, home=home)
        if not rec:
            continue
        if session_id and rec.get("session_id") != session_id:
            continue
        rows.append(
            {
                "id": rec.get("id"),
                "goal": rec.get("goal"),
                "status": rec.get("status"),
                "ok": rec.get("ok"),
                "updated_at": rec.get("updated_at"),
            }
        )
        if len(rows) >= max(1, min(50, int(limit or 20))):
            break
    return rows


def remaining_source_steps(rec: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Steps still to run, plus halt reason if the next one is an owner moment."""
    source = list(rec.get("source_steps") or [])
    done_n = 0
    for st in rec.get("steps") or []:
        if not isinstance(st, dict):
            break
        if st.get("status") == "done":
            done_n += 1
            continue
        if st.get("status") == "need_you":
            return source[done_n:], "need_you"
        break
    return source[done_n:], ""
