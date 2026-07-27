"""Per-turn session id, workspace, and cooperative abort for concurrent streams.

The desktop and messenger gateways share one BasicRuntime. Mutable fields like
``_session_id`` / ``_active_project_path`` still update for legacy tools, but
each stream also binds ContextVars so concurrent awaits see the correct session
and project jail. ``POST /sessions/{id}/abort`` sets the turn's Event so the
ReAct loop can stop without relying only on client SSE disconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TurnWorkspace:
    """Project binding for one stream turn (path jail + access scope)."""

    project_raw: str | None
    active_path: str  # resolved absolute path string


# Context-local for the current coroutine turn (survives across awaits).
_turn_session_id: ContextVar[str | None] = ContextVar("remedy_turn_session_id", default=None)
_turn_abort: ContextVar[asyncio.Event | None] = ContextVar("remedy_turn_abort", default=None)
_turn_workspace: ContextVar[TurnWorkspace | None] = ContextVar(
    "remedy_turn_workspace", default=None
)


# session_id -> list of abort events (overlapping streams rare but possible)
_registry: dict[str, list[asyncio.Event]] = {}
_lock = threading.Lock()


def current_session_id(fallback: str | None = None) -> str | None:
    sid = _turn_session_id.get()
    return sid if sid else fallback


def current_turn_workspace() -> TurnWorkspace | None:
    return _turn_workspace.get()


def is_turn_aborted() -> bool:
    ev = _turn_abort.get()
    return bool(ev is not None and ev.is_set())


def begin_turn(
    session_id: str | None,
    *,
    project_raw: str | None = None,
    active_path: str | Any = "",
) -> tuple[Token, Token, Token]:
    """Register abort + workspace for this turn. Returns (session, abort, workspace) tokens."""
    sid = str(session_id or "").strip() or None
    ev = asyncio.Event()
    path_s = str(active_path) if active_path is not None else ""
    ws = TurnWorkspace(project_raw=project_raw, active_path=path_s)
    tok_s = _turn_session_id.set(sid)
    tok_a = _turn_abort.set(ev)
    tok_w = _turn_workspace.set(ws)
    if sid:
        with _lock:
            _registry.setdefault(sid, []).append(ev)
    return tok_s, tok_a, tok_w


def bind_turn_workspace(project_raw: str | None, active_path: str | Any) -> Token:
    """Re-bind workspace mid-turn (rare). Prefer begin_turn kwargs."""
    path_s = str(active_path) if active_path is not None else ""
    return _turn_workspace.set(TurnWorkspace(project_raw=project_raw, active_path=path_s))


def end_turn(
    session_id: str | None,
    tok_s: Token,
    tok_a: Token,
    tok_w: Token | None = None,
) -> None:
    """Unregister abort event and reset contextvars."""
    sid = str(session_id or "").strip() or None
    ev = _turn_abort.get()
    if sid and ev is not None:
        with _lock:
            lst = _registry.get(sid) or []
            if ev in lst:
                lst.remove(ev)
            if not lst and sid in _registry:
                del _registry[sid]
    with contextlib.suppress(Exception):
        _turn_session_id.reset(tok_s)
    with contextlib.suppress(Exception):
        _turn_abort.reset(tok_a)
    if tok_w is not None:
        with contextlib.suppress(Exception):
            _turn_workspace.reset(tok_w)


def abort_session(session_id: str) -> int:
    """Signal all in-flight turns for ``session_id``. Returns count notified."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        events = list(_registry.get(sid) or [])
    for ev in events:
        with contextlib.suppress(Exception):
            ev.set()
    return len(events)


def is_session_streaming(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        return bool(_registry.get(sid))
