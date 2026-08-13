"""Streaming ReAct helpers — SSE parse, tool-call accumulation, message build.

Keeps :meth:`BasicRuntime._call_llm_stream` readable by isolating pure
stream-processing logic that can be unit-tested without an HTTP session.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from remedy.core.react_policy import (
    _HARD_CHAT_ONLY_RE,
    _META_NO_TOOLS_RE,
    _SOFT_AFFIRM_RE,
    history_suggests_open_work,
    looks_like_pseudo_tools,
    looks_like_tool_markup_prefix,
    message_wants_tools,
    parse_pseudo_tool_calls,
    tool_call_fingerprint,
)

# OpenAI-compatible finish reasons that mean "ran out of output budget".
LENGTH_FINISH_REASONS = frozenset({"length", "max_tokens"})


@dataclass
class StreamRoundState:
    """Mutable state for one LLM streaming round."""

    content_parts: list[str] = field(default_factory=list)
    reasoning_parts: list[str] = field(default_factory=list)
    tool_call_acc: dict[int, dict[str, Any]] = field(default_factory=dict)
    produced_user_text: bool = False
    finish_reason: str | None = None
    # True when we buffered content that looked like DSML / text tool-calls.
    suppressed_tool_markup: bool = False
    # Last provider usage snapshot for this HTTP stream (record once after done).
    last_usage: dict[str, Any] | None = None

    @property
    def text_out(self) -> str:
        return "".join(self.content_parts).strip()

    @property
    def reasoning_out(self) -> str:
        return "".join(self.reasoning_parts).strip()

    @property
    def hit_length_limit(self) -> bool:
        """True when the model stopped because max_tokens / length was hit."""
        fr = (self.finish_reason or "").lower()
        return fr in LENGTH_FINISH_REASONS

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


def want_sse_stream_parse(
    body: dict[str, Any] | None,
    *,
    use_openai_sse: bool,
    content_type: str = "",
) -> bool:
    """True when the HTTP body should be consumed as SSE deltas.

    Local/RMB tool rounds force ``stream=False`` so tools complete reliably.
    Those responses are non-stream JSON with ``choices[].message`` — not deltas.
    ``apply_openai_sse_chunk`` only reads ``delta``, so we must not take the SSE
    path when the request asked for a non-stream completion.
    """
    if isinstance(body, dict) and body.get("stream") is False:
        return False
    ct = (content_type or "").lower()
    if "event-stream" in ct:
        return True
    # Explicit stream=True or default OpenAI-compat SSE adapters.
    if isinstance(body, dict) and body.get("stream") is True:
        return bool(use_openai_sse) or "event-stream" in ct
    return bool(use_openai_sse) or "event-stream" in ct


def apply_openai_sse_chunk(
    state: StreamRoundState,
    chunk: dict[str, Any],
    *,
    stream_live: bool,
) -> str | None:
    """Apply one parsed OpenAI SSE JSON chunk. Returns content delta to yield live."""
    choice = (chunk.get("choices") or [{}])[0]
    # Final chunk usually carries finish_reason (stop | length | tool_calls | …).
    fr = choice.get("finish_reason")
    if fr:
        state.finish_reason = str(fr)
    delta = choice.get("delta") or {}
    content_delta = delta.get("content")
    live: str | None = None
    if content_delta:
        state.content_parts.append(content_delta)
        # Never live-stream DSML / fake tool markup — it becomes chat spam.
        acc = "".join(state.content_parts)
        if (
            stream_live
            and not looks_like_pseudo_tools(acc)
            and not looks_like_pseudo_tools(str(content_delta))
            and not looks_like_tool_markup_prefix(acc)
        ):
            state.produced_user_text = True
            live = content_delta
        elif stream_live and (
            looks_like_pseudo_tools(acc) or looks_like_tool_markup_prefix(acc)
        ):
            # Mark so callers know we suppressed junk (recovery will run later).
            state.suppressed_tool_markup = True
    # DeepSeek thinking mode streams reasoning_content alongside (or before) content.
    # Must accumulate independently — not only in the no-content branch.
    reason_delta = delta.get("reasoning_content") or delta.get("reasoning")
    if reason_delta:
        state.reasoning_parts.append(reason_delta)
    for tc in delta.get("tool_calls") or []:
        accumulate_tool_call_delta(state.tool_call_acc, tc)
    return live


def apply_openai_completion_message(
    state: StreamRoundState,
    data: dict[str, Any],
    *,
    stream_live: bool = False,
) -> str | None:
    """Apply a non-stream OpenAI-compat completion (``choices[].message``).

    Used when ``body[\"stream\"]`` is false (local tool rounds, disconnect
    retry). Unlike :func:`apply_openai_sse_chunk`, this reads ``message`` not
    ``delta`` so native ``tool_calls`` are not dropped.
    """
    choice = (data.get("choices") or [{}])[0]
    fr = choice.get("finish_reason")
    if fr:
        state.finish_reason = str(fr)
    msg = choice.get("message") or {}
    # Some providers put the full message under delta in a final non-stream blob.
    if not msg and isinstance(choice.get("delta"), dict):
        msg = choice.get("delta") or {}
    content = msg.get("content")
    live: str | None = None
    if isinstance(content, str) and content:
        state.content_parts.append(content)
        acc = "".join(state.content_parts)
        if stream_live and not looks_like_pseudo_tools(acc):
            state.produced_user_text = True
            live = content
        elif stream_live and looks_like_pseudo_tools(acc):
            state.suppressed_tool_markup = True
    reason = msg.get("reasoning_content") or msg.get("reasoning") or ""
    if isinstance(reason, str) and reason.strip():
        state.reasoning_parts.append(reason.strip())
    raw_tcs = msg.get("tool_calls")
    if isinstance(raw_tcs, list) and raw_tcs:
        # Store as complete tool_calls (index-keyed) for tool_calls_list().
        for i, tc in enumerate(raw_tcs):
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index", i)
            try:
                idx_i = int(idx)
            except (TypeError, ValueError):
                idx_i = i
            state.tool_call_acc[idx_i] = {
                "id": tc.get("id") or f"call_{idx_i}",
                "type": tc.get("type") or "function",
                "function": dict(tc.get("function") or {}),
            }
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
    user_message: str = "",
) -> str:
    # Local/RMB: auto-slim — cloud-length system prompts cause monologue + OOC
    try:
        from remedy.core.local_agent_optimize import (
            is_local_binding,
            slim_system_for_local,
        )

        if is_local_binding(provider, model, base_url):
            return slim_system_for_local(
                system_prompt,
                context,
                provider=provider,
                model=model,
                base_url=base_url,
                max_steps=max_steps,
                user_message=user_message or "",
            )
    except Exception:
        pass
    runtime_info = (
        f"Connected provider: {provider}\n"
        f"Connected model: {model}\n"
        f"API base URL: {base_url}\n"
        f"Operating mode: run until the task is finished — keep using tools for "
        f"coding/project work across as many steps as needed "
        f"(pathological-loop safety ceiling: {max_steps}). "
        "Soft epochs only compact context; they are not a stop or tool budget.\n"
        "Do not stop mid-task to summarize or because of step pressure.\n"
        "When asked which provider/model you use, answer from this block — do not call tools."
    )
    return f"{system_prompt}\n\n{runtime_info}\n\n{context}"


def should_enable_tools(
    message: str,
    all_tools: list[dict[str, Any]],
    *,
    has_attachments: bool,
    history: list[dict[str, Any]] | None = None,
    open_tasks: list[str] | None = None,
) -> bool:
    """Gate tool schemas for the ReAct loop.

    Tools stay on when:
    - the current message looks like work / an action kick, or
    - attachments are present, or
    - recent history / open tasks show unfinished work (critical for
      short follow-ups like "go with your suggestions" / "progress?").

    Pure chit-chat still returns False even mid-session so "thanks" does not
    thrash the filesystem.
    """
    if not all_tools:
        return False
    if has_attachments:
        return True
    msg = (message or "").strip()
    open_work = history_suggests_open_work(history, open_tasks=open_tasks)
    # Meta questions stay tool-free even mid-session.
    if msg and _META_NO_TOOLS_RE.search(msg):
        return False
    # Hard social (hi/thanks/bye): never thrash tools.
    if msg and _HARD_CHAT_ONLY_RE.match(msg):
        return False
    # Soft affirmations ("ok", "cool", "yep"): continue tools when work is open.
    # Session bug (2026-07-28): bare "ok" mid-Comfy setup forced tools=[] →
    # force_answer → one status line and stop.
    if msg and _SOFT_AFFIRM_RE.match(msg):
        return bool(open_work)
    if message_wants_tools(message):
        return True
    # History-aware continuity: keep agency across multi-turn tasks.
    return open_work


def filter_fresh_tool_calls(
    tool_calls_list: list[dict[str, Any]],
    seen_fps: set[str],
) -> list[dict[str, Any]]:
    return [
        tc
        for tc in tool_calls_list
        if tool_call_fingerprint(tc) not in seen_fps
    ]


def normalize_tool_calls(tool_calls_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return OpenAI-shaped tool_calls with stable non-empty ids and names.

    Streaming providers sometimes omit ``id`` on early deltas; empty ids break
    tool-result pairing on the next request (HTTP 400).

    Arguments are coerced to **valid JSON strings** with **full fidelity**
    (see :func:`coerce_tool_arguments_json`). This runs on the **execute**
    path — do **not** call :func:`sanitize_tool_arguments` here: that path
    mid-clips nested strings at 8k and turns large ``file_write`` bodies into
    history stubs, which then get written to disk as corrupted source.
    Provider history sanitization belongs only in
    :func:`sanitize_message` / :func:`sanitize_chat_body`.
    """
    from uuid import uuid4

    from remedy.core.provider_sanitize import coerce_tool_arguments_json

    out: list[dict[str, Any]] = []
    for tc in tool_calls_list or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        name = (fn.get("name") or "").strip()
        if not name:
            continue
        args_s = coerce_tool_arguments_json(
            fn.get("arguments"), tool_name=name
        )
        call_id = (tc.get("id") or "").strip() or f"call_{uuid4().hex[:24]}"
        out.append(
            {
                "id": call_id,
                "type": tc.get("type") or "function",
                "function": {"name": name, "arguments": args_s},
            }
        )
    return out


