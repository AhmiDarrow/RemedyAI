"""Streaming ReAct helpers — SSE parse, tool-call accumulation, message build.

Keeps :meth:`BasicRuntime._call_llm_stream` readable by isolating pure
stream-processing logic that can be unit-tested without an HTTP session.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Awaitable
from dataclasses import dataclass, field
from typing import Any

from remedy.core.react_policy import (
    looks_like_pseudo_tools,
    message_wants_tools,
    parse_pseudo_tool_calls,
    tool_call_fingerprint,
)


@dataclass
class StreamRoundState:
    """Mutable state for one LLM streaming round."""

    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_call_acc: dict[int, dict[str, Any]] = field(default_factory=dict)
    produced_user_text: bool = False

    @property
    def text_out(self) -> str:
        return "".join(self.content_parts).strip()

    @property
    def reasoning_out(self) -> str:
        return "".join(self.reasoning_parts).strip()

    def tool_calls_list(self, collected: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raw = (
            list(self.tool_call_acc.values())
            if self.tool_call_acc
            else ((collected or {}).get("tool_calls") or [])
        )
        return [
            tc
            for tc in raw
            if ((tc.get("function") or {}).get("name") or "").strip()
        ]


def accumulate_tool_call_delta(
    acc: dict[int, dict[str, Any]],
    tc: dict[str, Any],
) -> None:
    """Merge a streaming tool_call delta into *acc* by index."""
    idx = tc.get("index", 0)
    if idx not in acc:
        acc[idx] = {
            "id": tc.get("id") or "",
            "type": "function",
            "function": {
                "name": ((tc.get("function") or {}).get("name") or ""),
                "arguments": ((tc.get("function") or {}).get("arguments") or ""),
            },
        }
        return
    existing = acc[idx]
    fn_args = ((tc.get("function") or {}).get("arguments") or "")
    if fn_args:
        existing["function"]["arguments"] += fn_args
    fn_name = (tc.get("function") or {}).get("name")
    if fn_name:
        existing["function"]["name"] = fn_name
    tc_id = tc.get("id")
    if tc_id:
        existing["id"] = tc_id


def apply_openai_sse_chunk(
    state: StreamRoundState,
    chunk: dict[str, Any],
    *,
    stream_live: bool,
) -> str | None:
    """Apply one parsed OpenAI SSE JSON chunk. Returns content delta to yield live."""
    choice = (chunk.get("choices") or [{}])[0]
    delta = choice.get("delta") or {}
    content_delta = delta.get("content")
    live: str | None = None
    if content_delta:
        state.content_parts.append(content_delta)
        if stream_live:
            state.produced_user_text = True
            live = content_delta
    else:
        reason_delta = delta.get("reasoning_content") or delta.get("reasoning")
        if reason_delta:
            state.reasoning_parts.append(reason_delta)
    for tc in delta.get("tool_calls") or []:
        accumulate_tool_call_delta(state.tool_call_acc, tc)
    return live


def parse_sse_data_line(line_text: str) -> dict[str, Any] | None:
    """Parse a single SSE line into a JSON object, or None if not data."""
    line_text = (line_text or "").strip()
    if not line_text or line_text.startswith(":"):
        return None
    if line_text == "data: [DONE]":
        return None
    if line_text.startswith("data: "):
        line_text = line_text[6:]
    try:
        return json.loads(line_text)
    except json.JSONDecodeError:
        return None


async def iter_openai_sse_content(
    content: AsyncIterator[bytes],
    state: StreamRoundState,
    *,
    stream_live: bool,
    on_live: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Consume OpenAI SSE byte lines into *state*, optionally invoking *on_live*."""
    async for line in content:
        line_text = line.decode("utf-8").strip()
        if line_text == "data: [DONE]":
            break
        chunk = parse_sse_data_line(line_text)
        if chunk is None:
            continue
        live = apply_openai_sse_chunk(state, chunk, stream_live=stream_live)
        if live and on_live is not None:
            await on_live(live)


def build_runtime_system_block(
    *,
    system_prompt: str,
    provider: str,
    model: str,
    base_url: str,
    max_steps: int,
    context: str,
) -> str:
    runtime_info = (
        f"Connected provider: {provider}\n"
        f"Connected model: {model}\n"
        f"API base URL: {base_url}\n"
        f"Tool budget this turn: up to {max_steps} model steps "
        f"(final step always answers without tools).\n"
        "When asked which provider/model you use, answer from this block — do not call tools."
    )
    return f"{system_prompt}\n\n{runtime_info}\n\n{context}"


def should_enable_tools(
    message: str,
    all_tools: list[dict[str, Any]],
    *,
    has_attachments: bool,
) -> bool:
    return bool(all_tools) and (message_wants_tools(message) or has_attachments)


def filter_fresh_tool_calls(
    tool_calls_list: list[dict[str, Any]],
    seen_fps: set[str],
) -> list[dict[str, Any]]:
    return [
        tc
        for tc in tool_calls_list
        if tool_call_fingerprint(tc) not in seen_fps
    ]


def finalize_round_text(
    state: StreamRoundState,
    tool_calls_list: list[dict[str, Any]],
) -> str:
    """Pick best text for the round (content, or reasoning if no tools)."""
    text_out = state.text_out
    if not text_out and state.reasoning_parts and not tool_calls_list:
        text_out = state.reasoning_out
    return text_out


# Re-export policy helpers used by stream loop call sites.
__all__ = [
    "StreamRoundState",
    "accumulate_tool_call_delta",
    "apply_openai_sse_chunk",
    "build_runtime_system_block",
    "filter_fresh_tool_calls",
    "finalize_round_text",
    "iter_openai_sse_content",
    "looks_like_pseudo_tools",
    "message_wants_tools",
    "parse_pseudo_tool_calls",
    "parse_sse_data_line",
    "should_enable_tools",
    "tool_call_fingerprint",
]
