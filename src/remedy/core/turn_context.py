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

# ReAct lives in a Task that outlives the SSE socket (disconnect ≠ Stop).
_turn_jobs: dict[str, asyncio.Task] = {}


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
# Per-turn build flags (must not live on the process singleton).
_turn_build_verify_green: ContextVar[bool] = ContextVar(
    "remedy_turn_build_verify_green", default=False
)
_turn_last_auto_checkpoint_n: ContextVar[int] = ContextVar(
    "remedy_turn_last_auto_checkpoint_n", default=0
)


@dataclass
class TurnReactFlags:
    """Live-turn ReAct flags — never stored on the shared BasicRuntime."""

    force_tool_choice: bool = False
    tool_choice_required_blocked: bool = False
    turn_tier: int = 1
    turn_tier_label: str = ""
    action_ir: Any = None
    shadow_strict: bool = False
    force_spread: bool = False
    metabolism_allow_verify: bool = False
    thinking_level: str | None = None
    context_snapshot: Any = None
    rmb_load_waits: int = 0
    turn_browse: bool = False
    turn_pure_action: bool = False
    turn_has_attachments: bool = False
    write_budget: int = 0
    # Per-turn ReAct step ceiling (0 = use the runtime default). A hive daughter
    # runs on the mother's runtime object; her small budget must not follow the
    # context out and truncate the mother's own turn.
    max_react_steps: int = 0
    sleev_force_direct: bool = False
    skip_ask: bool = False
    # Snapshot at begin_turn — live Settings Full must not lift this turn's jail.
    approval_mode: str = ""
    # This turn's user text — never a process-wide runtime field (concurrent tabs).
    last_user_text: str = ""


_turn_react_flags: ContextVar[TurnReactFlags | None] = ContextVar(
    "remedy_turn_react_flags", default=None
)

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
    _turn_build_verify_green,
    _turn_last_auto_checkpoint_n,
    _turn_react_flags,
)


# session_id -> list of abort events (overlapping streams rare but possible)
_registry: dict[str, list[asyncio.Event]] = {}
# session_id -> live subprocesses for this turn (killed on abort)
_session_procs: dict[str, list[Any]] = {}
# Early 409 claim — taken before persist so two POSTs cannot both start.
_stream_claims: set[str] = set()
# sid → claim generation. Stale CancelledError must not abort a newer turn.
_stream_epochs: dict[str, int] = {}
# sid → /reset generation. Dying-stream persist must not refill a wiped chat.
_reset_epochs: dict[str, int] = {}
# Next-turn injects keyed by session (never a process-global leftover).
_pending_verify_by_session: dict[str, Any] = {}
# Mid-turn steering: owner messages sent while a turn runs. The ReAct loop
# drains these between steps and folds them in as user messages, so the
# owner can redirect without stopping her (Grove's "every message steers").
_nudges_by_session: dict[str, list[str]] = {}
_NUDGE_MAX = 8
# sid → why the last abort happened ("stop" = Stop button, "supersede" = the
# owner sent the next message while this turn ran). The dying stream reads it
# to word the durable assistant row.
_abort_reasons: dict[str, str] = {}
ABORT_REASON_STOP = "stop"
ABORT_REASON_SUPERSEDE = "supersede"
_build_protocol_by_session: dict[str, str] = {}
_frontier_continue_by_session: dict[str, Any] = {}
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
    flags = TurnReactFlags()
    with contextlib.suppress(Exception):
        from remedy.core.approvals import APPROVALS, normalize_approval_mode

        flags.approval_mode = normalize_approval_mode(APPROVALS.mode)
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
        False,
        0,
        flags,
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


def current_turn_approval_mode() -> str | None:
    """Approval mode snapped at begin_turn. None outside a turn.

    Live ``APPROVALS.set_mode('full')`` from Settings must not lift this
    turn's write jail.
    """
    if not in_active_turn():
        return None
    flags = _turn_react_flags.get()
    raw = str(getattr(flags, "approval_mode", "") or "").strip().lower()
    return raw or None


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
    if isinstance(session_id, Token):
        # ``end_turn(*tokens)`` without the session id: a misaligned reset would
        # silently fail on every var and leak this turn's flags into the caller's
        # context (the suite inherited one turn's RMB wait budget that way).
        tokens = (session_id, *tokens)
        session_id = _turn_session_id.get()
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
    # Body coordination note: file holds are NOT released at end_turn on purpose
    # — a multi-turn build keeps its claims between turns so a sibling can't
    # stomp unfinished work. Holds free on Stop/abort (abort_session) or via the
    # claim TTL when the session goes quiet.
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


