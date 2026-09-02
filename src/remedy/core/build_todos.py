"""Turn-local build todos — Claude-class checklist the machine owns.

Missions are durable and heavy. This is a lightweight per-project checklist the
model updates every few steps so multi-file work does not stall or skip verify.
Persisted under ``{project}/.remedy-build/todos.json``.
"""

from __future__ import annotations

import json
import re
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


def load_todos(
    runtime: Any = None,
    *,
    root: Path | str | None = None,
    session_id: str | None = None,
) -> list[TodoItem]:
    """Load todos from disk (empty list if none).

    When *root* is passed (session GET /todos), never fall back to the
    in-memory cache from another tab's turn — unless *session_id* is given
    for an unbound endless chat.
    """
    explicit = root is not None
    if session_id and not explicit:
        # Session GET with no bound folder: this chat's in-memory bag only —
        # never the runtime's last cwd (another tab's project disk).
        return _mem_todos(runtime, session_id)
    base = _disk_root(root) if explicit else _root_for(runtime)
    if base is None:
        if explicit and not session_id:
            return []
        return _mem_todos(runtime, session_id)
    fp = _path(base)
    if not fp.is_file():
        if explicit and not session_id:
            return []
        return _mem_todos(runtime, session_id)
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items: list[TodoItem] = []
    for row in raw if isinstance(raw, list) else (raw.get("items") or []):
        if not isinstance(row, dict):
            continue
        from remedy.core.build_oracle import coerce_text_arg

        content = coerce_text_arg(row.get("content") or "")
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


def _todos_session_key(runtime: Any, session_id: str | None = None) -> str:
    """Stable key for per-session in-memory todos (never a sibling tab's)."""
    sid = str(session_id or "").strip()
    if sid:
        return sid
    with suppress(Exception):
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or "").strip()
        if sid:
            return sid
    if runtime is not None:
        d = getattr(runtime, "__dict__", None) or {}
        if "_session_id_live" in d:
            sid = str(d.get("_session_id_live") or "").strip()
        else:
            sid = str(getattr(runtime, "_session_id", "") or "").strip()
        if sid:
            return sid
    return "_anon"


def _mem_todos(runtime: Any, session_id: str | None = None) -> list[TodoItem]:
    if runtime is None:
        return []
    key = _todos_session_key(runtime, session_id)
    bag = getattr(runtime, "_build_todos_by_session", None)
    if isinstance(bag, dict):
        cached = bag.get(key)
        return list(cached) if isinstance(cached, list) else []
    if session_id:
        return []
    cached = getattr(runtime, "_build_todos", None)
    return list(cached) if isinstance(cached, list) else []


def _set_mem_todos(
    runtime: Any, items: list[TodoItem], session_id: str | None = None
) -> None:
    if runtime is None:
        return
    stored = list(items)
    key = _todos_session_key(runtime, session_id)
    bag = getattr(runtime, "_build_todos_by_session", None)
    if not isinstance(bag, dict):
        bag = {}
        runtime._build_todos_by_session = bag
    bag[key] = stored
    runtime._build_todos = stored


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
        _set_mem_todos(runtime, stored)
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
        from remedy.core.build_oracle import coerce_text_arg

        content = coerce_text_arg(raw.get("content") or raw.get("title") or "")
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
            st.open_feature_todo_count = open_feature_todo_count(out)
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


def begin_chat_beat(runtime: Any, session_id: str | None = None) -> None:
    """Endless session: a new owner message is a new beat.

    Unbound chats drop leftover in-memory todos so yesterday's Oracle list
    does not sit on today's Telegram question. Bound project folders keep
    disk todos.
    """
    if runtime is None:
        return
    if _root_for(runtime) is not None:
        return
    _set_mem_todos(runtime, [], session_id=session_id)
    mark_todos_dirty(runtime, [], session_id=session_id)


def mark_todos_dirty(
    runtime: Any, items: list[TodoItem] | None, *, session_id: str | None = None
) -> None:
    """Queue a live checklist event for the current ReAct stream."""
    if runtime is None:
        return
    tok = todos_event_token(items)
    key = _todos_session_key(runtime, session_id)
    with suppress(Exception):
        bag = getattr(runtime, "_pending_todos_by_session", None)
        if not isinstance(bag, dict):
            bag = {}
            runtime._pending_todos_by_session = bag
        bag[key] = tok
        # Legacy single slot — only for this session's latest write.
        runtime._pending_todos_event = tok


def take_todos_event(
    runtime: Any, session_id: str | None = None
) -> str | None:
    """Pop the pending ``@@todos:`` token for *this* session, if any."""
    if runtime is None:
        return None
    key = _todos_session_key(runtime, session_id)
    tok = None
    bag = getattr(runtime, "_pending_todos_by_session", None)
    if isinstance(bag, dict):
        tok = bag.pop(key, None)
    else:
        tok = getattr(runtime, "_pending_todos_event", None)
        with suppress(Exception):
            runtime._pending_todos_event = None
    if isinstance(tok, str) and tok.startswith("@@todos:"):
        return tok
    return None


