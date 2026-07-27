"""Per-turn session id + cooperative abort for concurrent streams.

The desktop and messenger gateways share one BasicRuntime. Mutable fields like
``_session_id`` are still updated for legacy tools, but active streams also
register an asyncio.Event so ``POST /sessions/{id}/abort`` can stop generation
without waiting for the client to drop the SSE connection alone.
"""

from __future__ import annotations

import asyncio
import threading
from contextvars import ContextVar
from typing import Any

# Context-local for the current coroutine turn (survives across awaits).
_turn_session_id: ContextVar[str | None] = ContextVar("remedy_turn_session_id", default=None)
_turn_abort: ContextVar[asyncio.Event | None] = ContextVar("remedy_turn_abort", default=None)

# session_id -> list of abort events (multiple overlapping streams rare but possible)
_registry: dict[str, list[asyncio.Event]] = {}
_lock = threading.Lock()


def current_session_id(fallback: str | None = None) -> str | None:
    sid = _turn_session_id.get()
    return sid if sid else fallback


def is_turn_aborted() -> bool:
    ev = _turn_abort.get()
    return bool(ev is not None and ev.is_set())


def begin_turn(session_id: str | None) -> tuple[Any, Any]:
    """Register an abort event for this turn. Returns (token_session, token_abort)."""
    sid = str(session_id or "").strip() or None
    ev = asyncio.Event()
    tok_s = _turn_session_id.set(sid)
    tok_a = _turn_abort.set(ev)
    if sid:
        with _lock:
            _registry.setdefault(sid, []).append(ev)
    return tok_s, tok_a


def end_turn(session_id: str | None, tok_s: Any, tok_a: Any) -> None:
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
    try:
        _turn_session_id.reset(tok_s)
    except Exception:
        pass
    try:
        _turn_abort.reset(tok_a)
    except Exception:
        pass


def abort_session(session_id: str) -> int:
    """Signal all in-flight turns for ``session_id``. Returns count notified."""
    sid = str(session_id or "").strip()
    if not sid:
        return 0
    with _lock:
        events = list(_registry.get(sid) or [])
    for ev in events:
        try:
            ev.set()
        except Exception:
            pass
    return len(events)


def is_session_streaming(session_id: str) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    with _lock:
        return bool(_registry.get(sid))
