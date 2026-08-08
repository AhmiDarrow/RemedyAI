"""Consume one LLM HTTP response (SSE or JSON) into a StreamRoundState."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    parse_sse_data_line,
    want_sse_stream_parse,
)

logger = logging.getLogger(__name__)


def _sse_idle_timeout_s() -> float:
    try:
        sse_idle_timeout = float(os.environ.get("REMEDY_SSE_IDLE_SECONDS", "180"))
    except ValueError:
        sse_idle_timeout = 180.0
    return max(60.0, min(sse_idle_timeout, 900.0))


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
    """
    headers_map = getattr(resp, "headers", None) or {}
    content_type = str(
        headers_map.get("Content-Type") or headers_map.get("content-type") or ""
    ).lower()

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
                    content_iter.__anext__(),
                    timeout=sse_idle_timeout,
                )
            except StopAsyncIteration:
                break
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
        data = await resp.json()
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
        if (
            isinstance(reason, str)
            and reason.strip()
            and not round_state.reasoning_parts
        ):
            round_state.reasoning_parts.append(reason.strip())
        if not round_state.tool_call_acc and parsed.get("tool_calls"):
            raw_tcs = parsed.get("tool_calls")
            if isinstance(raw_tcs, list):
                round_state.tool_call_acc = dict(enumerate(raw_tcs))
        collected.update(parsed)
