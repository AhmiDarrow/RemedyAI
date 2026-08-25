"""Small helpers for the ReAct stream loop.

Kept out of ``loop.py`` so that module stays the orchestrator + HTTP session
(tests patch ``loop.aiohttp.ClientSession``). Re-exported from ``loop``.
"""

from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

from remedy.core.react_loop.stream_consume import _await_or_abort


def log_llm_round(
    bind: Any,
    runtime: Any,
    turn: Any,
    step: int,
    t0: float,
    status: str,
    round_state: Any,
    *,
    error: BaseException | None = None,
) -> None:
    """One ``remedy.llm`` line per streamed provider round (never raises)."""
    with suppress(Exception):
        from remedy.core.llm_log import log_llm_call

        acc = getattr(round_state, "tool_call_acc", None) or {}
        log_llm_call(
            provider=getattr(bind, "provider", None),
            model=getattr(bind, "model", None),
            session_id=getattr(turn, "session_id", None)
            or getattr(runtime, "_session_id", None),
            step=step,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            status=status,
            finish_reason=getattr(round_state, "finish_reason", None),
            tool_calls=len(acc),
            usage=getattr(round_state, "last_usage", None),
            error=error,
        )


def browse_tool_ok(body: str) -> tuple[bool, bool]:
    """Parse a computer/navigate tool body → (ok_true, ok_false).

    JSON ``ok`` is authoritative. Do not substring-match ``success``
    (matches ``unsuccessful``) or ``user_visible`` (present on failures too).
    """
    raw = body or ""
    low = raw.lower()
    failed = "rail_failed" in low
    with suppress(Exception):
        data = json.loads(raw)
        if isinstance(data, dict) and "ok" in data:
            ok = bool(data["ok"]) and not failed
            return ok, (not bool(data["ok"])) or failed
    import re as _re

    if failed or _re.search(r'"ok"\s*:\s*false', low):
        return False, True
    if _re.search(r'"ok"\s*:\s*true', low):
        return True, False
    return False, False


def stopped_note(tools_ran: bool) -> str:
    if tools_ran:
        return (
            "\n*(Stopped.) Tools already ran this turn are kept "
            "in history. Send **continue** to resume.*\n"
        )
    return (
        "\n*(Generation stopped before a final answer. "
        "History is intact — send a new message or "
        "**continue**.)*\n"
    )


def take_nudges(session_id: Any, runtime: Any) -> list[str]:
    """Owner messages queued for this turn (see turn_context.push_nudge)."""
    from remedy.core.turn_context import drain_nudges, turn_session_id

    sid = turn_session_id(runtime, fallback=str(session_id or "") or None)
    return drain_nudges(sid)


def steer_message(text: str) -> dict[str, Any]:
    """The owner spoke mid-turn. Plain user role — it *is* the user."""
    return {
        "role": "user",
        "content": (
            f"{text}\n\n"
            "(Said while you were working — fold it into what you are doing "
            "now; do not start over unless it asks you to.)"
        ),
    }


async def wait_rmb_ready_abortable(timeout_s: float) -> dict[str, Any]:
    import asyncio as _aio

    from remedy.core.turn_context import current_abort_event, is_turn_aborted
    from remedy.runtime.rmb.service import wait_rmb_ready

    if is_turn_aborted():
        raise _aio.CancelledError()
    return await _await_or_abort(
        _aio.to_thread(wait_rmb_ready, None, timeout_s=timeout_s),
        current_abort_event(),
    )
