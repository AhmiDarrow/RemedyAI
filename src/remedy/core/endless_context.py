"""Endless local coding under a fixed n_ctx (no bigger model required).

Problem
-------
Cloud agents assume 128k–200k. Local RMB hosts have a *hard* window (e.g. 32k).
Remedy's default payload (system head + 80 tool schemas + skill bodies + history)
can exceed that on turn one — the server returns ``exceed_context_size_error``
and the stream dies. Users experience "local models can't build."

Solution
--------
Treat the model window as a **rolling workspace**, not a growing transcript:

1. **Authoritative n_ctx** — live cache / rmb.json / size heuristic (never cloud).
2. **Reserve completion** — every request leaves headroom for the answer/tools.
3. **Hard fit cascade** — before HTTP, shrink tools → head → history until the
   prompt estimate fits. Prefer content-addressed handles over prose dumps.
4. **Coding tool pack** — when over budget, keep only the tools that build code.

This module is pure + sync. Call ``fit_local_request`` from local provider
``build_body`` so *every* path (stream, non-stream, mid-tool-chain) is safe.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Fraction of n_ctx reserved for the model completion (tool args + answer).
_COMPLETION_FRAC = 0.18
_COMPLETION_MIN = 768
_COMPLETION_MAX = 4096
# Write/edit tool rounds need far more n_predict (JSON path + old + new).
_COMPLETION_FRAC_WRITE = 0.35
_COMPLETION_MIN_WRITE = 4096
_COMPLETION_MAX_WRITE = 12288
_SAFETY = 192

# Core tools that can ship a calculator / multi-file app under a tight window.
CODING_TOOL_PACK: tuple[str, ...] = (
    "list_dir",
    "file_read",
    "file_write",
    "file_edit",
    "file_edit_batch",
    "bash_exec",
    "repo_search",
    "mission_start",
    "mission_status",
    "mission_verify",
    "mission_complete",
    "skill_activate",
    "skill_search",
    "memory_search",
    "memory_save",
    "web_fetch",
    "help_read",
)

# Secondary tools kept only when the window still has room after the coding pack.
EXPAND_TOOL_PACK: tuple[str, ...] = (
    "skill_run",
    "skill_reload",
    "partner_remember",
    "partner_recall",
    "spread_run",
    "local_discover",
    "web_search",
    "job_run",
    "job_status",
    "update_settings",
    "get_settings",
)

_HEAD_DROP_MARKERS: tuple[str, ...] = (
    "Skills catalog",
    "[Skill auto-suggest]",
    "Self-configuration:",
    "Durable memory:",
    "Owner's manual",
    "Recent memory",
    "Built-in tools (executable)",
    "Working memory (retrieved by query):",
    "RMB local agent:",
    "[Memory Harness]",
    "[Working memory]",
    "Soul Field",
    "Partner memory",
)


def estimate_messages_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Cheap token estimate for OpenAI-style chat messages (tools/tool_calls included)."""
    total = 0
    for m in messages or []:
        if not isinstance(m, dict):
            total += 8
            continue
        total += 6  # role overhead
        c = m.get("content")
        if isinstance(c, str):
            total += max(1, len(c) // 3)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += max(1, len(part["text"]) // 3)
                else:
                    total += 48
        tcs = m.get("tool_calls")
        if isinstance(tcs, list):
            for tc in tcs:
                try:
                    total += max(8, len(json.dumps(tc, default=str)) // 3)
                except Exception:
                    total += 64
        if m.get("name"):
            total += 4
    return max(1, total)


def estimate_tools_tokens(tools: list[dict[str, Any]] | None) -> int:
    if not tools:
        return 0
    try:
        raw = json.dumps(tools, default=str, separators=(",", ":"))
    except Exception:
        raw = str(tools)
    # Tool schemas are dense JSON; slightly denser than free text.
    return max(1, len(raw) // 3 + 8 * len(tools))


def prompt_budget(window: int, *, write_tools: bool = False) -> tuple[int, int]:
    """Return (prompt_budget, completion_reserve) for a fixed n_ctx.

    When *write_tools* is True (file_write/file_edit armed), reserve a much
    larger completion band so tool-call JSON is not cut mid-stream
    (TOOL_ARGS_TRUNCATED / HISTORY_STUB fail loops).
    """
    win = max(2048, int(window or 8192))
    if write_tools:
        completion = max(
            _COMPLETION_MIN_WRITE,
            min(_COMPLETION_MAX_WRITE, int(win * _COMPLETION_FRAC_WRITE)),
        )
        # 8k-class windows: still leave ≥2k completion for a small edit
        if win < 10000:
            completion = max(2048, min(4096, win // 3))
    else:
        completion = max(
            _COMPLETION_MIN,
            min(_COMPLETION_MAX, int(win * _COMPLETION_FRAC)),
        )
        # On tiny windows still leave something for the answer
        if win < 6000:
            completion = max(512, min(1024, win // 4))
    budget = max(1024, win - completion - _SAFETY)
    return budget, completion


def resolve_local_window(
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> int:
    """Authoritative fixed window for local/RMB budgeting."""
    # 1. rmb.json is the user's configured physical window when chat is RMB
    try:
        from remedy.nanoswarm.token_nanobot import cache_context_window
        from remedy.runtime.rmb.config import load_rmb_json, merge_state
        from remedy.runtime.rmb.mode import is_rmb_provider

        if is_rmb_provider(provider, base_url) or (provider or "").lower() == "rmb":
            st = merge_state(load_rmb_json())
            ctx = int(st.get("ctx_size") or 0)
            if ctx >= 2048:
                url = str(st.get("base_url") or base_url or "")
                cache_context_window(url, model, ctx)
                cache_context_window(url, st.get("model_id"), ctx)
                return ctx
    except Exception:
        pass

    try:
        from remedy.nanoswarm.token_nanobot import (
            get_cached_context_window,
            resolve_context_window,
        )

        hit = get_cached_context_window(base_url, model)
        if hit and hit >= 2048:
            return int(hit)
        return int(
            resolve_context_window(provider, model, base_url=base_url) or 8192
        )
    except Exception:
        return 8192


def tool_pack_for_window(window: int) -> tuple[int, int, int]:
    """(max_tools, desc_chars, max_props) scaled to n_ctx."""
    w = int(window or 8192)
    if w <= 6144:
        return 12, 72, 8
    if w <= 10240:
        return 16, 100, 10
    if w <= 16384:
        return 22, 140, 12
    if w <= 32768:
        return 28, 160, 12
    return 40, 200, 14


def slim_tools_pack(
    tools: list[dict[str, Any]] | None,
    *,
    names: tuple[str, ...] | list[str] | None = None,
    max_tools: int = 20,
    desc_chars: int = 120,
    max_props: int = 10,
    name_only: bool = False,
) -> list[dict[str, Any]] | None:
    """Priority-filter + compact tool schemas."""
    if not tools:
        return tools
    from remedy.core.agent_llm import slim_tools_for_local

    # Optional allowlist (coding pack)
    if names:
        allow = {str(n) for n in names}
        filtered: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            n = str((fn or {}).get("name") or "")
            if n in allow:
                filtered.append(t)
        # Preserve pack order
        by_name = {}
        for t in filtered:
            fn = t.get("function") if isinstance(t, dict) else None
            by_name[str((fn or {}).get("name") or "")] = t
        ordered = [by_name[n] for n in names if n in by_name]
        # Keep any remaining allowed that weren't in preferred order
        for t in filtered:
            fn = t.get("function") if isinstance(t, dict) else None
            n = str((fn or {}).get("name") or "")
            if n and n not in {str((x.get("function") or {}).get("name")) for x in ordered}:
                ordered.append(t)
        tools = ordered

    slim = slim_tools_for_local(
        tools,
        max_tools=max_tools,
        desc_chars=0 if name_only else desc_chars,
        max_props=2 if name_only else max_props,
    )
    if name_only and slim:
        out: list[dict[str, Any]] = []
        for t in slim:
            fn = t.get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            # Minimal schema: name + empty object params (model still sees required none)
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
        return out
    return slim


def _truncate_str(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    keep = max(64, max_chars - 40)
    return s[:keep] + f"\n…[truncated {len(s) - keep} chars; use tools to re-read]"


def _shrink_system_head(messages: list[dict[str, Any]], target_chars: int) -> list[dict[str, Any]]:
    """Drop expendable head blocks inside system messages; hard-truncate if needed."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "system":
            out.append(m)
            continue
        content = m.get("content")
        if not isinstance(content, str) or len(content) <= target_chars:
            out.append(m)
            continue
        parts = [p for p in re.split(r"\n{2,}", content) if p.strip()]
        # Drop lowest-value blocks first
        kept: list[str] = []
        dropped: list[str] = []
        for p in parts:
            if any(mk in p for mk in _HEAD_DROP_MARKERS):
                dropped.append(p)
            else:
                kept.append(p)
        # Re-add dropped only if room
        body = "\n\n".join(kept)
        for p in dropped:
            cand = (body + "\n\n" + p) if body else p
            if len(cand) <= target_chars:
                body = cand
        if not body.strip() and parts:
            body = parts[0]
        if len(body) > target_chars:
            body = _truncate_str(body, target_chars)
        nm = dict(m)
        nm["content"] = body
        out.append(nm)
    return out


def _offload_fat_tool_results(
    messages: list[dict[str, Any]],
    *,
    body_cap: int = 900,
) -> list[dict[str, Any]]:
    """Replace large tool/user dumps with short stubs (handles when available)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("tool", "function") and isinstance(content, str) and len(content) > body_cap:
            nm = dict(m)
            nm["content"] = (
                content[: body_cap // 2]
                + f"\n…[tool result offloaded; {len(content)} chars total — "
                "re-run the tool or memory_search if you need the full body]"
            )
            out.append(nm)
            continue
        if role == "assistant" and isinstance(content, str) and len(content) > body_cap * 2:
            nm = dict(m)
            # Keep tool_calls; truncate prose
            nm["content"] = _truncate_str(content, body_cap * 2)
            out.append(nm)
            continue
        out.append(m)
    return out


def _collapse_old_history(
    messages: list[dict[str, Any]],
    *,
    keep_tail: int = 8,
) -> list[dict[str, Any]]:
    """Keep all system + first user seed + last N non-system turns; stub the middle."""
    if len(messages) <= keep_tail + 3:
        return messages
    systems = [m for m in messages if isinstance(m, dict) and m.get("role") == "system"]
    non_sys = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
    if len(non_sys) <= keep_tail:
        return messages
    head = non_sys[:1]
    tail = non_sys[-keep_tail:]
    dropped = len(non_sys) - 1 - keep_tail
    if dropped <= 0:
        return messages
    stub = {
        "role": "system",
        "content": (
            f"[Endless context] {dropped} earlier turns held off-window in Session Brief / "
            "middleman / disk. Continue the live task; re-read files with tools if detail "
            "is missing. Do not re-ask the user for context you can recover with tools."
        ),
    }
    return systems + head + [stub] + tail


def fit_local_request(
    messages: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None,
    *,
    window: int,
    provider: str | None = None,
    model: str | None = None,
    coding_bias: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None, dict[str, Any]]:
    """Hard-fit messages+tools into a fixed n_ctx. Always returns a sendable payload.

    Never raises. Meta describes which cascade levels ran (for metrics/debug).
    """
    msgs: list[dict[str, Any]] = [dict(m) for m in (messages or []) if isinstance(m, dict)]
    tls: list[dict[str, Any]] | None = list(tools) if tools else None
    # Prefer write headroom when write tools are armed (or coding pack present)
    _writes = False
    try:
        from remedy.core.local_agent_optimize import tools_include_writes

        _writes = tools_include_writes(tools)
        if not _writes and coding_bias and tools:
            _writes = True
    except Exception:
        _writes = bool(coding_bias and tools)
    budget, completion = prompt_budget(window, write_tools=_writes)
    max_tools, desc_chars, max_props = tool_pack_for_window(window)
    meta: dict[str, Any] = {
        "window": int(window),
        "prompt_budget": budget,
        "completion_reserve": completion,
        "levels": [],
        "est_before": 0,
        "est_after": 0,
        "tools_before": len(tls or []),
        "tools_after": len(tls or []),
    }

    def _est() -> int:
        return estimate_messages_tokens(msgs) + estimate_tools_tokens(tls)

    before = _est()
    meta["est_before"] = before
    if before <= budget:
        meta["est_after"] = before
        meta["levels"].append("ok")
        return msgs, tls, meta

    # L1 — slim tools (window-scaled); coding pack if still huge
    meta["levels"].append("slim_tools")
    if tls:
        tls = slim_tools_pack(
            tls,
            max_tools=max_tools,
            desc_chars=desc_chars,
            max_props=max_props,
        )
    if _est() > budget and tls and coding_bias:
        meta["levels"].append("coding_pack")
        pack = CODING_TOOL_PACK
        # If window has room after coding pack estimate, expand slightly
        tls = slim_tools_pack(
            tools,
            names=pack,
            max_tools=min(max_tools, len(pack)),
            desc_chars=min(desc_chars, 120),
            max_props=max_props,
        )
        if _est() < budget * 0.7 and tools:
            # Room for a few secondary tools
            expanded = list(pack) + list(EXPAND_TOOL_PACK)
            trial = slim_tools_pack(
                tools,
                names=tuple(expanded),
                max_tools=max_tools,
                desc_chars=desc_chars,
                max_props=max_props,
            )
            if estimate_messages_tokens(msgs) + estimate_tools_tokens(trial) <= budget:
                tls = trial

    # L2 — offload fat tool results (keep real bodies longer — partner needs source)
    if _est() > budget:
        meta["levels"].append("offload_tools")
        _cap = 4_000 if window <= 16384 else 12_000
        msgs = _offload_fat_tool_results(msgs, body_cap=_cap)

    # L3 — shrink system head (skills catalog, soul prose, manuals, …)
    if _est() > budget:
        meta["levels"].append("shrink_head")
        # Cap system text share to ~35% of prompt budget
        sys_cap = max(800, int(budget * 0.35) * 3)  # chars ≈ tokens*3
        msgs = _shrink_system_head(msgs, sys_cap)

    # L4 — collapse middle history (rolling workspace)
    if _est() > budget:
        meta["levels"].append("collapse_history")
        keep = 8 if window <= 16384 else 14
        msgs = _collapse_old_history(msgs, keep_tail=keep)
        msgs = _offload_fat_tool_results(msgs, body_cap=2_000)

    # L5 — name-only tool schemas (last tool shrinkage)
    if _est() > budget and tls:
        meta["levels"].append("name_only_tools")
        tls = slim_tools_pack(
            tls,
            names=CODING_TOOL_PACK if coding_bias else None,
            max_tools=min(max_tools, 20),
            name_only=True,
        )

    # L6 — aggressive history: keep system + recent turns (never starve write tools)
    if _est() > budget:
        meta["levels"].append("aggressive_tail")
        msgs = _collapse_old_history(msgs, keep_tail=6)
        msgs = _offload_fat_tool_results(msgs, body_cap=1_200)
        msgs = _shrink_system_head(msgs, max(600, int(budget * 0.25) * 3))

    # L7 — NEVER strip tools on coding/build turns (drop_tools was neutering agents).
    # If still over budget: keep CODING_TOOL_PACK at minimum, trim history harder.
    if _est() > budget and tls:
        if coding_bias:
            meta["levels"].append("keep_coding_tools")
            tls = slim_tools_pack(
                tools or tls,
                names=CODING_TOOL_PACK,
                max_tools=min(12, max_tools),
                desc_chars=80,
                max_props=8,
            )
            msgs = _collapse_old_history(msgs, keep_tail=4)
            msgs = _offload_fat_tool_results(msgs, body_cap=600)
        elif _est() - estimate_tools_tokens(tls) <= budget:
            meta["levels"].append("drop_tools")
            tls = None
        else:
            meta["levels"].append("drop_tools_and_trim")
            tls = None
            # Keep only last user message + thin system
            systems = [
                m
                for m in msgs
                if m.get("role") == "system"
            ][:1]
            if systems:
                systems[0] = dict(systems[0])
                systems[0]["content"] = _truncate_str(
                    str(systems[0].get("content") or ""),
                    max(400, int(budget * 0.2) * 3),
                )
            tail = [m for m in msgs if m.get("role") == "user"][-1:]
            if not tail:
                tail = msgs[-1:]
            msgs = systems + tail

    # Final hard truncate on any remaining oversized content fields
    if _est() > budget:
        meta["levels"].append("hard_truncate")
        # Binary shrink content until under
        for _ in range(12):
            if _est() <= budget:
                break
            # Truncate largest content blob
            best_i = -1
            best_len = 0
            for i, m in enumerate(msgs):
                c = m.get("content")
                if isinstance(c, str) and len(c) > best_len:
                    best_len = len(c)
                    best_i = i
            if best_i < 0 or best_len < 200:
                break
            m = dict(msgs[best_i])
            m["content"] = _truncate_str(str(m["content"]), max(120, best_len // 2))
            msgs[best_i] = m

    after = _est()
    meta["est_after"] = after
    meta["tools_after"] = len(tls or [])
    meta["fits"] = after <= budget
    if after > budget:
        logger.warning(
            "endless_context: still over budget after cascade "
            "(est=%s budget=%s window=%s levels=%s)",
            after,
            budget,
            window,
            meta["levels"],
        )
    else:
        logger.debug(
            "endless_context: fit %s→%s / %s (window=%s levels=%s tools=%s→%s)",
            before,
            after,
            budget,
            window,
            meta["levels"],
            meta["tools_before"],
            meta["tools_after"],
        )
    return msgs, tls, meta


def apply_fit_to_body(
    body: dict[str, Any],
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """In-place-ish fit of a chat-completions body for local hosts."""
    if not isinstance(body, dict):
        return body
    window = resolve_local_window(
        provider=provider, model=model, base_url=base_url
    )
    msgs = body.get("messages") if isinstance(body.get("messages"), list) else []
    tools = body.get("tools") if isinstance(body.get("tools"), list) else None
    fitted_msgs, fitted_tools, meta = fit_local_request(
        msgs,
        tools,
        window=window,
        provider=provider,
        model=model,
        coding_bias=True,
    )
    body = dict(body)
    body["messages"] = fitted_msgs
    if fitted_tools is None:
        body.pop("tools", None)
        body.pop("tool_choice", None)
    else:
        body["tools"] = fitted_tools
    # Completion reserve from fit — clamp max_tokens if present
    try:
        from remedy.core.local_agent_optimize import tools_include_writes

        _writes = tools_include_writes(fitted_tools)
    except Exception:
        _writes = False
    _, completion = prompt_budget(window, write_tools=_writes)
    # Remaining after fitted prompt
    used = estimate_messages_tokens(fitted_msgs) + estimate_tools_tokens(fitted_tools)
    remain = max(256, window - used - _SAFETY)
    # Hard ceiling for this request: never exceed free context
    cap = max(256, min(completion, remain))
    if body.get("max_tokens") is not None:
        try:
            cur = int(body["max_tokens"])
            if _writes:
                # Prefer the larger of provider request and write reserve, ≤ remain
                body["max_tokens"] = max(256, min(max(cur, cap), remain))
            else:
                body["max_tokens"] = max(256, min(cur, cap))
        except (TypeError, ValueError):
            body["max_tokens"] = cap
    else:
        body["max_tokens"] = cap
    body["_remedy_endless"] = {
        "window": window,
        "est": meta.get("est_after"),
        "budget": meta.get("prompt_budget"),
        "levels": meta.get("levels"),
        "fits": meta.get("fits"),
    }
    return body