def session_turn_running(session_id: str) -> bool:
    """Is a ReAct turn live for this session right now?"""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        return bool(_registry.get(sid)) or sid in _stream_claims


def push_nudge(session_id: str, text: str) -> bool:
    """Queue an owner message for the running turn. False when no turn runs.

    The caller then sends it as an ordinary message instead. At most
    ``_NUDGE_MAX`` are held; older ones are kept (they were said first).
    """
    sid = str(session_id or "").strip()
    body = str(text or "").strip()
    if not sid or not body:
        return False
    with _lock:
        if not (_registry.get(sid) or sid in _stream_claims):
            return False
        lst = _nudges_by_session.setdefault(sid, [])
        if len(lst) >= _NUDGE_MAX:
            return False
        lst.append(body)
        return True


def drain_nudges(session_id: str | None) -> list[str]:
    """Take every queued nudge for *session_id* (empty when none)."""
    sid = str(session_id or "").strip()
    if not sid:
        return []
    with _lock:
        return list(_nudges_by_session.pop(sid, []) or [])


def clear_nudges(session_id: str | None) -> None:
    sid = str(session_id or "").strip()
    if sid:
        with _lock:
            _nudges_by_session.pop(sid, None)


def normalize_abort_reason(reason: str | None) -> str:
    r = str(reason or "").strip().lower()
    return ABORT_REASON_SUPERSEDE if r in ("supersede", "superseded", "resend", "next_message") else ABORT_REASON_STOP


