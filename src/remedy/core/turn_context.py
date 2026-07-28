"""Per-turn session id, workspace, and cooperative abort for concurrent streams.

The desktop and messenger gateways share one BasicRuntime. Mutable fields like
``_session_id`` / ``_active_project_path`` still update for legacy tools, but
each stream also binds ContextVars so concurrent awaits see the correct session
and project jail. ``POST /sessions/{id}/abort`` sets the turn's Event so the
ReAct loop can stop without relying only on client SSE disconnect.

In-flight shell/sandbox processes are registered per session and killed when
the turn is aborted so Stop / session switch does not leave tools running.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


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
# Per-turn plan mode + tool trace (must not share mutable runtime lists across
# concurrent multi-provider streams).
_turn_plan_mode: ContextVar[bool] = ContextVar("remedy_turn_plan_mode", default=False)
_turn_tool_steps: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "remedy_turn_tool_steps", default=None
)


# session_id -> list of abort events (overlapping streams rare but possible)
_registry: dict[str, list[asyncio.Event]] = {}
# session_id -> live subprocesses for this turn (killed on abort)
_session_procs: dict[str, list[Any]] = {}
_lock = threading.Lock()


def current_session_id(fallback: str | None = None) -> str | None:
    sid = _turn_session_id.get()
    return sid if sid else fallback


def turn_session_id(runtime: Any = None, fallback: str | None = None) -> str | None:
    """Session id for this coroutine turn (ContextVar first, then runtime).

    Prefer this for approvals / telemetry so concurrent tabs do not steal
    each other's session-scoped fingerprints.
    """
    sid = _turn_session_id.get()
    if sid:
        return sid
    if runtime is not None:
        raw = getattr(runtime, "_session_id", None)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return fallback if fallback and str(fallback).strip() else None


def current_turn_workspace() -> TurnWorkspace | None:
    return _turn_workspace.get()


def is_turn_aborted() -> bool:
    ev = _turn_abort.get()
    return bool(ev is not None and ev.is_set())


def current_abort_event() -> asyncio.Event | None:
    return _turn_abort.get()


def begin_turn(
    session_id: str | None,
    *,
    project_raw: str | None = None,
    active_path: str | Any = "",
    plan_mode: bool = False,
) -> tuple[Token, Token, Token, Token, Token]:
    """Register abort + workspace + plan/tools for this turn.

    Returns (session, abort, workspace, plan_mode, tool_steps) tokens.
    """
    sid = str(session_id or "").strip() or None
    ev = asyncio.Event()
    path_s = str(active_path) if active_path is not None else ""
    ws = TurnWorkspace(project_raw=project_raw, active_path=path_s)
    tok_s = _turn_session_id.set(sid)
    tok_a = _turn_abort.set(ev)
    tok_w = _turn_workspace.set(ws)
    tok_p = _turn_plan_mode.set(bool(plan_mode))
    tok_t = _turn_tool_steps.set([])
    if sid:
        with _lock:
            _registry.setdefault(sid, []).append(ev)
    return tok_s, tok_a, tok_w, tok_p, tok_t


def current_plan_mode(runtime: Any = None) -> bool:
    """Plan mode for this coroutine turn (ContextVar first)."""
    # ContextVar always set by begin_turn; default False when outside a turn.
    if _turn_tool_steps.get() is not None or _turn_session_id.get():
        return bool(_turn_plan_mode.get())
    if runtime is not None:
        return bool(getattr(runtime, "_plan_mode", False))
    return bool(_turn_plan_mode.get())


def current_turn_tool_steps(runtime: Any = None) -> list[dict[str, Any]]:
    """Mutable tool-step list for this turn only."""
    steps = _turn_tool_steps.get()
    if steps is not None:
        return steps
    if runtime is not None:
        legacy = getattr(runtime, "_turn_tool_steps", None)
        if isinstance(legacy, list):
            return legacy
        runtime._turn_tool_steps = []
        return runtime._turn_tool_steps
    empty: list[dict[str, Any]] = []
    return empty


def bind_turn_workspace(project_raw: str | None, active_path: str | Any) -> Token:
    """Re-bind workspace mid-turn (rare). Prefer begin_turn kwargs."""
    path_s = str(active_path) if active_path is not None else ""
    return _turn_workspace.set(TurnWorkspace(project_raw=project_raw, active_path=path_s))


def end_turn(
    session_id: str | None,
    tok_s: Token,
    tok_a: Token,
    tok_w: Token | None = None,
    tok_p: Token | None = None,
    tok_t: Token | None = None,
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
            # Drop any leftover proc handles for this session if no turns remain
            if sid not in _registry:
                _session_procs.pop(sid, None)
    with contextlib.suppress(Exception):
        _turn_session_id.reset(tok_s)
    with contextlib.suppress(Exception):
        _turn_abort.reset(tok_a)
    if tok_w is not None:
        with contextlib.suppress(Exception):
            _turn_workspace.reset(tok_w)
    if tok_p is not None:
        with contextlib.suppress(Exception):
            _turn_plan_mode.reset(tok_p)
    if tok_t is not None:
        with contextlib.suppress(Exception):
            _turn_tool_steps.reset(tok_t)


def register_turn_process(proc: Any) -> None:
    """Track a live child process for the current turn (killed on abort)."""
    sid = current_session_id()
    if not sid or proc is None:
        return
    with _lock:
        lst = _session_procs.setdefault(sid, [])
        if proc not in lst:
            lst.append(proc)


def unregister_turn_process(proc: Any) -> None:
    sid = current_session_id()
    if not sid or proc is None:
        return
    with _lock:
        lst = _session_procs.get(sid) or []
        if proc in lst:
            lst.remove(proc)
        if not lst and sid in _session_procs:
            del _session_procs[sid]


def _kill_proc(proc: Any) -> None:
    """Best-effort kill of asyncio or stdlib process (incl. Windows tree)."""
    if proc is None:
        return
    try:
        from remedy.execution.process import kill_process_tree

        kill_process_tree(proc)
    except Exception:
        with contextlib.suppress(Exception):
            if hasattr(proc, "kill"):
                proc.kill()
            elif hasattr(proc, "terminate"):
                proc.terminate()


def kill_session_processes(session_id: str) -> int:
    """Kill all registered subprocesses for ``session_id``. Returns count."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        procs = list(_session_procs.pop(sid, []) or [])
    n = 0
    for proc in procs:
        try:
            _kill_proc(proc)
            n += 1
        except Exception:
            logger.debug("kill_session_processes failed", exc_info=True)
    return n


def abort_session(session_id: str) -> int:
    """Signal all in-flight turns and kill their shell children. Returns events notified."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        events = list(_registry.get(sid) or [])
    for ev in events:
        with contextlib.suppress(Exception):
            ev.set()
    kill_session_processes(sid)
    return len(events)


def is_session_streaming(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        return bool(_registry.get(sid))
