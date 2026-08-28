"""Consume one LLM HTTP response (SSE or JSON) into a StreamRoundState."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    apply_reasoning_piece,
    parse_sse_data_line,
    want_sse_stream_parse,
)

logger = logging.getLogger(__name__)

# Matches is_disconnect_error ("connection reset") so a dropped wait retries
# the same turn instead of painting "Generation stopped".
PROVIDER_DROP_ERROR = "connection reset (provider stream dropped)"


def is_owner_stop(abort_ev: asyncio.Event | None = None) -> bool:
    """True when the owner pressed Stop / abort_session set the event."""
    if abort_ev is not None and abort_ev.is_set():
        return True
    with contextlib.suppress(Exception):
        from remedy.core.turn_context import is_turn_aborted

        if is_turn_aborted():
            return True
    return False


def uncancel_current_task() -> None:
    """CPython 3.12 sticky cancel: the next await would re-raise without this."""
    task = asyncio.current_task()
    if task is None:
        return
    with contextlib.suppress(Exception):
        uncancel = getattr(task, "uncancel", None)
        if callable(uncancel):
            uncancel()


def raise_drop_or_cancel(abort_ev: asyncio.Event | None = None) -> None:
    """Call from ``except CancelledError``.

    Owner Stop keeps CancelledError (turn ends). A provider/socket drop
    becomes ConnectionError so the ReAct loop retries the same turn.
    """
    if is_owner_stop(abort_ev):
        raise asyncio.CancelledError()
    uncancel_current_task()
    raise ConnectionError(PROVIDER_DROP_ERROR) from None


def _sse_idle_timeout_s() -> float:
    try:
        sse_idle_timeout = float(os.environ.get("REMEDY_SSE_IDLE_SECONDS", "180"))
    except ValueError:
        sse_idle_timeout = 180.0
    return max(60.0, min(sse_idle_timeout, 900.0))


def _close_http_response(resp: Any) -> None:
    for name in ("close", "release"):
        fn = getattr(resp, name, None)
        if callable(fn):
            with contextlib.suppress(Exception):
                fn()


async def _await_or_abort(awaitable: Any, abort_ev: asyncio.Event | None) -> Any:
    """Race an awaitable against the turn abort Event; cancel HTTP on abort."""
    if abort_ev is None:
        return await awaitable
    if abort_ev.is_set():
        raise asyncio.CancelledError()
    wait_task = asyncio.ensure_future(awaitable)
    abort_task = asyncio.ensure_future(abort_ev.wait())
    try:
        done, pending = await asyncio.wait(
            {wait_task, abort_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        if abort_ev.is_set():
            wait_task.cancel()
            raise asyncio.CancelledError()
        return wait_task.result()
    except asyncio.CancelledError:
        wait_task.cancel()
        abort_task.cancel()
        # Keep CancelledError: asyncio.wait_for idle-timeout relies on it
        # becoming TimeoutError. consume / loop_http decide drop vs Stop.
        raise
    finally:
        if not wait_task.done():
            wait_task.cancel()
        if not abort_task.done():
            abort_task.cancel()


async def consume_llm_http_response(
    resp: Any,
    *,
    round_state: StreamRoundState,
    collected: dict[str, Any],
    adapter: Any,
    bind: Any,
    body: dict[str, Any] | None,
    use_openai_sse: bool,
    stream_live: bool,
) -> AsyncIterator[tuple[str, bool]]:
    """Yield (token, produced_user_text_flag) from one provider response.

    Updates *round_state* and *collected* in place.

    Each HTTP round is a new scratchpad. ``@@thinking_round`` tells the
    desktop to *replace* live thinking, not append this round's recap onto
    every previous one (session 4d89: the same "The user wants…" 20 times).
    """
    yield "@@thinking_round", False
    headers_map = getattr(resp, "headers", None) or {}
    content_type = str(
        headers_map.get("Content-Type") or headers_map.get("content-type") or ""
    ).lower()

    abort_ev = None
    try:
        from remedy.core.turn_context import current_abort_event, is_turn_aborted

        abort_ev = current_abort_event()
        if is_turn_aborted():
            _close_http_response(resp)
            raise asyncio.CancelledError()
    except asyncio.CancelledError:
        raise
    except Exception:
        abort_ev = None

    if want_sse_stream_parse(
        body if isinstance(body, dict) else None,
        use_openai_sse=use_openai_sse,
        content_type=content_type,
    ):
        content_iter = resp.content.__aiter__()
        sse_idle_timeout = _sse_idle_timeout_s()
        while True:
            try:
                line = await asyncio.wait_for(
                    _await_or_abort(content_iter.__anext__(), abort_ev),
                    timeout=sse_idle_timeout,
                )
            except StopAsyncIteration:
                break
            except asyncio.CancelledError:
                _close_http_response(resp)
                raise_drop_or_cancel(abort_ev)
            except TimeoutError:
                logger.warning(
                    "SSE stream idle >%.0fs; ending this model round "
                    "(provider likely stuck; will continue/promote reasoning if any)",
                    sse_idle_timeout,
                )
                if not round_state.content_parts and not round_state.reasoning_parts:
                    yield (
                        "\n\n_(Provider stream idle — ending this model round.)_\n",
                        False,
                    )
                break
            line_text = line.decode("utf-8").strip()
            if line_text == "data: [DONE]":
                break
            chunk = parse_sse_data_line(line_text)
            if chunk is None:
                continue
            try:
                from remedy.core.usage import usage_from_provider_payload

                u = usage_from_provider_payload(
                    chunk,
                    model=bind.model,
                    provider=bind.provider,
                )
                if u:
                    prev = round_state.last_usage
                    if prev and prev.get("source") == "provider":
                        from remedy.core.usage import merge_usage

                        round_state.last_usage = merge_usage(prev, u)
                    else:
                        round_state.last_usage = u
            except Exception:
                pass
            r_before = len("".join(round_state.reasoning_parts))
            live = apply_openai_sse_chunk(
                round_state, chunk, stream_live=stream_live
            )
            r_after = "".join(round_state.reasoning_parts)
            if len(r_after) > r_before:
                yield f"@@thinking:{r_after[r_before:]}", False
            if live:
                yield live, True
    else:
        try:
            data = await _await_or_abort(resp.json(), abort_ev)
        except asyncio.CancelledError:
            _close_http_response(resp)
            raise_drop_or_cancel(abort_ev)
        try:
            from remedy.core.usage import usage_from_provider_payload

            u = usage_from_provider_payload(
                data,
                model=bind.model,
                provider=bind.provider,
            )
            if u:
                round_state.last_usage = u
        except Exception:
            pass
        from remedy.core.react_stream import apply_openai_completion_message

        apply_openai_completion_message(
            round_state, data, stream_live=stream_live
        )
        parsed = adapter.extract_response(data)
        if not round_state.content_parts and parsed.get("content"):
            round_state.content_parts.append(parsed["content"])
        reason = (
            parsed.get("reasoning_content") or parsed.get("reasoning") or ""
        )
        if isinstance(reason, str) and reason.strip():
            apply_reasoning_piece(round_state, reason.strip())
        if not round_state.tool_call_acc and parsed.get("tool_calls"):
            raw_tcs = parsed.get("tool_calls")
            if isinstance(raw_tcs, list):
                round_state.tool_call_acc = dict(enumerate(raw_tcs))
        collected.update(parsed)
        thought = round_state.reasoning_out
        if thought:
            yield f"@@thinking:{thought}", False
