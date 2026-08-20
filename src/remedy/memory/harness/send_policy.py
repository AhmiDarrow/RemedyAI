"""Enforce Memory Harness send-view policy (soft/strong) — accuracy-first.

Mechanical slim on the hot path; optional local-model brief update in background.
Never blocks waiting for local inference.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


def _evidence_eu_for_session(runtime: Any, sid: str) -> int:
    """Per-session evidence inject watermark (parallel tabs must not share)."""
    key = str(sid or "").strip() or "_"
    by = getattr(runtime, "_evidence_inject_eu_by_session", None)
    if isinstance(by, dict) and key in by:
        try:
            return int(by[key])
        except (TypeError, ValueError):
            return -1
    # Legacy single attribute — only when no map yet for this sid.
    try:
        return int(getattr(runtime, "_evidence_inject_eu", -1) or -1)
    except (TypeError, ValueError):
        return -1


def _set_evidence_eu_for_session(runtime: Any, sid: str, value: int) -> None:
    key = str(sid or "").strip() or "_"
    by = getattr(runtime, "_evidence_inject_eu_by_session", None)
    if not isinstance(by, dict):
        by = {}
        try:
            runtime._evidence_inject_eu_by_session = by
        except Exception:
            return
    by[key] = int(value)
    # Keep legacy mirror for single-tab paths / continuity reset.
    with suppress(Exception):
        runtime._evidence_inject_eu = int(value)


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
    base_url = str(
        getattr(runtime, "_llm_base_url", None)
        or getattr(getattr(runtime, "config", None), "llm_base_url", "")
        or ""
    )
    try:
        from remedy.core.llm_binding import get_llm_binding
        from remedy.nanoswarm.token_nanobot import resolve_context_window

        with suppress(Exception):
            bind = get_llm_binding(runtime)
            provider = bind.provider or provider
            model = bind.model or model
            base_url = bind.base_url or base_url
        return int(resolve_context_window(provider, model, base_url=base_url))
    except Exception:
        return 128_000


def _session_brief_store(runtime: Any) -> dict[str, Any]:
    """Per-session brief map on the shared runtime (concurrent tabs)."""
    store = getattr(runtime, "_session_briefs", None)
    if not isinstance(store, dict):
        store = {}
        runtime._session_briefs = store
    return store


def _ensure_session_brief(runtime: Any, session_id: str = "") -> Any:
    """Create or restore SessionBrief for this session_id.

    Concurrent tabs share one agent runtime; briefs are keyed by session so
    tab A compress does not wipe tab B's working memory.
    """
    sid = session_id or str(getattr(runtime, "_session_id", "") or "")
    store = _session_brief_store(runtime)
    if sid and sid in store:
        brief = store[sid]
        runtime._session_brief = brief
        return brief

    brief = getattr(runtime, "_session_brief", None)
    if brief is not None:
        bsid = str(getattr(brief, "session_id", "") or "")
        if sid and bsid and bsid != sid:
            # Park foreign brief under its own key; do not destroy it
            store[bsid] = brief
            brief = None
        else:
            # Same session, or an as-yet-unlabelled brief we adopt for `sid`.
            # (The old separate `elif sid and not bsid` branch set the id but
            # fell through to create a fresh empty brief, DESTROYING this one's
            # working memory — folded in here so the existing brief survives.)
            if sid and brief is not None and not bsid:
                with suppress(Exception):
                    brief.session_id = sid
            if sid and brief is not None:
                store[sid] = brief
            return brief

    from remedy.memory.harness.brief import SessionBrief

    brief = SessionBrief(session_id=sid)
    if sid:
        store[sid] = brief
        # Bound store size (LRU-ish: drop oldest keys)
        if len(store) > 48:
            for k in list(store.keys())[: max(0, len(store) - 40)]:
                if k != sid:
                    store.pop(k, None)
    runtime._session_brief = brief
    return brief


def _safe_insert_before_last(
    messages: list[dict[str, Any]],
    content: str | dict[str, Any],
) -> None:
    """Insert a system (or raw) message before the final turn; never IndexError."""
    msg = content if isinstance(content, dict) else {"role": "system", "content": str(content)}
    if len(messages) >= 2:
        messages.insert(-1, msg)
    elif messages:
        messages.insert(0, msg)
    else:
        messages.append(msg)


def _soft_inject_brief_pointer(
    messages: list[dict[str, Any]],
    brief: Any,
    *,
    silent: bool = False,
) -> bool:
    """Inject a compact Session Brief block after soft prune (local/RMB continuity)."""
    if not _brief_has_substance(brief):
        return False
    try:
        from remedy.memory.harness.brief import brief_to_context_block

        block = brief_to_context_block(brief, max_chars=900)
    except Exception:
        block = ""
    if not (block or "").strip():
        return False
    if silent:
        # No "please compress" language — memory just appears
        text = "[Working memory]\n" + block.strip()
    else:
        text = (
            "[Memory Harness] Session Brief (auto):\n"
            + block.strip()
            + "\nContinue from this state; re-read files if you need raw detail."
        )
    _safe_insert_before_last(messages, text)
    return True


def _brief_has_substance(brief: Any) -> bool:
    if brief is None:
        return False
    return bool(
        (getattr(brief, "intent", None) or "").strip()
        or (getattr(brief, "artifacts", None) or [])
        or (getattr(brief, "decisions", None) or [])
        or (getattr(brief, "decision_records", None) or [])
        or (getattr(brief, "history_thread", None) or [])
        or (getattr(brief, "key_paths", None) or [])
    )


def _chain_keep(messages: list[dict[str, Any]], base: int) -> int:
    from remedy.memory.harness.pruner import keep_recent_for_chain

    return keep_recent_for_chain(messages, base)


def _is_tool_chain(messages: list[dict[str, Any]]) -> bool:
    from remedy.memory.harness.pruner import tool_chain_active

    return tool_chain_active(messages)


def _protect_ids_from_runtime(runtime: Any) -> set[str]:
    """Open-subgoal tool_call_ids must stay full (Partner State Phase A)."""
    ids: set[str] = set()
    with suppress(Exception):
        from remedy.memory.partner_state import ensure_partner_state

        st = ensure_partner_state(runtime)
        ids |= st.protected_tool_call_ids()
        # Also protect recent write-set related txns
        for t in list(st.tool_txns)[-24:]:
            if t.effect == "write" and t.tool_call_id:
                ids.add(t.tool_call_id)
    return ids


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

    cfg = getattr(runtime, "config", None)
    provider = str(
        getattr(cfg, "provider", None)
        or getattr(cfg, "llm_provider", None)
        or getattr(runtime, "_llm_provider", "")
        or ""
    )
    model = str(
        getattr(cfg, "model", None)
        or getattr(cfg, "llm_model", None)
        or getattr(runtime, "_llm_model", "")
        or ""
    )
    project_path = str(
        getattr(cfg, "project_path", None)
        or getattr(runtime, "_project_path", None)
        or ""
    ) or None
    sid = session_id or str(getattr(runtime, "_session_id", "") or "")
    window = resolve_context_window_for_runtime(runtime)
    min_pct = float(getattr(runtime, "_harness_min_pct", 0.75) or 0.75)
    max_pct = float(getattr(runtime, "_harness_max_pct", 0.92) or 0.92)
    # Local/RMB fixed windows: compress much earlier so endless coding never
    # waits until fill% is already past physical n_ctx (tools count too).
    with suppress(Exception):
        from remedy.nanoswarm.token_nanobot import is_local_model
        from remedy.runtime.rmb.mode import harness_pcts_for_local_agent, is_rmb_provider

        if is_local_model(provider, model) or is_rmb_provider(provider):
            lo, hi = harness_pcts_for_local_agent()
            min_pct = min(float(min_pct), float(lo))
            max_pct = min(float(max_pct), float(hi))
            # Sub-16k: even earlier soft gate (tools + one file read fill fast)
            if int(window or 0) and int(window) <= 16384:
                min_pct = min(min_pct, 0.42)
                max_pct = min(max_pct, 0.68)
            meta["local_early_harness"] = True
    # Governor: compress earlier when stuck/waste — lower soft threshold.
    with suppress(Exception):
        from remedy.core.metabolism.governor import get_governor

        gov = get_governor(sid)
        if gov.compress_earlier:
            min_pct = max(0.35, float(min_pct) - 0.12)
            max_pct = max(min_pct + 0.05, float(max_pct) - 0.05)
            meta["governor_compress_earlier"] = True

    # Pre-classify tier for lean snapshot (cheap heuristics).
    # Honor browse / pure-action / attachments stashed by the react loop so
    # L2 agency does not get a light pack/scout-less snapshot.
    full_snap: bool | None = None
    pre_tier: int | None = None
    with suppress(Exception):
        from remedy.core.metabolism.tier import TurnTier, classify_turn_tier
        from remedy.core.turn_context import (
            current_plan_mode,
            turn_browse,
            turn_has_attachments,
            turn_pure_action,
        )

        t0 = classify_turn_tier(
            user_text or "",
            tools_enabled=True,
            browse=bool(turn_browse(runtime)),
            pure_action=bool(turn_pure_action(runtime)),
            has_attachments=bool(turn_has_attachments(runtime)),
            plan_mode=bool(current_plan_mode(runtime)),
        )
        pre_tier = int(t0)
        full_snap = t0 >= TurnTier.L2_AGENCY
        from remedy.core.turn_context import set_turn_tier, turn_tier

        set_turn_tier(int(turn_tier(runtime, default=pre_tier)), runtime)
        runtime._turn_tier_preclassified = pre_tier
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
        full_snapshot=full_snap,
        # Reuse pre_tier so snapshot force_spread skips a second classify walk
        turn_tier=pre_tier,
    )
    from remedy.core.turn_context import set_turn_context_snapshot

    set_turn_context_snapshot(snap, runtime)
    est = snap.token_estimate
    level = snap.nudge
    meta["level"] = level
    meta["token_estimate"] = est
    meta["fill_pct"] = snap.fill_pct
    meta["context_window"] = window
    meta["lean_snapshot"] = bool(full_snap is False)
    if pre_tier is not None:
        meta["pre_tier"] = pre_tier

    # Ground-truth history for compress quality — only when soft/strong runs
    # (skip expensive shallow copy on the common no-compress path).
    pre_prune: list[dict[str, Any]] | None = None
    if level:
        pre_prune = [dict(m) for m in messages]

    # Inject policy + remedies + project pins + metabolism deltas
    injects: list[str] = []
    if snap.policy_system:
        injects.append(snap.policy_system)
    if snap.remedy_system:
        injects.append(snap.remedy_system)
    with suppress(Exception):
        # Prefer pins already loaded on the snapshot (same turn — no second disk hit)
        pin = ""
        pins = (getattr(snap, "signals", None) or {}).get("project_pins")
        if pins:
            lines = ["[Project continuity notes]"]
            for p in list(pins)[:5]:
                lines.append(f"- {p}")
            pin = "\n".join(lines)
        if not pin:
            from remedy.core.project_learning import pinned_constraints_block

            pin = pinned_constraints_block(project_path)
        if pin:
            injects.append(pin)
    # Metabolism injects (evidence/crystal/CUA) owned by begin_turn_metabolism —
    # do not double-inject here (token waste + lock churn).
    if injects:
        _safe_insert_before_last(
            messages, {"role": "system", "content": "\n\n".join(injects)}
        )

    if not level:
        # Common path: no compress — skip pre_prune / prune / offload entirely
        runtime._last_send_messages = list(messages)
        with suppress(Exception):
            from remedy.core.metrics import default_registry

            default_registry.gauge("remedy_context_tokens_estimate").set(float(est))
        return messages, meta

    assert pre_prune is not None

    # Ensure brief exists before any compress work
    _ensure_session_brief(runtime, sid)
    with suppress(Exception):
        from remedy.memory.harness.local_brief import register_session_brief

        register_session_brief(sid, runtime._session_brief)

    # Shared home for offload
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)

    chain = _is_tool_chain(messages)
    meta["tool_chain_active"] = chain
    protect_ids = _protect_ids_from_runtime(runtime)
    meta["protected_tool_ids"] = len(protect_ids)

    if level == "soft":
        # Soft: collapse older sludge but keep a long recent tool window for
        # multi-step code chains (base 8, higher when many tools).
        keep = _chain_keep(messages, 8)
        budget = max(2048, int(window * max(0.55, min_pct - 0.05)))
        messages[:] = prune_messages_for_send(
            messages,
            max_tool_chars=0,
            dedupe_tools=True,
            collapse_completed_tools=True,
            keep_recent_tool_pairs=keep,
            token_budget=budget,
            reserve_tokens=max(512, int(window * 0.08)),
            provider=provider or None,
            model=model or None,
            protect_tool_call_ids=protect_ids,
        )
        with suppress(Exception):
            messages[:] = maybe_offload_messages(
                messages,
                session_id=sid,
                home=home,
                min_chars=8000,
                keep_recent_tools=keep,
            )
        # Refresh brief heuristically so soft prune keeps a continuity thread
        with suppress(Exception):
            from remedy.memory.harness.compressor import heuristic_merge_from_history

            b = getattr(runtime, "_session_brief", None)
            if b is not None and pre_prune:
                runtime._session_brief = heuristic_merge_from_history(
                    b, pre_prune, intent_hint=user_text
                )

        # RMB / local agent: silent mechanical compress — no "please compress" chatter
        _silent = False
        with suppress(Exception):
            from remedy.runtime.rmb.mode import silent_context_for_local_agent

            _base = str(
                getattr(runtime, "_llm_base_url", None)
                or getattr(cfg, "llm_base_url", "")
                or ""
            )
            _silent = silent_context_for_local_agent(
                provider=provider,
                base_url=_base,
                cfg={
                    "llm_provider": provider,
                    "llm_base_url": _base,
                },
            )
        # Soft brief pointer: always when substance (esp. silent local)
        with suppress(Exception):
            if _soft_inject_brief_pointer(
                messages,
                getattr(runtime, "_session_brief", None),
                silent=_silent,
            ):
                meta["soft_brief_injected"] = True
        if not _silent:
            _safe_insert_before_last(
                messages,
                compression_nudge_message("soft", tool_chain_active=chain),
            )
        else:
            meta["silent_context"] = True
        with suppress(Exception):
            from remedy.memory.harness.local_brief import schedule_background_brief_update

            meta["local_queued"] = schedule_background_brief_update(
                runtime, messages, intent_hint=user_text, level="soft"
            )
        with suppress(Exception):
            from remedy.memory.partner_state.continuity import schedule_continuity_core

            schedule_continuity_core(runtime, use_local=False)
        runtime._last_send_messages = list(messages)
        with suppress(Exception):
            from remedy.core.metrics import default_registry

            default_registry.counter(
                "remedy_context_auto_compress_total", level="soft"
            ).inc()
            default_registry.gauge("remedy_context_tokens_estimate").set(float(est))
        return messages, meta

    # --- strong ---
    tokens_before = est
    # Strong still protects a real recent window (was 3 — too thin for code chains).
    keep = _chain_keep(messages, 6 if chain else 5)
    # Full-power: do not slash tool bodies to 2k–4k mid-build
    _trc = int(tool_result_char_cap or 0)
    hard_cap = 128_000 if _trc <= 0 else max(16_000, _trc // 2)
    budget = max(2048, int(window * max(0.45, min_pct - 0.15)))
    messages[:] = prune_messages_for_send(
        messages,
        max_tool_chars=hard_cap,
        dedupe_tools=True,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=keep,
        token_budget=budget,
        reserve_tokens=max(768, int(window * 0.1)),
        provider=provider or None,
        model=model or None,
        protect_tool_call_ids=protect_ids,
    )
    with suppress(Exception):
        messages[:] = maybe_offload_messages(
            messages,
            session_id=sid,
            home=home,
            min_chars=4000,
            keep_recent_tools=keep,
        )

    quality: dict[str, Any] = {}
    with suppress(Exception):
        brief = _ensure_session_brief(runtime, sid)
        from remedy.core.session_quality import get_session_quality
        from remedy.memory.harness.quality import review_compress_quality
        from remedy.memory.partner_state import ensure_partner_state

        runtime._session_brief = heuristic_merge_from_history(
            brief, pre_prune, intent_hint=user_text
        )
        # Phase C: project epistemic graph into brief before quality score
        with suppress(Exception):
            ensure_partner_state(runtime).apply_graph_to_brief(runtime._session_brief)
        with suppress(Exception):
            runtime._session_brief.append_history_thread(
                f"Auto-compress at ~{snap.fill_pct:.0%} fill "
                f"({tokens_before} tok est). Intent: "
                f"{(runtime._session_brief.intent or user_text or '')[:200]}",
                decisions_why=list(runtime._session_brief.decisions[-3:]),
                blockers=list(runtime._session_brief.blockers[-3:]),
            )
        tokens_after_prune = estimate_tokens(
            messages, provider=provider or None, model=model or None
        )
        # Score against *pre-prune* history so the gate is not self-fulfilling
        quality = review_compress_quality(
            messages_before=pre_prune,
            brief=runtime._session_brief,
            tokens_before=tokens_before,
            tokens_after=tokens_after_prune,
        )
        score = quality.get("score")
        if score is not None:
            runtime._session_brief.last_quality_score = float(score)
        get_session_quality(sid).record_compress(
            tokens_before=tokens_before,
            tokens_after=tokens_after_prune,
            quality=quality,
            source="auto_strong",
        )
        meta["quality"] = quality

    # Quality gate: only replace middle when brief is solid (fail-closed).
    # During an active tool/code chain, require excellent continuity so we do
    # not drop the working tool history mid-implementation.
    score = float((quality or {}).get("score") or 0.0)
    quality_ok = bool((quality or {}).get("ok"))
    chain_now = _is_tool_chain(messages)
    replace_floor = 0.85 if chain_now else 0.65
    can_replace = (
        quality_ok
        and score >= replace_floor
        and _brief_has_substance(getattr(runtime, "_session_brief", None))
    )
    if can_replace:
        with suppress(Exception):
            messages[:] = _replace_middle_with_brief_pointer(
                messages,
                runtime._session_brief,
                keep_tool_pairs=max(8, keep),
            )
            meta["middle_replaced"] = True
            # Re-measure after middle replace for accurate metrics
            with suppress(Exception):
                tokens_final = estimate_tokens(
                    messages, provider=provider or None, model=model or None
                )
                if meta.get("quality") and isinstance(meta["quality"], dict):
                    meta["quality"]["tokens_after"] = tokens_final
                    if tokens_before > 0:
                        meta["quality"]["token_reduction_pct"] = round(
                            max(0.0, 1.0 - tokens_final / tokens_before) * 100, 1
                        )
    elif chain_now and quality_ok and score >= 0.65:
        meta["middle_replace_deferred_chain"] = True
    _silent_strong = False
    with suppress(Exception):
        from remedy.runtime.rmb.mode import silent_context_for_local_agent

        _base_s = str(
            getattr(runtime, "_llm_base_url", None)
            or getattr(cfg, "llm_base_url", "")
            or ""
        )
        _silent_strong = silent_context_for_local_agent(
            provider=provider,
            base_url=_base_s,
            cfg={
                "llm_provider": provider,
                "llm_base_url": _base_s,
            },
        )

    if not _silent_strong and (score < 0.5 or not quality_ok):
        with suppress(Exception):
            _safe_insert_before_last(
                messages,
                {
                    "role": "system",
                    "content": (
                        "[Memory Harness] Continuity quality is low — "
                        "verify exact facts from history/tools over guessing. "
                        "Session Brief may be incomplete."
                    ),
                },
            )

    if not _silent_strong:
        _safe_insert_before_last(
            messages,
            compression_nudge_message("strong", tool_chain_active=chain_now),
        )
    else:
        meta["silent_context"] = True
        # Silent strong without middle replace still needs a brief anchor
        if not meta.get("middle_replaced"):
            with suppress(Exception):
                if _soft_inject_brief_pointer(
                    messages,
                    getattr(runtime, "_session_brief", None),
                    silent=True,
                ):
                    meta["strong_brief_injected"] = True

    local_ok = False
    with suppress(Exception):
        from remedy.memory.harness.local_brief import schedule_background_brief_update

        local_ok = schedule_background_brief_update(
            runtime, messages, intent_hint=user_text, level="strong"
        )
        meta["local_queued"] = local_ok

    # Continuity Core (Partner State Phase E) — always schedule on strong
    with suppress(Exception):
        from remedy.memory.partner_state.continuity import schedule_continuity_core

        meta["continuity_core"] = schedule_continuity_core(
            runtime, use_local=True, priority=1
        )

    # Phase E: when local queue unavailable and quality poor, heuristic enrich only
    # (no paid provider call on the hot path — avoids latency + surprise $).
    if not local_ok and score < 0.55:
        with suppress(Exception):
            _try_heuristic_brief_fallback(runtime, pre_prune, user_text=user_text)
            meta["heuristic_fallback"] = True

    runtime._last_send_messages = list(messages)
    with suppress(Exception):
        from remedy.core.metrics import default_registry

        default_registry.counter(
            "remedy_context_auto_compress_total", level="strong"
        ).inc()
        default_registry.gauge("remedy_context_tokens_estimate").set(float(est))

    return messages, meta


def slim_messages_mid_turn(
    runtime: Any,
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
    tool_result_char_cap: int = 0,
) -> list[dict[str, Any]]:
    """Re-slim send-view between ReAct steps when fill is high.

    Lighter than full auto policy: prune + offload only (no middle replace,
    no history_thread spam, no compress nudge). Safe to call every provider
    round without derailing long tool/code chains.
    """
    if str(getattr(runtime, "_harness_mode", "auto") or "auto").lower() == "off":
        return messages
    if len(messages) < 6:
        return messages
    # L0/L1 lean: skip mid-turn slim entirely
    from remedy.core.turn_context import turn_tier as _tt

    if int(_tt(runtime, default=2) or 2) < 2:
        return messages

    from remedy.memory.harness.compressor import estimate_tokens
    from remedy.memory.harness.offload import maybe_offload_messages
    from remedy.memory.harness.pruner import prune_messages_for_send

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
    window = resolve_context_window_for_runtime(runtime)
    min_pct = float(getattr(runtime, "_harness_min_pct", 0.75) or 0.75)
    sid = session_id or str(getattr(runtime, "_session_id", "") or "")
    # Governor compress_earlier also applies mid-turn (not only next turn)
    with suppress(Exception):
        from remedy.core.metabolism.governor import get_governor

        if get_governor(sid).compress_earlier:
            min_pct = max(0.45, float(min_pct) - 0.12)
    # Cheap char-sum gate before BPE/token measure
    rough_chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            rough_chars += len(c)
        elif isinstance(c, list):
            rough_chars += 500 * len(c)
    if rough_chars < int(window * 0.45 * 3.5):
        return messages
    est = estimate_tokens(messages, provider=provider or None, model=model or None)
    fill = est / max(1, window)
    # Mid-turn: wait longer before slim so short tool bursts stay full fidelity
    if fill < max(0.60, min_pct - 0.12):
        # Inject evidence delta only when new EUs since last inject (no spam)
        with suppress(Exception):
            from remedy.core.metabolism.evidence import get_evidence_ledger
            from remedy.core.turn_context import turn_tier as _tt2

            if int(_tt2(runtime) or 1) >= 2:
                led = get_evidence_ledger(sid)
                last_eu = _evidence_eu_for_session(runtime, sid)
                if led.evidence_units > last_eu:
                    eblock = led.pointer_block(limit=8)
                    if eblock:
                        messages.append({"role": "system", "content": eblock})
                        _set_evidence_eu_for_session(
                            runtime, sid, led.evidence_units
                        )
        return messages

    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)

    # Stronger when above soft threshold — but keep a wide recent tool window
    strong = fill >= min_pct
    keep = _chain_keep(messages, 6 if strong else 8)
    _trc = int(tool_result_char_cap or 0)
    if not strong:
        hard_cap = 0
    elif _trc <= 0:
        hard_cap = 128_000
    else:
        hard_cap = max(16_000, _trc // 2)
    # Mid-turn budget slightly looser than turn-start strong so chains can finish
    budget = max(2048, int(window * (0.50 if strong else 0.58)))
    protect_ids = _protect_ids_from_runtime(runtime)
    out = prune_messages_for_send(
        messages,
        max_tool_chars=hard_cap,
        dedupe_tools=True,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=keep,
        token_budget=budget,
        reserve_tokens=max(512, int(window * 0.08)),
        provider=provider or None,
        model=model or None,
        protect_tool_call_ids=protect_ids,
    )
    with suppress(Exception):
        out = maybe_offload_messages(
            out,
            session_id=sid,
            home=home,
            min_chars=4000 if strong else 8000,
            keep_recent_tools=keep,
        )
    # Evidence delta after slim — only if new EUs (deduped)
    with suppress(Exception):
        from remedy.core.metabolism.evidence import get_evidence_ledger
        from remedy.core.turn_context import turn_tier as _tt3

        if int(_tt3(runtime) or 1) >= 2:
            led = get_evidence_ledger(sid)
            last_eu = _evidence_eu_for_session(runtime, sid)
            if led.evidence_units > last_eu:
                eblock = led.pointer_block(limit=10)
                if eblock:
                    out = list(out) + [{"role": "system", "content": eblock}]
                    _set_evidence_eu_for_session(runtime, sid, led.evidence_units)
    runtime._last_send_messages = list(out)
    return out


def _replace_middle_with_brief_pointer(
    messages: list[dict[str, Any]],
    brief: Any,
    *,
    keep_tool_pairs: int = 8,
) -> list[dict[str, Any]]:
    """Keep system head + brief pointer + recent *tool-pair* tail (not fixed N msgs).

    Preserves complete assistant/tool spans so providers stay happy and the
    model retains the last N tool results of an in-flight code chain.
    """
    if len(messages) < 8:
        return messages
    head: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system" and len(head) < 3:
            head.append(m)
        else:
            break

    keep_n = max(4, int(keep_tool_pairs))
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if tool_idxs:
        start_tool = (
            tool_idxs[-keep_n] if len(tool_idxs) >= keep_n else tool_idxs[0]
        )
        start = start_tool
        # Include the assistant that issued the tool_calls for that tool result
        if start > 0 and messages[start - 1].get("role") == "assistant":
            start -= 1
        # Never drop the final user turn if it sits before a short tail
        for j in range(start, -1, -1):
            if messages[j].get("role") == "user":
                # Keep at most one user message immediately before the tool block
                if j >= start - 2:
                    start = j
                break
        tail = messages[start:]
    else:
        tail = messages[-8:]

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
    # Avoid duplicating head messages that also appear in tail
    head_ids = {id(m) for m in head}
    tail_clean = [m for m in tail if id(m) not in head_ids]
    return head + [mid] + tail_clean


def _try_heuristic_brief_fallback(
    runtime: Any,
    messages: list[dict[str, Any]],
    *,
    user_text: str = "",
) -> None:
    """When local brief queue is unavailable: enrich brief from history (no paid call)."""
    brief = getattr(runtime, "_session_brief", None)
    if brief is None:
        return
    from remedy.memory.harness.compressor import heuristic_merge_from_history

    heuristic_merge_from_history(brief, messages, intent_hint=user_text)
    brief.append_history_thread(
        f"Heuristic refresh (local brief queue unavailable). "
        f"Focus: {(user_text or '')[:180]}",
        decisions_why=list(brief.decisions[-3:]),
        blockers=list(brief.blockers[-3:]),
    )
