"""L0 mechanical pruning for the model send-view (Memory Harness)."""

from __future__ import annotations

import json
from typing import Any


def _tool_fingerprint(msg: dict[str, Any]) -> str | None:
    """Fingerprint for tool result messages when possible."""
    if msg.get("role") != "tool":
        return None
    # OpenAI-style tool messages often lack name; fingerprint content prefix.
    content = msg.get("content")
    if not isinstance(content, str):
        try:
            content = json.dumps(content, sort_keys=True, default=str)
        except Exception:
            content = str(content)
    name = msg.get("name") or msg.get("tool_call_id") or ""
    return f"{name}::{(content or '')[:240]}"


def prune_messages_for_send(
    messages: list[dict[str, Any]],
    *,
    max_tool_chars: int = 0,
    dedupe_tools: bool = True,
) -> list[dict[str, Any]]:
    """Return a pruned *copy* of messages for the provider request.

    Does not mutate stored session history. Strategies:
    - Drop empty content noise
    - **No truncation by default** (max_tool_chars=0): full answers/thinking/tools
    - Optional truncate only when max_tool_chars > 0 (legacy/tests)
    - Deduplicate identical tool results (keep latest; keeps one full copy)
    """
    if not messages:
        return []

    # First pass: only truncate when explicitly requested (max_tool_chars > 0)
    trimmed: list[dict[str, Any]] = []
    for msg in messages:
        m = dict(msg)
        role = m.get("role")
        content = m.get("content")
        if (
            max_tool_chars > 0
            and isinstance(content, str)
            and len(content) > max_tool_chars
        ):
            if role == "tool":
                m["content"] = (
                    content[:max_tool_chars]
                    + "\n…[harness truncated tool output — re-read file or re-run if needed]"
                )
            elif role == "assistant":
                m["content"] = content[:max_tool_chars] + "\n…[truncated]"
        trimmed.append(m)

    if not dedupe_tools:
        return trimmed

    # Second pass: keep only latest of each tool fingerprint (scan newest→oldest)
    seen: set[str] = set()
    out_rev: list[dict[str, Any]] = []
    for msg in reversed(trimmed):
        fp = _tool_fingerprint(msg)
        if fp is not None:
            if fp in seen:
                # Replace with short placeholder so tool_call pairing can still work
                placeholder = dict(msg)
                placeholder["content"] = (
                    "(duplicate tool result removed by Memory Harness — see latest occurrence)"
                )
                out_rev.append(placeholder)
                continue
            seen.add(fp)
        out_rev.append(msg)
    out_rev.reverse()
    return out_rev