def ensure_tool_call_pairings(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ensure every assistant ``tool_calls`` entry has a following tool result.

    OpenAI-compatible APIs reject incomplete pairings with HTTP 400:
    \"An assistant message with 'tool_calls' must be followed by tool messages…\".

    Multi-step robustness: when a system/user inject (epoch continue, re-arm
    nudge, recovery) lands *between* tool results for the same assistant
    turn, look ahead until the next assistant message so real results are
    not orphaned and replaced by empty stubs.
    """
    if not messages:
        return messages

    # Fast path: no tool_calls / tool roles → identity (common L1 chat)
    has_tools = False
    for m in messages:
        if not isinstance(m, dict):
            continue
        r = m.get("role")
        if r == "tool" or (r == "assistant" and m.get("tool_calls")):
            has_tools = True
            break
    if not has_tools:
        return messages

    out: list[dict[str, Any]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if not isinstance(msg, dict):
            out.append(msg)
            i += 1
            continue

        role = msg.get("role")
        tcs = msg.get("tool_calls") if role == "assistant" else None
        if not tcs:
            # Drop orphan tool messages (no preceding assistant tool_calls).
            if role == "tool":
                i += 1
                continue
            out.append(msg)
            i += 1
            continue

        # Normalize ids on a shallow copy of the assistant message.
        normalized = normalize_tool_calls(list(tcs))
        assistant_msg = {**msg, "tool_calls": normalized}
        out.append(assistant_msg)

        needed: dict[str, dict[str, Any]] = {
            tc["id"]: tc for tc in normalized if tc.get("id")
        }
        found: set[str] = set()
        collected_tools: list[dict[str, Any]] = []
        j = i + 1
        # 1) Immediate consecutive tool results (happy path).
        while j < n and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
            tid = (messages[j].get("tool_call_id") or "").strip()
            if tid and tid in needed and tid not in found:
                collected_tools.append(messages[j])
                found.add(tid)
            j += 1

        # 2) Multi-step look-ahead: epoch / re-arm / recovery injects can sit
        # between tool results. Pull matching tool msgs until next assistant
        # tool_calls turn so API order stays: assistant → all tools → rest.
        if len(found) < len(needed):
            k = j
            pulled: list[int] = []
            while k < n:
                mk = messages[k]
                if not isinstance(mk, dict):
                    k += 1
                    continue
                rk = mk.get("role")
                # Stop before the next assistant turn (tool or content).
                if rk == "assistant":
                    break
                if rk == "tool":
                    tid = (mk.get("tool_call_id") or "").strip()
                    if tid and tid in needed and tid not in found:
                        collected_tools.append(mk)
                        found.add(tid)
                        pulled.append(k)
                        if len(found) >= len(needed):
                            k += 1
                            break
                k += 1
            if pulled:
                # Skip pulled tool messages when replaying the intervening span.
                pulled_set = set(pulled)
                look_end = k
            else:
                pulled_set = set()
                look_end = j
        else:
            pulled_set = set()
            look_end = j

        for tm in collected_tools:
            out.append(tm)

        for cid, tc in needed.items():
            if cid in found:
                continue
            name = ((tc.get("function") or {}).get("name") or "unknown").strip()
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "content": (
                        f"(missing tool result for {name}; "
                        "treated as empty so the conversation can continue)"
                    ),
                }
            )

        # Replay intervening non-tool messages (and non-matching tools dropped).
        if look_end > j:
            for p in range(j, look_end):
                if p in pulled_set:
                    continue
                mp = messages[p]
                if isinstance(mp, dict) and mp.get("role") == "tool":
                    # Orphan / unmatched tool in the look-ahead span — drop.
                    continue
                out.append(mp)
            i = look_end
        else:
            i = j

    return out


def finalize_round_text(
    state: StreamRoundState,
    tool_calls_list: list[dict[str, Any]],
) -> str:
    """Pick best text for the round (content, or reasoning if no tools).

    DeepSeek-class models often put the entire useful answer in
    ``reasoning_content`` and leave ``content`` empty. When this round has no
    tool calls, promote reasoning so we never soft-empty a finished turn.
    """
    text_out = state.text_out
    if not text_out and state.reasoning_parts and not tool_calls_list:
        text_out = state.reasoning_out
    return text_out


def build_assistant_api_message(
    *,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None = None,
    reasoning_content: str | None = None,
) -> dict[str, Any]:
    """Build an assistant message for the next provider request.

    DeepSeek thinking mode requires ``reasoning_content`` to be passed back
    whenever the assistant turn included tool calls — otherwise HTTP 400:
    "The reasoning_content in the thinking mode must be passed back to the API."
    """
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content if content is not None else None,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
        # Always include the field on tool turns when we have any reasoning text
        # (or empty string if the provider is in thinking mode but sent none —
        # empty is safer than omitting for some DeepSeek variants).
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        else:
            msg["reasoning_content"] = ""
    elif reasoning_content:
        # Non-tool turns: optional; keep for continuity when present.
        msg["reasoning_content"] = reasoning_content
    return msg


def repair_reasoning_content_in_messages(
    messages: list[dict[str, Any]],
) -> bool:
    """Ensure every assistant tool_calls turn has reasoning_content.

    Returns True if any message was modified (caller should retry the request).
    """
    changed = False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        if not msg.get("tool_calls"):
            continue
        if "reasoning_content" not in msg:
            msg["reasoning_content"] = ""
            changed = True
    return changed


def repair_tool_arguments_in_messages(messages: list[dict[str, Any]]) -> int:
    """Rewrite every assistant tool_calls[].arguments to valid JSON.

    Returns number of assistant turns touched. Prevents provider HTTP 400
    ``EOF while parsing a string at column ~6000`` from truncated stream args
    or mid-string sanitizer clips.
    """
    n = 0
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls")
        if not tcs:
            continue
        before = json.dumps(tcs, default=str) if not isinstance(tcs, list) else None
        fixed = normalize_tool_calls(list(tcs) if isinstance(tcs, list) else [])
        after = json.dumps(fixed, default=str)
        # Always assign normalized (stable ids + valid JSON args)
        if before is None or after != json.dumps(tcs, default=str):
            msg["tool_calls"] = fixed
            n += 1
        else:
            # Still assign to force valid shape
            msg["tool_calls"] = fixed
            n += 1
    return n


def strip_broken_tool_call_turns(messages: list[dict[str, Any]]) -> int:
    """Drop assistant+tool spans whose arguments still fail JSON after repair.

    Last-resort recovery when the provider rejects even repaired history.
    Returns number of assistant turns removed.
    """
    if not messages:
        return 0
    out: list[dict[str, Any]] = []
    removed = 0
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if (
            not isinstance(msg, dict)
            or msg.get("role") != "assistant"
            or not msg.get("tool_calls")
        ):
            out.append(msg)
            i += 1
            continue
        tcs = normalize_tool_calls(list(msg.get("tool_calls") or []))
        broken = False
        for tc in tcs:
            raw = (tc.get("function") or {}).get("arguments") or ""
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, dict) and (
                    parsed.get("_invalid_json") or parsed.get("_truncated")
                ):
                    broken = True
                    break
            except (json.JSONDecodeError, TypeError):
                broken = True
                break
        j = i + 1
        while (
            j < n
            and isinstance(messages[j], dict)
            and messages[j].get("role") == "tool"
        ):
            j += 1
        if broken:
            removed += 1
            out.append(
                {
                    "role": "user",
                    "content": (
                        "(System: a prior tool call was truncated mid-JSON and "
                        "cannot be replayed to the model. Continue from tool "
                        "results already shown; do not repeat that call.)"
                    ),
                }
            )
            i = j
            continue
        out.append({**msg, "tool_calls": tcs})
        for k in range(i + 1, j):
            out.append(messages[k])
        i = j
    messages[:] = out
    return removed


# Re-export policy helpers used by stream loop call sites.
__all__ = [
    "StreamRoundState",
    "accumulate_tool_call_delta",
    "apply_openai_sse_chunk",
    "build_runtime_system_block",
    "build_assistant_api_message",
    "ensure_tool_call_pairings",
    "filter_fresh_tool_calls",
    "finalize_round_text",
    "iter_openai_sse_content",
    "looks_like_pseudo_tools",
    "message_wants_tools",
    "repair_tool_arguments_in_messages",
    "strip_broken_tool_call_turns",
    "normalize_tool_calls",
    "parse_pseudo_tool_calls",
    "parse_sse_data_line",
    "repair_reasoning_content_in_messages",
    "should_enable_tools",
    "tool_call_fingerprint",
]
