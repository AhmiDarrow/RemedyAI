"""Enforce Memory Harness send-view policy (soft/strong) — accuracy-first.

Mechanical slim on the hot path; optional local-model brief update in background.
Never blocks waiting for local inference.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


def resolve_context_window_for_runtime(runtime: Any) -> int:
    """Best-effort true window for fill% (not a fixed 200k)."""
    provider = str(
        getattr(runtime, "_llm_provider", None)
        or getattr(getattr(runtime, "config", None), "llm_provider", "")
        or ""
    )
    model = str(
        getattr(runtime, "_llm_model", None)
        or getattr(getattr(runtime, "config", None), "llm_model", "")
        or ""
    )
    try:
        from remedy.nanoswarm.token_nanobot import resolve_context_window

        return int(resolve_context_window(provider, model))
    except Exception:
        return 128_000


def apply_auto_harness_send_policy(
    runtime: Any,
    messages: list[dict[str, Any]],
    *,
    user_text: str = "",
    session_id: str = "",
    tool_result_char_cap: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Apply soft/strong send-view slim. Mutates/replaces messages list content.

    Returns (messages, meta) where meta has level, est, quality, local_queued.
    """
    meta: dict[str, Any] = {
        "level": None,
        "token_estimate": 0,
        "fill_pct": 0.0,
        "local_queued": False,
        "quality": None,
        "middle_replaced": False,
    }
    if str(getattr(runtime, "_harness_mode", "auto") or "auto").lower() == "off":
        return messages, meta

    from remedy.core.context_snapshot import build_context_snapshot
    from remedy.memory.harness.compressor import (
        compression_nudge_message,
        estimate_tokens,
        heuristic_merge_from_history,
    )
    from remedy.memory.harness.offload import maybe_offload_messages
    from remedy.memory.harness.pruner import prune_messages_for_send

    provider = str(
        getattr(runtime.config, "provider", None)
        or getattr(runtime.config, "llm_provider", "")
        or getattr(runtime, "_llm_provider", "")
        or ""
    )
    model = str(
        getattr(runtime.config, "model", None)
        or getattr(runtime.config, "llm_model", "")
        or getattr(runtime, "_llm_model", "")
        or ""
    )
    project_path = str(
        getattr(runtime.config, "project_path", None)
        or getattr(runtime, "_project_path", None)
        or ""
    ) or None
    sid = session_id or str(getattr(runtime, "_session_id", "") or "")
    window = resolve_context_window_for_runtime(runtime)
    min_pct = float(getattr(runtime, "_harness_min_pct", 0.75) or 0.75)
    max_pct = float(getattr(runtime, "_harness_max_pct", 0.92) or 0.92)

    snap = build_context_snapshot(
        messages=messages,
        user_text=user_text or "",
        brief=getattr(runtime, "_session_brief", None),
        session_id=sid,
        provider=provider or None,
        model=model or None,
        context_window=window,
        min_pct=min_pct,
        max_pct=max_pct,
        project_path=project_path,
    )
    runtime._last_context_snapshot = snap
    runtime._last_send_messages = list(messages)
    est = snap.token_estimate
    level = snap.nudge
    meta["level"] = level
    meta["token_estimate"] = est
    meta["fill_pct"] = snap.fill_pct
    meta["context_window"] = window

    # Inject policy + remedies + project pins
    injects: list[str] = []
    if snap.policy_system:
        injects.append(snap.policy_system)
    if snap.remedy_system:
        injects.append(snap.remedy_system)
    with suppress(Exception):
        from remedy.core.project_learning import pinned_constraints_block

        pin = pinned_constraints_block(project_path)
        if pin:
            injects.append(pin)
    if injects:
        messages.insert(
            -1,
            {"role": "system", "content": "\n\n".join(injects)},
        )

    if not level:
        with suppress(Exception):
            from remedy.core.metrics import default_registry

            default_registry.gauge("remedy_context_tokens_estimate").set(float(est))
        return messages, meta

    # Shared home for offload
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)

    if level == "soft":
        # Soft: collapse old tools + token budget prune (enforce lean send)
        budget = max(2048, int(window * max(0.55, min_pct - 0.05)))
        messages[:] = prune_messages_for_send(
            messages,
            max_tool_chars=0,
            dedupe_tools=True,
            collapse_completed_tools=True,
            keep_recent_tool_pairs=6,
            token_budget=budget,
            reserve_tokens=max(512, int(window * 0.08)),
            provider=provider or None,
            model=model or None,
        )
        with suppress(Exception):
            messages[:] = maybe_offload_messages(
                messages,
                session_id=sid,
                home=home,
                min_chars=8000,
                keep_recent_tools=6,
            )
        messages.insert(-1, compression_nudge_message("soft"))
        # Background local brief (non-blocking)
        with suppress(Exception):
            from remedy.memory.harness.local_brief import schedule_background_brief_update

            meta["local_queued"] = schedule_background_brief_update(
                runtime, messages, intent_hint=user_text, level="soft"
            )
        with suppress(Exception):
            from remedy.core.metrics import default_registry

            default_registry.counter(
                "remedy_context_auto_compress_total", level="soft"
            ).inc()
            default_registry.gauge("remedy_context_tokens_estimate").set(float(est))
        return messages, meta

    # strong
    tokens_before = est
    hard_cap = max(4_000, (tool_result_char_cap or 64_000) // 4)
    budget = max(2048, int(window * max(0.45, min_pct - 0.15)))
    messages[:] = prune_messages_for_send(
        messages,
        max_tool_chars=hard_cap,
        dedupe_tools=True,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=3,
        token_budget=budget,
        reserve_tokens=max(768, int(window * 0.1)),
        provider=provider or None,
        model=model or None,
    )
    with suppress(Exception):
        messages[:] = maybe_offload_messages(
            messages,
            session_id=sid,
            home=home,
            min_chars=4000,
            keep_recent_tools=3,
        )

    quality: dict[str, Any] = {}
    with suppress(Exception):
        brief = getattr(runtime, "_session_brief", None)
        if brief is not None:
            from remedy.core.session_quality import get_session_quality
            from remedy.memory.harness.quality import review_compress_quality

            pre_hist = list(messages)
            runtime._session_brief = heuristic_merge_from_history(
                brief, messages, intent_hint=user_text
            )
            # Append cumulative thread entry for this strong event
            with suppress(Exception):
                runtime._session_brief.append_history_thread(
                    f"Auto-compress at ~{snap.fill_pct:.0%} fill "
                    f"({tokens_before} tok est). Intent: "
                    f"{(runtime._session_brief.intent or user_text or '')[:200]}",
                    decisions_why=list(runtime._session_brief.decisions[-3:]),
                    blockers=list(runtime._session_brief.blockers[-3:]),
                )
            tokens_after = estimate_tokens(
                messages, provider=provider or None, model=model or None
            )
            quality = review_compress_quality(
                messages_before=pre_hist,
                brief=runtime._session_brief,
                tokens_before=tokens_before,
                tokens_after=tokens_after,
            )
            score = quality.get("score")
            if score is not None:
                runtime._session_brief.last_quality_score = float(score)
            get_session_quality(sid).record_compress(
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                quality=quality,
                source="auto_strong",
            )
            meta["quality"] = quality

    # Quality gate: only replace middle history when brief looks solid
    score = float((quality or {}).get("score") or 0.0)
    if score >= 0.65 and getattr(runtime, "_session_brief", None) is not None:
        with suppress(Exception):
            messages[:] = _replace_middle_with_brief_pointer(
                messages, runtime._session_brief
            )
            meta["middle_replaced"] = True
    elif score < 0.5:
        # Keep more fidelity — local/provider may improve brief later
        with suppress(Exception):
            messages.insert(
                -1,
                {
                    "role": "system",
                    "content": (
                        "[Memory Harness] Continuity quality is low — "
                        "prefer re-reading key files over guessing. "
                        "Session Brief may be incomplete."
                    ),
                },
            )

    messages.insert(-1, compression_nudge_message("strong"))

    # Background local brief (prefer free local over paid provider)
    local_ok = False
    with suppress(Exception):
        from remedy.memory.harness.local_brief import schedule_background_brief_update

        local_ok = schedule_background_brief_update(
            runtime, messages, intent_hint=user_text, level="strong"
        )
        meta["local_queued"] = local_ok

    # Phase E: paid provider compress only if local unavailable and quality poor
    if not local_ok and score < 0.55:
        with suppress(Exception):
            _try_provider_brief_fallback(runtime, messages, user_text=user_text)
            meta["provider_fallback"] = True

    with suppress(Exception):
        from remedy.core.metrics import default_registry

        default_registry.counter(
            "remedy_context_auto_compress_total", level="strong"
        ).inc()
        default_registry.gauge("remedy_context_tokens_estimate").set(float(est))

    return messages, meta


def _replace_middle_with_brief_pointer(
    messages: list[dict[str, Any]],
    brief: Any,
) -> list[dict[str, Any]]:
    """Keep system head + brief pointer + recent user/assistant tail + last user."""
    if len(messages) < 8:
        return messages
    # Keep first system messages and last 6 messages (includes current user)
    head: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system" and len(head) < 3:
            head.append(m)
        else:
            break
    tail = messages[-6:]
    from remedy.memory.harness.brief import brief_to_context_block

    block = brief_to_context_block(brief, max_chars=1600)
    mid = {
        "role": "system",
        "content": (
            "[Memory Harness] Prior work summarized in Session Brief below. "
            "Do not re-litigate decided choices. Re-read files if you need raw detail.\n\n"
            + (block or "(brief empty)")
        ),
    }
    # Avoid duplicating tail systems
    return head + [mid] + [m for m in tail if m not in head]


def _try_provider_brief_fallback(
    runtime: Any,
    messages: list[dict[str, Any]],
    *,
    user_text: str = "",
) -> None:
    """Rare paid path: one structured compress via provider if local queue failed.

    Synchronous short call — only when quality is already poor and local unavailable.
    Best-effort; failures are silent.
    """
    brief = getattr(runtime, "_session_brief", None)
    if brief is None:
        return
    # Prefer not to block long — skip if no API key
    key = str(getattr(runtime, "_llm_api_key", "") or "")
    if not key:
        return
    # Use heuristic only if we can't afford another call this turn
    # Mark that fallback was considered; full LLM path deferred to compress_context tool
    # to avoid doubling latency on the hot path. Enrich brief via heuristic merge.
    from remedy.memory.harness.compressor import heuristic_merge_from_history

    heuristic_merge_from_history(brief, messages, intent_hint=user_text)
    brief.append_history_thread(
        f"Provider-side heuristic refresh (local brief unavailable). "
        f"Focus: {(user_text or '')[:180]}",
        decisions_why=list(brief.decisions[-3:]),
        blockers=list(brief.blockers[-3:]),
    )
