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

from remedy.core.atomic_json import write_json_atomic

_VALID = frozenset({"pending", "in_progress", "completed", "cancelled"})


@dataclass
class TodoItem:
    """One checklist row."""

    id: str
    content: str
    status: str = "pending"

    def to_public(self) -> dict[str, str]:
        return asdict(self)


def _disk_root(raw: Path | str | None) -> Path | None:
    """Project folder that may hold ``.remedy-build/todos.json``.

    Volume roots are not projects — writing ``C:\\.remedy-build`` leaked the
    last turn's checklist onto every other tab.
    """
    if raw is None:
        return None
    p = Path(raw)
    with suppress(Exception):
        p = p.resolve()
    from remedy.core.workspace import is_volume_root_path

    if is_volume_root_path(p):
        return None
    return p.parent if p.is_file() else p


def _root_for(runtime: Any) -> Path | None:
    """Bound project folder only. Never the user profile or a volume root."""
    with suppress(Exception):
        raw = runtime.effective_project_path()
        if not raw:
            return None
        from remedy.core.workspace import is_unset_project_path, is_volume_root_path

        if is_unset_project_path(raw) or is_volume_root_path(raw):
            return None
        base = _disk_root(raw)
        if base is None:
            return None
        if is_volume_root_path(base):
            return None
        try:
            if base.resolve() == Path.home().resolve():
                return None
        except OSError:
            pass
        return base
    return None


def _path(root: Path) -> Path:
    return root / ".remedy-build" / "todos.json"


def load_todos(runtime: Any = None, *, root: Path | str | None = None) -> list[TodoItem]:
    """Load todos from disk (empty list if none).

    When *root* is passed (session GET /todos), never fall back to the
    in-memory cache from another tab's turn.
    """
    explicit = root is not None
    base = _disk_root(root) if explicit else _root_for(runtime)
    if base is None:
        if explicit:
            return []
        cached = getattr(runtime, "_build_todos", None) if runtime is not None else None
        return list(cached) if isinstance(cached, list) else []
    fp = _path(base)
    if not fp.is_file():
        if explicit:
            return []
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
    stored = list(items)
    # Closed lists are not a live checklist — drop so the next turn starts clean.
    if stored and open_todo_count(stored) == 0:
        stored = []
    if runtime is not None:
        runtime._build_todos = list(stored)
    mark_todos_dirty(runtime, stored)
    base = Path(root) if root else _root_for(runtime)
    if base is None:
        return None
    fp = _path(base)
    if not stored:
        with suppress(OSError):
            if fp.is_file():
                fp.unlink()
        return fp
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = [t.to_public() for t in stored]
    write_json_atomic(fp, payload)
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


def todos_public(items: list[TodoItem] | None) -> list[dict[str, str]]:
    """JSON-safe rows for SSE / HTTP."""
    return [t.to_public() for t in (items or [])]


def todos_event_token(items: list[TodoItem] | None) -> str:
    """``@@todos:`` control token for the desktop live checklist."""
    payload = {
        "type": "todos",
        "todos": todos_public(items),
        "open": open_todo_count(items),
    }
    return "@@todos:" + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def mark_todos_dirty(runtime: Any, items: list[TodoItem] | None) -> None:
    """Queue a live checklist event for the current ReAct stream."""
    if runtime is None:
        return
    with suppress(Exception):
        runtime._pending_todos_event = todos_event_token(items)


def take_todos_event(runtime: Any) -> str | None:
    """Pop the pending ``@@todos:`` token, if any."""
    if runtime is None:
        return None
    tok = getattr(runtime, "_pending_todos_event", None)
    with suppress(Exception):
        runtime._pending_todos_event = None
    if isinstance(tok, str) and tok.startswith("@@todos:"):
        return tok
    return None


def open_todo_count(items: list[TodoItem] | None) -> int:
    """Pending + in_progress rows (blocks DONE)."""
    n = 0
    for t in items or []:
        if t.status in {"pending", "in_progress"}:
            n += 1
    return n


def format_todos_block(items: list[TodoItem] | None) -> str:
    """Context inject. Empty string when there is nothing to show."""
    if not items or open_todo_count(items) == 0:
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
    lines.append(
        f"{open_n} open. Do not claim done until these are completed or cancelled."
    )
    return "\n".join(lines)


def sync_todos_with_build(runtime: Any, state: Any = None) -> list[TodoItem]:
    """Close checklist rows the tree already satisfied — do not stall on ledger.

    Scout/explore complete after the first real write. File-named rows complete
    when that file exists with content. Verify completes on green (or when the
    required files are on disk and verify is not mid-hang).
    """
    root = None
    with suppress(Exception):
        raw_pp = str(state.project_path or "") if state is not None else ""
        if raw_pp:
            root = Path(raw_pp)
    items = load_todos(runtime, root=root)
    if not items:
        if state is not None:
            state.open_todo_count = 0
        return items

    missing: list[str] = []
    named: list[str] = []
    with suppress(Exception):
        if state is not None and hasattr(state, "missing_required_files"):
            missing = list(state.missing_required_files() or [])
        if state is not None and hasattr(state, "named_required_files"):
            named = list(state.named_required_files() or [])

    wrote = int(getattr(state, "write_steps", 0) or 0) > 0
    verify_ok = getattr(state, "last_verify_ok", None) is True
    files_ok = bool(named) and not missing
    phase = str(getattr(state, "phase", "") or "")

    write_names: list[str] = []
    for raw in list(getattr(state, "write_set", None) or []) + list(
        getattr(state, "paths_touched", None) or []
    ):
        name = str(raw or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
        if name and name not in write_names:
            write_names.append(name)

    changed = False
    for t in items:
        if t.status in {"completed", "cancelled"}:
            continue
        low = (t.content or "").lower()
        done = False
        if wrote and any(
            k in low
            for k in ("scout", "explore", "research", "lock buildspec", "gather")
        ):
            done = True
        if any(n and n in low for n in write_names if n not in {"ledger.json", "todos.json"}):
            done = True
        if named and root is not None:
            for n in named:
                if n.lower() in low:
                    cand = root / n
                    with suppress(OSError):
                        if cand.is_file() and cand.stat().st_size > 8:
                            done = True
        if "verify" in low or "green" in low:
            if verify_ok or (files_ok and (verify_ok or phase in {"done", "ship"})):
                done = True
        if "tdd" in low or "failing test" in low:
            if files_ok or verify_ok:
                done = True
        if files_ok and any(k in low for k in ("implement", "write ", "create ", "file_write")):
            done = True
        if done:
            t.status = "completed"
            changed = True

    # A GREEN-verified finished build owns its checklist: only when the build
    # actually declared done with a passing verify and nothing missing do we
    # close every remaining row (heuristic misses like "RemedyPDF update" name
    # no file and used to linger). Requiring verify_ok keeps the checklist from
    # claiming work done when the build reached "done" WITHOUT a green verify.
    if phase == "done" and verify_ok and not missing:
        for t in items:
            if t.status in {"pending", "in_progress"}:
                t.status = "completed"
                changed = True

    if changed:
        save_todos(items, runtime, root=root)
        items = load_todos(runtime, root=root)
    n_open = open_todo_count(items)
    if state is not None:
        state.open_todo_count = n_open
    return items


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
