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
# Continuity objects for this turn only (Session Brief / PartnerState / work roots).
# Live runtime mirrors still update under the turn lock for legacy paths, but tools
# must read these ContextVars so concurrent tabs cannot stomp mid-stream.
_turn_session_brief: ContextVar[Any] = ContextVar("remedy_turn_session_brief", default=None)
_turn_partner_state: ContextVar[Any] = ContextVar("remedy_turn_partner_state", default=None)
_turn_work_roots: ContextVar[list[str] | None] = ContextVar(
    "remedy_turn_work_roots", default=None
)
# True only between begin_turn / end_turn for this coroutine.
_turn_active: ContextVar[bool] = ContextVar("remedy_turn_active", default=False)

# Ordered ContextVars set by begin_turn (end_turn resets by zip-order).
_TURN_CONTEXT_VARS: tuple[ContextVar[Any], ...] = (
    _turn_session_id,
    _turn_abort,
    _turn_workspace,
    _turn_plan_mode,
    _turn_tool_steps,
    _turn_session_brief,
    _turn_partner_state,
    _turn_work_roots,
    _turn_active,
)


# session_id -> list of abort events (overlapping streams rare but possible)
_registry: dict[str, list[asyncio.Event]] = {}
# session_id -> live subprocesses for this turn (killed on abort)
_session_procs: dict[str, list[Any]] = {}
_lock = threading.Lock()


def in_active_turn() -> bool:
    """True while this coroutine is inside begin_turn … end_turn."""
    return bool(_turn_active.get())


def current_session_id(fallback: str | None = None) -> str | None:
    sid = _turn_session_id.get()
    return sid if sid else fallback


def turn_session_id(runtime: Any = None, fallback: str | None = None) -> str | None:
    """Session id for this coroutine turn (ContextVar first, then runtime).

    Prefer this for approvals / telemetry so concurrent tabs do not steal
    each other's session-scoped fingerprints.
    """
    if in_active_turn():
        sid = _turn_session_id.get()
        if sid:
            return sid
        # Anonymous turn with an active context — do not fall through to a
        # sibling tab's live runtime._session_id.
        if fallback and str(fallback).strip():
            return str(fallback).strip()
        return None
    sid = _turn_session_id.get()
    if sid:
        return sid
    if runtime is not None:
        # Prefer live store to avoid property recursion on BasicRuntime.
        d = getattr(runtime, "__dict__", None) or {}
        if "_session_id_live" in d:
            live = d.get("_session_id_live")
        else:
            live = getattr(runtime, "_session_id", None)
        if live is not None and str(live).strip():
            return str(live).strip()
    return fallback if fallback and str(fallback).strip() else None


def current_turn_workspace() -> TurnWorkspace | None:
    return _turn_workspace.get()


def turn_session_brief(runtime: Any = None) -> Any:
    """Session Brief for this turn (ContextVar first)."""
    if in_active_turn():
        return _turn_session_brief.get()
    if runtime is not None:
        return runtime.__dict__.get(
            "_session_brief_live", getattr(runtime, "_session_brief", None)
        )
    return _turn_session_brief.get()


def set_turn_session_brief(brief: Any) -> None:
    """Update this turn's Session Brief (no-op outside an active turn)."""
    if in_active_turn():
        _turn_session_brief.set(brief)


def turn_partner_state(runtime: Any = None) -> Any:
    """PartnerState for this turn (ContextVar first)."""
    if in_active_turn():
        return _turn_partner_state.get()
    if runtime is not None:
        return runtime.__dict__.get(
            "_partner_state_live", getattr(runtime, "_partner_state", None)
        )
    return _turn_partner_state.get()


def set_turn_partner_state(state: Any) -> None:
    if in_active_turn():
        _turn_partner_state.set(state)


def turn_work_roots(runtime: Any = None) -> list[str]:
    """Work roots for this turn (ContextVar first)."""
    if in_active_turn():
        roots = _turn_work_roots.get()
        return list(roots) if roots is not None else []
    if runtime is not None:
        live = runtime.__dict__.get("_work_roots_live")
        if live is None:
            live = getattr(runtime, "_work_roots", None)
        return list(live or [])
    roots = _turn_work_roots.get()
    return list(roots) if roots is not None else []


def set_turn_work_roots(roots: list[str] | None) -> None:
    if in_active_turn():
        _turn_work_roots.set(list(roots or []))


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
    session_brief: Any = None,
    partner_state: Any = None,
    work_roots: list[str] | None = None,
) -> tuple[Token, ...]:
    """Register abort + workspace + plan/tools + continuity for this turn.

    Returns tokens in ``_TURN_CONTEXT_VARS`` order (pass to ``end_turn`` via ``*tokens``).
    Continuity kwargs freeze Session Brief / PartnerState / work roots for the
    full ReAct stream so a sibling tab cannot rebind mid-turn.
    """
    sid = str(session_id or "").strip() or None
    ev = asyncio.Event()
    path_s = str(active_path) if active_path is not None else ""
    ws = TurnWorkspace(project_raw=project_raw, active_path=path_s)
    roots = list(work_roots) if work_roots is not None else []
    values: tuple[Any, ...] = (
        sid,
        ev,
        ws,
        bool(plan_mode),
        [],
        session_brief,
        partner_state,
        roots,
        True,
    )
    tokens: list[Token] = []
    for var, val in zip(_TURN_CONTEXT_VARS, values, strict=True):
        tokens.append(var.set(val))
    if sid:
        with _lock:
            _registry.setdefault(sid, []).append(ev)
    return tuple(tokens)


def current_plan_mode(runtime: Any = None) -> bool:
    """Plan mode for this coroutine turn (ContextVar first)."""
    # ContextVar always set by begin_turn; default False when outside a turn.
    if in_active_turn() or _turn_tool_steps.get() is not None or _turn_session_id.get():
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
    *tokens: Token | None,
) -> None:
    """Unregister abort event and reset contextvars (zip-order with begin_turn)."""
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
    for var, tok in zip(_TURN_CONTEXT_VARS, tokens, strict=False):
        if tok is not None:
            with contextlib.suppress(Exception):
                var.reset(tok)


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
    """Signal all in-flight turns and kill their shell children. Returns events notified.

    Immediately drops the session from the live registry so a new stream is not
    blocked with HTTP 409 after Stop (stuck LLM/tool turns may take a moment to
    unwind ``end_turn``).
    """
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        # Pop so is_session_streaming is False right away (not only after end_turn).
        events = list(_registry.pop(sid, []) or [])
    for ev in events:
        with contextlib.suppress(Exception):
            ev.set()
    kill_session_processes(sid)
    # Cancel in-flight computer-use browser jobs for *this session only*
    # so Stop on one tab does not clobber a concurrent sibling stream.
    with contextlib.suppress(Exception):
        from remedy.core.computer.host_bridge import get_host_bridge

        get_host_bridge().cancel_pending_and_running(
            reason="session_aborted",
            session_id=sid,
        )
    return len(events)


def is_session_streaming(session_id: str) -> bool:
    """True when a non-aborted turn is registered for this session."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        events = list(_registry.get(sid) or [])
    # Aborted events (is_set) must not block a new stream with 409.
    return any(not ev.is_set() for ev in events)
