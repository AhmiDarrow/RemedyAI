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
    token_budget: int | None = None,
    reserve_tokens: int = 0,
    provider: str | None = None,
    model: str | None = None,
    collapse_completed_tools: bool = False,
    keep_recent_tool_pairs: int = 4,
) -> list[dict[str, Any]]:
    """Return a pruned *copy* of messages for the provider request.

    Does not mutate stored session history. Strategies:
    - Drop empty content noise
    - **No truncation by default** (max_tool_chars=0): full answers/thinking/tools
    - Optional truncate only when max_tool_chars > 0 (legacy/tests)
    - Deduplicate identical tool results (keep latest; keeps one full copy)
    - Optional collapse_completed_tools: structural prune of older tool spans
      (keep last N assistant+tool pairs full; earlier tool bodies → short outcomes)
    - Optional token_budget: shrink older tool bodies until under budget
      (reserve_tokens held for Session Brief / reply headroom)
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
        out = trimmed
    else:
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
        out = out_rev

    if collapse_completed_tools:
        out = _collapse_old_tool_spans(
            out, keep_recent_pairs=max(1, int(keep_recent_tool_pairs))
        )

    if token_budget is not None and token_budget > 0:
        out = _shrink_to_token_budget(
            out,
            budget=max(256, int(token_budget) - max(0, int(reserve_tokens))),
            provider=provider,
            model=model,
        )
    return out


def _outcome_line(content: str, *, name: str = "") -> str:
    """Accuracy-preserving collapse: status, paths, error, first/last signals."""
    text = (content or "").strip()
    if not text:
        return "(empty tool result)"
    low = text[:800].lower()
    errish = any(
        k in low
        for k in ("error", "failed", "traceback", "exception", "errno", "denied")
    )
    status = "ERR" if errish else "OK"
    # Paths
    try:
        from remedy.memory.harness.compressor import extract_paths_from_text

        paths = extract_paths_from_text(text, limit=4)
    except Exception:
        paths = []
    path_s = ", ".join(paths[:3]) if paths else ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = (lines[0] if lines else text)[:140]
    last = (lines[-1] if len(lines) > 1 else "")[:100]
    bits = [f"{status}"]
    if name:
        bits.append(name)
    if path_s:
        bits.append(path_s)
    bits.append(first)
    if last and last != first:
        bits.append(f"… {last}")
    body = " · ".join(bits)
    return (
        f"{body}\n…[tool span collapsed — outcome retained; "
        f"re-read path or re-run tool if full output needed]"
    )


def _collapse_old_tool_spans(
    messages: list[dict[str, Any]],
    *,
    keep_recent_pairs: int = 4,
) -> list[dict[str, Any]]:
    """Structural prune: older tool results → short outcome lines; keep recent full.

    Preserves role/tool_call_id pairing so providers still accept the history.
    """
    # Index of tool messages from oldest to newest
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idxs) <= keep_recent_pairs:
        return messages
    protect = set(tool_idxs[-keep_recent_pairs:])
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i in protect or msg.get("role") != "tool":
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < 200:
            out.append(msg)
            continue
        m = dict(msg)
        name = str(msg.get("name") or msg.get("tool_call_id") or "")
        m["content"] = _outcome_line(content, name=name)
        out.append(m)
    return out


def _shrink_to_token_budget(
    messages: list[dict[str, Any]],
    *,
    budget: int,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Progressively cap tool/assistant bodies from oldest until under budget."""
    try:
        from remedy.memory.harness.compressor import estimate_tokens
    except Exception:
        return messages

    msgs = [dict(m) for m in messages]
    est = estimate_tokens(msgs, provider=provider, model=model)
    if est <= budget:
        return msgs

    caps = (8000, 4000, 2000, 800, 200)
    for cap in caps:
        for i, m in enumerate(msgs):
            if m.get("role") not in ("tool", "assistant"):
                continue
            # Prefer trimming older tool noise first
            content = m.get("content")
            if isinstance(content, str) and len(content) > cap:
                msgs[i] = dict(m)
                msgs[i]["content"] = content[:cap] + "\n…[budget trim]"
        est = estimate_tokens(msgs, provider=provider, model=model)
        if est <= budget:
            break
    return msgs
