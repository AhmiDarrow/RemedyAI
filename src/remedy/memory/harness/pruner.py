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
    keep_recent_tool_pairs: int = 8,
    protect_tool_call_ids: set[str] | frozenset[str] | None = None,
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
    - protect_tool_call_ids: open-subgoal tool results stay full (Partner State A)
    """
    if not messages:
        return []

    protect_ids = {str(x) for x in (protect_tool_call_ids or set()) if x}

    # First pass: only truncate when explicitly requested (max_tool_chars > 0)
    trimmed: list[dict[str, Any]] = []
    for msg in messages:
        m = dict(msg)
        role = m.get("role")
        content = m.get("content")
        tcid = str(m.get("tool_call_id") or "")
        if (
            max_tool_chars > 0
            and isinstance(content, str)
            and len(content) > max_tool_chars
            and tcid not in protect_ids
        ):
            if role == "tool":
                m["content"] = (
                    content[:max_tool_chars]
                    + "\n…[harness truncated tool output — outcome retained in history]"
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
            out,
            keep_recent_pairs=max(1, int(keep_recent_tool_pairs)),
            protect_tool_call_ids=protect_ids,
        )

    if token_budget is not None and token_budget > 0:
        out = _shrink_to_token_budget(
            out,
            budget=max(256, int(token_budget) - max(0, int(reserve_tokens))),
            provider=provider,
            model=model,
            keep_recent_tools=max(1, int(keep_recent_tool_pairs)),
            protect_tool_call_ids=protect_ids,
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
        f"{body}\n…[tool span collapsed — outcome retained in history. "
        f"Do not re-run unless path or request changed]"
    )


def _collapse_old_tool_spans(
    messages: list[dict[str, Any]],
    *,
    keep_recent_pairs: int = 4,
    protect_tool_call_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Structural prune: older tool results → short outcome lines; keep recent full.

    Preserves role/tool_call_id pairing so providers still accept the history.
    Open-subgoal tool_call_ids (Partner State) are never collapsed.
    """
    protect_ids = {str(x) for x in (protect_tool_call_ids or set()) if x}
    # Index of tool messages from oldest to newest
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idxs) <= keep_recent_pairs and not protect_ids:
        return messages
    protect = set(tool_idxs[-keep_recent_pairs:]) if tool_idxs else set()
    # Also protect by tool_call_id for open subgoals
    for i, msg in enumerate(messages):
        if msg.get("role") == "tool" and str(msg.get("tool_call_id") or "") in protect_ids:
            protect.add(i)
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


def _body_char_total(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
    return total


def count_tool_messages(messages: list[dict[str, Any]] | None) -> int:
    return sum(1 for m in (messages or []) if m.get("role") == "tool")


def tool_chain_active(
    messages: list[dict[str, Any]] | None,
    *,
    lookback: int = 16,
) -> bool:
    """True when recent send-view looks like an in-progress multi-step tool chain.

    Used to keep more recent full tool bodies and avoid middle-history drops /
    "stop and compress" nudges mid code-chain.
    """
    msgs = list(messages or [])
    if not msgs:
        return False
    tail = msgs[-max(4, int(lookback)) :]
    tools = 0
    tool_call_asst = 0
    for m in tail:
        role = m.get("role")
        if role == "tool":
            tools += 1
        elif role == "assistant" and m.get("tool_calls"):
            tool_call_asst += 1
    if tools >= 2 or tool_call_asst >= 1:
        return True
    # Whole-history density: long coding turns accumulate many tools
    return count_tool_messages(msgs) >= 6


def keep_recent_for_chain(
    messages: list[dict[str, Any]] | None,
    base: int,
    *,
    ceiling: int = 14,
) -> int:
    """Raise keep-recent floor for long tool chains so multi-step work stays intact."""
    n = count_tool_messages(messages)
    floor = max(1, int(base))
    if n >= 16:
        floor = max(floor, 12)
    elif n >= 10:
        floor = max(floor, 10)
    elif n >= 6:
        floor = max(floor, 8)
    elif tool_chain_active(messages):
        floor = max(floor, 6)
    return min(int(ceiling), floor)


def _shrink_to_token_budget(
    messages: list[dict[str, Any]],
    *,
    budget: int,
    provider: str | None = None,
    model: str | None = None,
    keep_recent_tools: int = 4,
    protect_tool_call_ids: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Progressively cap older tool bodies until under budget.

    Protects the last ``keep_recent_tools`` tool messages (and does not chop
    assistant prose until tools are already at the floor cap).

    Token accounting: one full estimate up front, then incremental deltas
    from char savings (tokens-per-char density). Avoids re-BPE of the full
    list on every cap step. When the running estimate claims under budget,
    re-measure once so optimistic deltas cannot leave context over-limit
    (API failures abort long chains).
    """
    try:
        from remedy.memory.harness.compressor import estimate_tokens
    except Exception:
        return messages

    msgs = [dict(m) for m in messages]
    est = estimate_tokens(msgs, provider=provider, model=model)
    if est <= budget:
        return msgs

    body_chars = max(1, _body_char_total(msgs))
    # tokens per content char from the initial measure (includes msg overhead
    # amortized into density — slightly conservative for body-only trims).
    density = max(0.05, min(1.0, float(est) / float(body_chars)))
    trim_suffix = "\n…[budget trim]"

    def _remeasure() -> int:
        nonlocal est, density
        est = estimate_tokens(msgs, provider=provider, model=model)
        bc = max(1, _body_char_total(msgs))
        density = max(0.05, min(1.0, float(est) / float(bc)))
        return est

    def _apply_cap(role: str, protect: set[int], caps: tuple[int, ...]) -> bool:
        """Trim matching roles; return True if verified under budget."""
        nonlocal est
        for cap in caps:
            changed = False
            for i, m in enumerate(msgs):
                if m.get("role") != role or i in protect:
                    continue
                content = m.get("content")
                if not isinstance(content, str) or len(content) <= cap:
                    continue
                new_c = content[:cap] + trim_suffix
                saved = max(0, len(content) - len(new_c))
                if saved <= 0:
                    continue
                msgs[i] = dict(m)
                msgs[i]["content"] = new_c
                est = max(0, est - int(saved * density + 0.5))
                changed = True
            if changed and est <= budget:
                # Verify — do not stop early on optimistic density alone
                if _remeasure() <= budget:
                    return True
        return False

    # Protect more recent tools when the list is a long in-flight chain
    keep_n = max(1, int(keep_recent_tools))
    if tool_chain_active(msgs):
        keep_n = max(keep_n, keep_recent_for_chain(msgs, keep_n))
    tool_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    protect_tools = set(tool_idxs[-keep_n:]) if tool_idxs else set()
    protect_ids = {str(x) for x in (protect_tool_call_ids or set()) if x}
    if protect_ids:
        for i, m in enumerate(msgs):
            if m.get("role") == "tool" and str(m.get("tool_call_id") or "") in protect_ids:
                protect_tools.add(i)

    # Phase 1: trim unprotected tools only (ladders raised — 2k caps neutered agents)
    if _apply_cap("tool", protect_tools, (64_000, 32_000, 16_000, 8_000, 4_000)):
        return msgs

    # Phase 2: if still over, lightly trim older assistants (never last 2)
    asst_idxs = [i for i, m in enumerate(msgs) if m.get("role") == "assistant"]
    protect_asst = set(asst_idxs[-2:]) if asst_idxs else set()
    if _apply_cap("assistant", protect_asst, (16_000, 8_000, 2_000)):
        return msgs
    # Final truth for caller metrics path (messages already as lean as we allow)
    return msgs