def set_abort_reason(session_id: str, reason: str | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        _abort_reasons[sid] = normalize_abort_reason(reason)


def peek_abort_reason(session_id: str | None) -> str | None:
    """Reason recorded by the last ``abort_session`` for *session_id* (or None)."""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with _lock:
        return _abort_reasons.get(sid)


def clear_abort_reason(session_id: str | None) -> None:
    sid = str(session_id or "").strip()
    if sid:
        with _lock:
            _abort_reasons.pop(sid, None)


def register_turn_job(session_id: str, task: asyncio.Task) -> None:
    """Hold a strong ref so a dropped SSE cannot GC the ReAct worker."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        prev = _turn_jobs.get(sid)
        _turn_jobs[sid] = task
    if prev is not None and prev is not task and not prev.done():
        prev.cancel()


def drop_turn_job(session_id: str, task: asyncio.Task | None = None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        cur = _turn_jobs.get(sid)
        if task is None or cur is task:
            _turn_jobs.pop(sid, None)


def cancel_turn_job(session_id: str) -> None:
    """Cancel the detached ReAct worker (Stop / supersede). Disconnect must not."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        t = _turn_jobs.get(sid)
    if t is not None and not t.done():
        t.cancel()


def abort_session(
    session_id: str, *, epoch: int | None = None, reason: str | None = None
) -> int:
    """Signal in-flight turns and kill their shell children. Returns events notified.

    Keeps the stream *claim* until the dying generator releases it so Stop+send
    cannot overlap a still-running ReAct loop. Pass ``epoch`` from the stream
    that is dying — a stale CancelledError must not abort a newer claim.
    ``reason`` ("stop" | "supersede") is remembered for the dying stream's
    durable assistant row; an internal disconnect abort passes None and keeps
    whatever the client said.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        cur = int(_stream_epochs.get(sid, 0) or 0)
        if epoch is not None and cur != int(epoch):
            return 0
        if reason is not None:
            _abort_reasons[sid] = normalize_abort_reason(reason)
        events = list(_registry.pop(sid, []) or [])
        # Claim stays until release_session_stream_claim (stream finally).
    for ev in events:
        with contextlib.suppress(Exception):
            ev.set()
    kill_session_processes(sid)
    with contextlib.suppress(Exception):
        import asyncio

        from remedy.execution.host.session import close_shared_session

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(close_shared_session(sid))
    # Foragers hired by this owner turn die with Stop. Standing posts stay (PR2).
    with contextlib.suppress(Exception):
        from remedy.core.hive.runner import cancel_children

        cancel_children(sid)
    # Cancel in-flight computer-use browser jobs for *this session only*
    # so Stop on one tab does not clobber a concurrent sibling stream.
    with contextlib.suppress(Exception):
        from remedy.core.computer.host_bridge import get_host_bridge

        get_host_bridge().cancel_pending_and_running(
            reason="session_aborted",
            session_id=sid,
        )
    # Body coordination: a stopped session releases its beacon + file claims so
    # sibling muscles are not blocked on a hold nobody is using. (Crashed
    # sessions self-heal via the beacon TTL.)
    with contextlib.suppress(Exception):
        from remedy.core.coordination import unregister as _coord_unregister

        _coord_unregister(sid)
    # Stop / supersede cancel the detached worker. A dropped SSE must not.
    cancel_turn_job(sid)
    return len(events)


def try_claim_session_stream(session_id: str) -> bool:
    """Atomically claim a session for a new stream. False → 409.

    Must run *before* persisting the user message so two concurrent POSTs
    cannot both pass the busy check.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        if sid in _stream_claims:
            return False
        events = list(_registry.get(sid) or [])
        if any(not ev.is_set() for ev in events):
            return False
        _stream_claims.add(sid)
        _stream_epochs[sid] = int(_stream_epochs.get(sid, 0) or 0) + 1
        # A fresh turn starts with no abort verdict; the previous stream read
        # its reason synchronously before releasing the claim.
        _abort_reasons.pop(sid, None)
        return True


def stream_claim_epoch(session_id: str) -> int:
    """Current claim generation for ``session_id`` (0 if none)."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        return int(_stream_epochs.get(sid, 0) or 0)


def bump_session_reset_epoch(session_id: str) -> int:
    """Mark a /reset so in-flight persist skips this session. Returns new epoch."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        n = int(_reset_epochs.get(sid, 0) or 0) + 1
        _reset_epochs[sid] = n
        return n


def session_reset_epoch(session_id: str) -> int:
    """Current /reset generation for ``session_id`` (0 if never reset)."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        return int(_reset_epochs.get(sid, 0) or 0)


def release_session_stream_claim(session_id: str, *, epoch: int | None = None) -> None:
    """Drop the early stream claim (call from stream finally / setup errors)."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        if epoch is not None and int(_stream_epochs.get(sid, 0) or 0) != int(epoch):
            return
        _stream_claims.discard(sid)
        # A nudge nobody drained belongs to a turn that is over.
        _nudges_by_session.pop(sid, None)


def is_session_streaming(session_id: str) -> bool:
    """True when a non-aborted turn is registered for this session."""
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        if sid in _stream_claims:
            return True
        events = list(_registry.get(sid) or [])
    # Aborted events (is_set) must not block a new stream with 409.
    return any(not ev.is_set() for ev in events)


def turn_build_verify_green(runtime: Any = None) -> bool:
    """Per-turn green-verify flag (ContextVar first)."""
    if in_active_turn() or _turn_session_id.get():
        return bool(_turn_build_verify_green.get())
    if runtime is not None:
        return bool(getattr(runtime, "_build_verify_green", False))
    return bool(_turn_build_verify_green.get())


def set_turn_build_verify_green(value: bool, runtime: Any = None) -> None:
    flag = bool(value)
    _turn_build_verify_green.set(flag)
    if runtime is not None:
        runtime._build_verify_green = flag


def turn_last_auto_checkpoint_n(runtime: Any = None) -> int:
    if in_active_turn() or _turn_session_id.get():
        return int(_turn_last_auto_checkpoint_n.get() or 0)
    if runtime is not None:
        return int(getattr(runtime, "_last_auto_checkpoint_n", 0) or 0)
    return int(_turn_last_auto_checkpoint_n.get() or 0)


def set_turn_last_auto_checkpoint_n(n: int, runtime: Any = None) -> None:
    val = int(n or 0)
    _turn_last_auto_checkpoint_n.set(val)
    if runtime is not None:
        runtime._last_auto_checkpoint_n = val


def _react_flags() -> TurnReactFlags | None:
    return _turn_react_flags.get()


def turn_force_tool_choice(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.force_tool_choice)
    if runtime is not None:
        return bool(getattr(runtime, "_force_tool_choice", False))
    return False


def current_last_user_text(runtime: Any = None) -> str:
    """User text for this coroutine turn (ContextVar first).

    Concurrent tabs share one BasicRuntime; a process-wide
    ``runtime._last_user_text`` would leak tab A's prompt into tab B's context.
    Inside an active turn, only this turn's stamp is returned — even when empty.
    """
    flags = _react_flags()
    if flags is not None and in_active_turn():
        return str(getattr(flags, "last_user_text", "") or "")
    if flags is not None and str(getattr(flags, "last_user_text", "") or ""):
        return str(flags.last_user_text)
    if runtime is not None:
        return str(getattr(runtime, "_last_user_text", "") or "")
    return ""


def set_turn_last_user_text(text: str, runtime: Any = None) -> None:
    """Stamp this turn's user text; mirror onto runtime for legacy readers."""
    clipped = (text or "")[:4000]
    flags = _react_flags()
    if flags is not None:
        flags.last_user_text = clipped
    if runtime is not None:
        with contextlib.suppress(Exception):
            runtime._last_user_text = clipped


def set_turn_force_tool_choice(value: bool, runtime: Any = None) -> None:
    """Live-turn only — do not write the shared runtime singleton."""
    del runtime  # callers may still pass runtime; ignore to stop cross-tab theft
    flags = _react_flags()
    if flags is not None:
        flags.force_tool_choice = bool(value)


def turn_tool_choice_required_blocked(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.tool_choice_required_blocked)
    if runtime is not None:
        return bool(getattr(runtime, "_tool_choice_required_blocked", False))
    return False


def set_turn_tool_choice_required_blocked(value: bool, runtime: Any = None) -> None:
    del runtime
    flags = _react_flags()
    if flags is not None:
        flags.tool_choice_required_blocked = bool(value)


def turn_tier(runtime: Any = None, default: int = 1) -> int:
    """The tier this turn was classified as. 0 (L0_INSTANT) is a real value.

    ``or default`` here collapsed a genuine tier 0 to the default, and every
    caller repeated the same idiom — so L0_INSTANT, the tier whose whole point
    is answering without spending a frontier call, could never be observed by
    anyone downstream. Only a *missing* tier falls back.
    """
    flags = _react_flags()
    if flags is not None:
        from_flags = flags.turn_tier
        return int(from_flags) if from_flags is not None else int(default)
    if runtime is not None:
        from_runtime: Any = getattr(runtime, "_turn_tier", None)
        return int(from_runtime) if from_runtime is not None else int(default)
    return int(default)


def set_turn_tier(
    value: int,
    runtime: Any = None,
    *,
    label: str | None = None,
) -> None:
    """Live-turn only — do not write the shared runtime singleton."""
    del runtime
    flags = _react_flags()
    n = int(value or 0)
    if flags is not None:
        flags.turn_tier = n
        if label is not None:
            flags.turn_tier_label = str(label)


def turn_action_ir(runtime: Any = None) -> Any:
    flags = _react_flags()
    if flags is not None:
        return flags.action_ir
    if runtime is not None:
        return getattr(runtime, "_action_ir", None)
    return None


def set_turn_action_ir(value: Any, runtime: Any = None) -> None:
    """Live-turn only — do not write the shared runtime singleton."""
    del runtime
    flags = _react_flags()
    if flags is not None:
        flags.action_ir = value


def turn_shadow_strict(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.shadow_strict)
    if runtime is not None:
        return bool(getattr(runtime, "_shadow_strict", False))
    return False


def set_turn_shadow_strict(value: bool, runtime: Any = None) -> None:
    """Live-turn only — do not write the shared runtime singleton."""
    del runtime
    flags = _react_flags()
    if flags is not None:
        flags.shadow_strict = bool(value)


def turn_context_snapshot(runtime: Any = None) -> Any:
    flags = _react_flags()
    if flags is not None and flags.context_snapshot is not None:
        return flags.context_snapshot
    if runtime is not None:
        return getattr(runtime, "_last_context_snapshot", None)
    return None


def set_turn_context_snapshot(value: Any, runtime: Any = None) -> None:
    """Live-turn only — process-global runtime snapshot is a last-resort fallback."""
    flags = _react_flags()
    if flags is not None:
        flags.context_snapshot = value
    if runtime is not None:
        runtime._last_context_snapshot = value


def turn_force_spread(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.force_spread)
    if runtime is not None:
        return bool(getattr(runtime, "_force_spread", False))
    return False


def turn_thinking_level(runtime: Any = None, default: str = "high") -> str:
    flags = _react_flags()
    if flags is not None and flags.thinking_level:
        return str(flags.thinking_level)
    if runtime is not None:
        return str(getattr(runtime, "_thinking_level", default) or default)
    return str(default)


def set_turn_thinking_level(value: str, runtime: Any = None) -> None:
    """Live-turn only — do not write the shared runtime singleton."""
    del runtime
    flags = _react_flags()
    if flags is not None:
        flags.thinking_level = str(value or "")


def set_turn_force_spread(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.force_spread = bool(value)
    elif runtime is not None:
        runtime._force_spread = bool(value)


def turn_metabolism_allow_verify(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.metabolism_allow_verify)
    if runtime is not None:
        return bool(getattr(runtime, "_metabolism_allow_verify", False))
    return False


def set_turn_metabolism_allow_verify(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.metabolism_allow_verify = bool(value)
    elif runtime is not None:
        runtime._metabolism_allow_verify = bool(value)


def turn_skip_ask(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.skip_ask)
    if runtime is not None:
        return bool(getattr(runtime, "_turn_skip_ask", False))
    return False


def set_turn_skip_ask(value: bool, runtime: Any = None) -> None:
    """Turn-local Ask skip — do not write process-global APPROVALS.mode."""
    flags = _react_flags()
    if flags is not None:
        flags.skip_ask = bool(value)
    if runtime is not None:
        with contextlib.suppress(Exception):
            runtime._turn_skip_ask = bool(value)


def turn_browse(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.turn_browse)
    if runtime is not None:
        return bool(getattr(runtime, "_turn_browse", False))
    return False


def set_turn_browse(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.turn_browse = bool(value)
    elif runtime is not None and not in_active_turn():
        runtime._turn_browse = bool(value)


def turn_pure_action(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.turn_pure_action)
    if runtime is not None:
        return bool(getattr(runtime, "_turn_pure_action", False))
    return False


def set_turn_pure_action(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.turn_pure_action = bool(value)
    elif runtime is not None and not in_active_turn():
        runtime._turn_pure_action = bool(value)


def turn_has_attachments(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.turn_has_attachments)
    if runtime is not None:
        return bool(getattr(runtime, "_turn_has_attachments", False))
    return False


def set_turn_has_attachments(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.turn_has_attachments = bool(value)
    elif runtime is not None and not in_active_turn():
        runtime._turn_has_attachments = bool(value)


def turn_write_budget(runtime: Any = None) -> int:
    flags = _react_flags()
    if flags is not None:
        return int(flags.write_budget or 0)
    if runtime is not None:
        return int(getattr(runtime, "_remedy_write_budget", 0) or 0)
    return 0


def set_turn_write_budget(value: int, runtime: Any = None) -> None:
    n = max(0, int(value or 0))
    flags = _react_flags()
    if flags is not None:
        flags.write_budget = max(int(flags.write_budget or 0), n)
    elif runtime is not None and not in_active_turn():
        runtime._remedy_write_budget = max(
            int(getattr(runtime, "_remedy_write_budget", 0) or 0), n
        )


def turn_max_react_steps(runtime: Any = None) -> int:
    """This turn's ReAct step ceiling, or 0 when the runtime default applies."""
    flags = _react_flags()
    if flags is not None and int(flags.max_react_steps or 0) > 0:
        return int(flags.max_react_steps)
    return 0


def set_turn_max_react_steps(value: int) -> None:
    """Scope a step ceiling to this turn only. No-op outside an active turn."""
    flags = _react_flags()
    if flags is not None:
        flags.max_react_steps = max(0, int(value or 0))


def turn_sleev_force_direct(runtime: Any = None) -> bool:
    flags = _react_flags()
    if flags is not None:
        return bool(flags.sleev_force_direct)
    if runtime is not None:
        return bool(getattr(runtime, "_sleev_force_direct", False))
    return False


def set_turn_sleev_force_direct(value: bool, runtime: Any = None) -> None:
    flags = _react_flags()
    if flags is not None:
        flags.sleev_force_direct = bool(value)
    elif runtime is not None and not in_active_turn():
        runtime._sleev_force_direct = bool(value)


def stash_pending_verify_remedy(session_id: str | None, text: Any) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        if text:
            _pending_verify_by_session[sid] = text
        else:
            _pending_verify_by_session.pop(sid, None)


def take_pending_verify_remedy(session_id: str | None) -> Any:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with _lock:
        return _pending_verify_by_session.pop(sid, None)


def stash_build_protocol(session_id: str | None, text: str | None) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        if text:
            _build_protocol_by_session[sid] = str(text)
        else:
            _build_protocol_by_session.pop(sid, None)


def take_build_protocol(session_id: str | None) -> str | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with _lock:
        return _build_protocol_by_session.pop(sid, None)


def stash_frontier_continue(session_id: str | None, payload: Any) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _lock:
        if payload:
            _frontier_continue_by_session[sid] = payload
        else:
            _frontier_continue_by_session.pop(sid, None)


def take_frontier_continue(session_id: str | None) -> Any:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    with _lock:
        return _frontier_continue_by_session.pop(sid, None)


def any_stream_claimed() -> bool:
    """True when any session holds a stream claim (self-inject must skip)."""
    with _lock:
        return bool(_stream_claims)
