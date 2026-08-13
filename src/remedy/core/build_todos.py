"""Turn-local build todos — Claude-class checklist the machine owns.

Missions are durable and heavy. This is a lightweight per-project checklist the
model updates every few steps so multi-file work does not stall or skip verify.
Persisted under ``{project}/.remedy-build/todos.json``.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_VALID = frozenset({"pending", "in_progress", "completed", "cancelled"})


@dataclass
class TodoItem:
    """One checklist row."""

    id: str
    content: str
    status: str = "pending"

    def to_public(self) -> dict[str, str]:
        return asdict(self)


def _root_for(runtime: Any) -> Path | None:
    with suppress(Exception):
        raw = runtime.effective_project_path()
        if raw:
            p = Path(raw)
            return p.parent if p.is_file() else p
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if home:
            return Path(home)
    return None


def _path(root: Path) -> Path:
    return root / ".remedy-build" / "todos.json"


def load_todos(runtime: Any = None, *, root: Path | str | None = None) -> list[TodoItem]:
    """Load todos from disk (empty list if none)."""
    base = Path(root) if root else _root_for(runtime)
    if base is None:
        cached = getattr(runtime, "_build_todos", None) if runtime is not None else None
        return list(cached) if isinstance(cached, list) else []
    fp = _path(base)
    if not fp.is_file():
        cached = getattr(runtime, "_build_todos", None) if runtime is not None else None
        return list(cached) if isinstance(cached, list) else []
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[TodoItem] = []
    for row in raw if isinstance(raw, list) else (raw.get("items") or []):
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").strip()
        if not content:
            continue
        status = str(row.get("status") or "pending").strip().lower()
        if status not in _VALID:
            status = "pending"
        items.append(
            TodoItem(
                id=str(row.get("id") or uuid4().hex[:8]),
                content=content[:240],
                status=status,
            )
        )
    return items


def save_todos(
    items: list[TodoItem],
    runtime: Any = None,
    *,
    root: Path | str | None = None,
) -> Path | None:
    """Persist todos. Also stamps runtime._build_todos."""
    if runtime is not None:
        runtime._build_todos = list(items)
    base = Path(root) if root else _root_for(runtime)
    if base is None:
        return None
    fp = _path(base)
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = [t.to_public() for t in items]
    fp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return fp


def upsert_todos(
    runtime: Any,
    items: list[dict[str, Any]] | None,
    *,
    merge: bool = True,
    root: Path | str | None = None,
) -> list[TodoItem]:
    """Create or update todos. merge=True updates by id; False replaces the list."""
    current = load_todos(runtime, root=root) if merge else []
    by_id = {t.id: t for t in current}
    order = [t.id for t in current]
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or raw.get("title") or "").strip()
        if not content:
            continue
        tid = str(raw.get("id") or "").strip() or uuid4().hex[:8]
        status = str(raw.get("status") or "pending").strip().lower()
        if status not in _VALID:
            status = "pending"
        if tid in by_id:
            prev = by_id[tid]
            if content:
                prev.content = content[:240]
            prev.status = status
        else:
            by_id[tid] = TodoItem(id=tid, content=content[:240], status=status)
            order.append(tid)
    out = [by_id[i] for i in order if i in by_id]
    save_todos(out, runtime, root=root)
    with suppress(Exception):
        from remedy.core.build_engine import get_build_state

        st = get_build_state(runtime)
        if st is not None:
            st.open_todo_count = open_todo_count(out)
    return out


def open_todo_count(items: list[TodoItem] | None) -> int:
    """Pending + in_progress rows (blocks DONE)."""
    n = 0
    for t in items or []:
        if t.status in {"pending", "in_progress"}:
            n += 1
    return n


def format_todos_block(items: list[TodoItem] | None) -> str:
    """Context inject. Empty string when there is nothing to show."""
    if not items:
        return ""
    lines = ["## Active build todos (machine-owned — update with todo_write)"]
    open_n = 0
    for t in items:
        mark = {
            "completed": "[x]",
            "cancelled": "[-]",
            "in_progress": "[>]",
            "pending": "[ ]",
        }.get(t.status, "[ ]")
        if t.status in {"pending", "in_progress"}:
            open_n += 1
        lines.append(f"- {mark} `{t.id}` {t.content}")
    if open_n:
        lines.append(
            f"{open_n} open. Do not claim done until these are completed or cancelled."
        )
    else:
        lines.append("All todos closed.")
    return "\n".join(lines)


def seed_drive_todos(
    runtime: Any,
    *,
    units: list[dict[str, Any]] | None = None,
    goal: str = "",
) -> list[TodoItem]:
    """Replace the checklist with a drive-loop skeleton (spec → TDD → units → verify)."""
    rows: list[dict[str, Any]] = [
        {"id": "spec", "content": f"Lock BuildSpec: {(goal or 'user goal')[:80]}", "status": "in_progress"},
        {"id": "tdd", "content": "Write failing TDD tests before implement", "status": "pending"},
    ]
    for u in (units or [])[:8]:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "")
        sym = str(u.get("symbol") or Path(path).stem)
        if not path and not sym:
            continue
        rows.append(
            {
                "id": f"unit-{sym}"[:24],
                "content": f"Implement {sym} ({path or 'unit'})",
                "status": "pending",
            }
        )
    rows.append({"id": "verify", "content": "Verify green (gate tower / tests)", "status": "pending"})
    return upsert_todos(runtime, rows, merge=False)