def _write_match_tokens(paths: list[str] | None) -> list[str]:
    """Filenames + stems the checklist can match (``audioToMidi.ts`` → audiotomidi)."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in paths or []:
        name = str(raw or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
        if not name or name in {"ledger.json", "todos.json"}:
            continue
        if name not in seen:
            seen.add(name)
            out.append(name)
        stem = name.rsplit(".", 1)[0] if "." in name else name
        for suffix in (".test", ".spec", "_test", "_spec"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                break
        compact = stem.replace("-", "").replace("_", "")
        for tok in (stem, compact):
            if len(tok) >= 6 and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def todo_is_verify_row(content: str) -> bool:
    """True for checklist rows that *are* the test run, not product work.

    "Verify critical fixes" is product work — it must not complete just because
    ``npm test`` is already green from an earlier hop (session 765c 22:45).
    """
    low = (content or "").lower().strip()
    if low in {"verify", "tests", "test", "verify green"}:
        return True
    if low.startswith("verify green"):
        return True
    return any(
        k in low
        for k in (
            "npm test",
            "pytest",
            "cargo test",
            "go test",
            "vitest",
            "tests green",
            "test green",
            "run tests",
            "run the tests",
            "run the suite",
        )
    )


def open_feature_todo_count(items: list[TodoItem] | None) -> int:
    """Pending product work — excludes 'npm test green' rows."""
    n = 0
    for t in items or []:
        if t.status not in {"pending", "in_progress"}:
            continue
        if todo_is_verify_row(t.content):
            continue
        n += 1
    return n


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
    when that file exists with content. Verify completes only on a green verify
    — files-on-disk alone must not close it (that was the old false-stop).
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
            state.open_feature_todo_count = 0
        return items

    named: list[str] = []
    with suppress(Exception):
        if state is not None and hasattr(state, "named_required_files"):
            named = list(state.named_required_files() or [])

    wrote = int(getattr(state, "write_steps", 0) or 0) > 0
    verify_ok = getattr(state, "last_verify_ok", None) is True

    # Only files the turn actually WROTE can close a file-named row. Every
    # tool-arg path (file_read included) lands in paths_touched, so matching
    # on it closed "Implement tabScore.ts" the moment the model merely read
    # tabScore.ts — and the DONE gate then opened with zero writes.
    write_names = (
        _write_match_tokens(list(getattr(state, "write_set", None) or []))
        if wrote
        else []
    )

    changed = False
    for t in items:
        if t.status in {"completed", "cancelled"}:
            continue
        raw_low = (t.content or "").lower()
        compact_todo = raw_low.replace("-", "").replace("_", "")
        done = False
        # Prefix only — "research" inside "go back through the reviews" must not close.
        if wrote and raw_low.startswith(
            ("read ", "scout", "explore", "research", "gather", "lock buildspec")
        ):
            done = True
        # Filename as a whole token (``tabScore.ts`` / tabscore), not a short
        # substring of an unrelated feature row.
        for n in write_names:
            if not n:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", compact_todo):
                done = True
                break
        if named and root is not None:
            for n in named:
                if n.lower() in raw_low:
                    cand = root / n
                    with suppress(OSError):
                        if cand.is_file() and cand.stat().st_size > 8:
                            done = True
        # Only the actual test-run row — never "Verify critical fixes".
        if todo_is_verify_row(t.content) and verify_ok:
            done = True
        if done:
            t.status = "completed"
            changed = True

    # Keep exactly one in_progress row so the on-screen Build list moves.
    if wrote or changed:
        if not any(t.status == "in_progress" for t in items):
            for t in items:
                if t.status == "pending":
                    t.status = "in_progress"
                    changed = True
                    break

    if changed:
        save_todos(items, runtime, root=root)
        items = load_todos(runtime, root=root)
    n_open = open_todo_count(items)
    n_feature = open_feature_todo_count(items)
    if state is not None:
        state.open_todo_count = n_open
        state.open_feature_todo_count = n_feature
        if (
            n_open == 0
            and getattr(state, "last_verify_ok", None) is True
            and bool(getattr(state, "drive_to_done", False))
        ):
            state.drive_to_done = False
    return items


def seed_review_finding_todos(
    runtime: Any,
    items: list[str],
    *,
    root: Path | str | None = None,
) -> int:
    """Replace ``rf-*`` checklist rows with this review's numbered findings."""
    cleaned: list[str] = []
    for raw in items or []:
        content = str(raw or "").strip()
        if len(content) < 8:
            continue
        cleaned.append(content[:240])
        if len(cleaned) >= 12:
            break
    if not cleaned:
        return 0
    current = load_todos(runtime, root=root)
    keep = [
        t
        for t in current
        if not str(t.id).startswith("rf-") or t.status in {"completed", "cancelled"}
    ]
    rows: list[dict[str, Any]] = [
        {"id": t.id, "content": t.content, "status": t.status} for t in keep
    ]
    for i, content in enumerate(cleaned, 1):
        rows.append({"id": f"rf-{i}", "content": content, "status": "pending"})
    upsert_todos(runtime, rows, merge=False, root=root)
    return len(cleaned)


def has_open_review_finding_todos(runtime: Any = None) -> bool:
    items = load_todos(runtime)
    return any(
        str(t.id).startswith("rf-") and t.status in {"pending", "in_progress"}
        for t in items
    )


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
    # merge=True: never wipe the owner's rows (rf-* review findings gate DONE).
    return upsert_todos(runtime, rows, merge=True)
