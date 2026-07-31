"""Turn metabolism bridge — wire organs into one silent pre/post pass."""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from remedy.core.metabolism.decision import get_decision_tracker
from remedy.core.metabolism.evidence import get_evidence_ledger
from remedy.core.metabolism.governor import get_governor
from remedy.core.metabolism.machine_map import get_machine_map
from remedy.core.metabolism.tier import (
    TurnTier,
    classify_turn_tier,
    tier_policy,
    tier_system_block,
)
from remedy.core.metabolism.time_crystal import get_time_crystal

# CUA element refs in snapshot dumps (e1, w1, c1) — precompiled for tool hot path
_CUA_REF_RE = re.compile(r"\b[ewc]\d+\b", re.I)


def begin_turn_metabolism(
    *,
    session_id: str = "",
    user_text: str = "",
    intent: str = "chat",
    plan_mode: bool = False,
    has_attachments: bool = False,
    tools_enabled: bool = True,
    pure_action: bool = False,
    browse: bool = False,
    project_path: str = "",
    work_roots: list[str] | None = None,
    brief_head: str = "",
    pre_tier: int | None = None,
) -> dict[str, Any]:
    """Classify tier, warm map/ledger/governor, return policy + inject notes.

    When *pre_tier* is set (e.g. send_policy already classified this turn),
    reuse it and skip a second ``classify_turn_tier`` walk — unless intent is
    autonomous (can elevate to L3 beyond text-only pre-classification).
    """
    sid = (session_id or "").strip() or "_default"
    intent_l = (intent or "chat").strip().lower()
    # Reuse send_policy pre_tier (same turn) — avoid dual classify work.
    # Autonomous intent may raise L0–L2 → L3; re-walk only then.
    if pre_tier is not None and intent_l != "autonomous":
        try:
            tier = TurnTier(int(pre_tier))
        except (TypeError, ValueError):
            tier = classify_turn_tier(
                user_text,
                intent=intent,
                plan_mode=plan_mode,
                has_attachments=has_attachments,
                tools_enabled=tools_enabled,
                pure_action=pure_action,
                browse=browse,
            )
    else:
        tier = classify_turn_tier(
            user_text,
            intent=intent,
            plan_mode=plan_mode,
            has_attachments=has_attachments,
            tools_enabled=tools_enabled,
            pure_action=pure_action,
            browse=browse,
        )
    policy = tier_policy(tier)

    # Cheap accuracy metric — tier distribution (no labels overhead beyond key)
    with suppress(Exception):
        from remedy.core.metrics import default_registry

        default_registry.counter(
            "remedy_turn_tier_total", tier=policy.label
        ).inc()

    # L0: skip map/crystal/governor warm entirely (instant path).
    # Only touch ledger/decision counters for Advanced UI stubs.
    if int(tier) == 0:
        ledger = get_evidence_ledger(sid)
        decisions = get_decision_tracker(sid)
        with suppress(Exception):
            from remedy.core.session_quality import get_session_quality

            get_session_quality(sid).record_metabolism(tier=0)
        return {
            "session_id": sid,
            "tier": 0,
            "tier_label": policy.label,
            "policy": policy.to_public(),
            "force_spread": False,
            "full_snapshot": False,
            "shadow_high_blast": False,
            "record_ir": False,
            "allow_critical_verify": False,
            "injects": [],
            "action_ir": None,
            "governor": {},
            "evidence": {
                "session_id": sid,
                "evidence_units": ledger.evidence_units,
                "unit_count": 0,
                "waste_tokens": 0,
            },
            "decisions": {
                "session_id": sid,
                "decision_units": decisions.decision_units,
            },
            "machine_map": {},
            "time_crystal": {},
        }

    # L1+: warm organs used for injects / control
    ledger = get_evidence_ledger(sid)
    decisions = get_decision_tracker(sid)
    mmap = get_machine_map(sid)
    crystal = get_time_crystal(sid, project_id=project_path or "")
    gov = get_governor(sid)

    if work_roots:
        mmap.set_work_roots(list(work_roots))
    elif project_path:
        mmap.set_work_roots([project_path])

    # One SessionQuality handle for the whole begin_turn (no double registry lookup)
    sq = None
    with suppress(Exception):
        from remedy.core.session_quality import get_session_quality

        sq = get_session_quality(sid)

    # Session horizon: admit short intent line (no secrets path)
    if user_text and len(user_text) < 240:
        crystal.admit(user_text[:200], horizon="session", source="user")

    # Quality + metabolism for governor (L1+ only) — reuse sq handle
    quality: dict[str, Any] = {}
    if sq is not None:
        with suppress(Exception):
            quality = sq.snapshot()

    meta_snap = {
        "evidence_units": ledger.evidence_units,
        "decision_units": decisions.decision_units,
        # Cheap rate — avoid full snapshot/list copy on every turn
        "waste_batch_rate": decisions.waste_batch_rate(),
        "force_spread_signal": policy.force_spread,
    }
    gov.observe_and_decide(quality=quality, metabolism=meta_snap, tier=int(tier))

    injects: list[str] = []
    note = tier_system_block(tier)
    if note:
        injects.append(note)
    # Force-spread muscle (L3 / governor) — stronger than soft hint
    if policy.force_spread or gov.force_spread:
        injects.append(
            "[Spread · force] Work looks partitionable or deep. "
            "Call spread_run for independent modules/paths/URLs first, "
            "then synthesize one answer. Do not serial-loop list_dir."
        )
    gnote = gov.system_notes()
    if gnote:
        injects.append(gnote)
    map_hint = mmap.system_hint()
    if map_hint and int(tier) >= 2:
        injects.append(map_hint)
    crystal_block = crystal.hot_block(max_chars=800)
    if crystal_block and int(tier) >= 1:
        injects.append(crystal_block)
    # Evidence delta from prior tools (after first model call mark)
    eblock = ledger.pointer_block(limit=12)
    if eblock and int(tier) >= 2:
        injects.append(eblock)
    with suppress(Exception):
        from remedy.core.metabolism.cua_macros import get_cua_macros

        mh = get_cua_macros().top_hints(3)
        if mh and int(tier) >= 2:
            injects.append(mh)
    with suppress(Exception):
        from remedy.core.metabolism.skill_genome import get_skill_genome

        top = get_skill_genome().rank(5)
        if top and int(tier) >= 2:
            names = ", ".join(
                f"{t['skill_id']}({t['score']})" for t in top if t.get("skill_id")
            )
            if names:
                injects.append(
                    f"[Skill genome] Prefer proven skills when relevant: {names}"
                )

    ir = None
    if policy.record_ir:
        with suppress(Exception):
            from remedy.core.metabolism.action_ir import start_action_ir

            ir = start_action_ir(
                session_id=sid,
                tier=int(tier),
                brief_head=brief_head or user_text[:200],
            )

    # Only record when tier label changes — skip identical L1 every chat turn
    decisions.record_tier_if_changed(policy.label)

    # Session quality metabolism counters (same handle as governor snapshot)
    if sq is not None:
        with suppress(Exception):
            sq.record_metabolism(
                tier=int(tier),
                evidence_units=ledger.evidence_units,
                decision_units=decisions.decision_units,
                waste_tokens=ledger.waste_tokens,
                force_spread=bool(policy.force_spread or gov.force_spread),
            )

    # Hot path: skip expensive organ snapshots (use metabolism_public_snapshot for Advanced)
    return {
        "session_id": sid,
        "tier": int(tier),
        "tier_label": policy.label,
        "policy": policy.to_public(),
        "force_spread": bool(policy.force_spread or gov.force_spread),
        "full_snapshot": policy.full_snapshot or int(tier) >= 2,
        "shadow_high_blast": policy.shadow_high_blast or gov.shadow_strict,
        "record_ir": policy.record_ir,
        "allow_critical_verify": policy.allow_critical_verify or gov.verify_next,
        "injects": injects,
        "action_ir": ir,
    }


def after_tool_batch(
    *,
    session_id: str = "",
    tool_name: str = "",
    arguments: dict[str, Any] | None = None,
    content: str = "",
    tool_call_id: str = "",
    success: bool = True,
    tier: int = 2,
    action_ir: Any = None,
    shadow_outcome: str = "",
    offload_path: str | None = None,
) -> dict[str, Any]:
    """Admit evidence, update map, record IR step, decision waste score."""
    sid = (session_id or "").strip() or "_default"
    ledger = get_evidence_ledger(sid)
    decisions = get_decision_tracker(sid)
    mmap = get_machine_map(sid)

    admitted = ledger.admit_tool_result(
        tool_name=tool_name,
        content=content or "",
        tool_call_id=tool_call_id,
        offload_path=offload_path,
        success=success,
        # L0/L1: tight unit/path caps so lean chats do not grow agency ledgers
        lean=int(tier) <= 1,
    )
    decisions.record_tool_batch(new_eu=len(admitted))

    # Map updates from tool names
    name = tool_name or ""
    if name in ("file_write", "file_edit", "file_read") and arguments:
        p = str(arguments.get("path") or "")
        if p:
            mmap.note_file_touch(p)
            if name != "file_read":
                decisions.record("tool_write", f"{name}:{p[:120]}")
    if name == "computer_navigate" and arguments:
        url = str(arguments.get("url") or arguments.get("target") or "")
        mmap.note_browser(url=url, settled=False)
    if name in ("computer_snapshot", "computer_screenshot", "computer_act") and success:
        # Parse light ref counts from element dump (e1, w1, c1)
        refs = len(_CUA_REF_RE.findall(content or ""))
        url = ""
        if arguments:
            url = str(arguments.get("url") or "")
        mmap.note_browser(url=url, settled=True, ref_count=refs)
    if name == "computer_windows" and success:
        titles = [
            ln.strip()[:80]
            for ln in (content or "").splitlines()
            if ln.strip()
        ][:12]
        mmap.note_desktop_windows(len(titles), titles)

    if action_ir is not None and int(tier) >= 2:
        with suppress(Exception):
            action_ir.add_step(
                tool=name,
                arguments=arguments,
                result=content or "",
                eu_delta=len(admitted),
                shadow_outcome=shadow_outcome,
                ok=success,
            )

    # Throttle session_quality metabolism writes (every 3rd tool or on write).
    # Counter is per-session on the ledger — never a process-global attr.
    with suppress(Exception):
        from remedy.core.session_quality import get_session_quality

        with ledger._lock:
            ledger._tool_batch_n = int(getattr(ledger, "_tool_batch_n", 0) or 0) + 1
            n = ledger._tool_batch_n
        if n % 3 == 0 or name in ("file_write", "file_edit", "bash_exec"):
            get_session_quality(sid).record_metabolism(
                evidence_units=ledger.evidence_units,
                decision_units=decisions.decision_units,
                waste_tokens=ledger.waste_tokens,
                ir_steps=1 if action_ir is not None else 0,
            )

    return {
        "eu_new": len(admitted),
        "evidence_units": ledger.evidence_units,
        "waste_tokens": ledger.waste_tokens,
    }


def mark_model_call(session_id: str = "") -> None:
    get_evidence_ledger(session_id).mark_model_call()


def end_turn_metabolism(
    *,
    session_id: str = "",
    action_ir: Any = None,
    status: str = "done",
    assistant_text: str = "",
    recent_tool_texts: list[str] | None = None,
    allow_verify: bool = False,
    home: Any = None,
) -> dict[str, Any]:
    """Finish IR, optional critical verify, promote crystal, persist soft."""
    sid = (session_id or "").strip() or "_default"
    out: dict[str, Any] = {}
    if action_ir is not None:
        with suppress(Exception):
            action_ir.finish(status)
            action_ir.persist(home)
            out["ir"] = action_ir.to_public()

    crystal = get_time_crystal(sid)
    crystal.promote_session_to_project(min_hits=2)
    with suppress(Exception):
        crystal.persist(home)

    verify_result = None
    if allow_verify and (assistant_text or recent_tool_texts):
        with suppress(Exception):
            from remedy.core.metabolism.verify import verify_critical

            verify_result = verify_critical(
                assistant_text=assistant_text or "",
                recent_tool_texts=recent_tool_texts,
            )
            out["verify"] = verify_result.to_public()
            if verify_result.silent_remedy:
                out["verify_remedy"] = verify_result.silent_remedy
            get_governor(sid)
            if not verify_result.ok:
                with suppress(Exception):
                    from remedy.core.session_quality import get_session_quality

                    get_session_quality(sid).record_verify(caught=True)

    ledger = get_evidence_ledger(sid)
    with suppress(Exception):
        ledger.persist_index(home)

    # Lean counters only on end-turn hot path (full Advanced uses GET endpoint)
    out["metabolism"] = metabolism_public_snapshot(sid, lean=True)
    return out


def metabolism_public_snapshot(
    session_id: str | None = None,
    *,
    lean: bool = False,
) -> dict[str, Any]:
    """Aggregate Advanced/operator snapshot (calm fields only).

    *lean*: counters + flags only — no recent lists, no skill/CUA ranking sorts,
    no IR coverage scan. Use on every end-turn and partner-status polls.
    Full snapshot (default) for ``GET /api/partner/metabolism``.
    """
    sid = (session_id or "").strip() or "_default"
    skill_snap: dict = {}
    cua_snap: dict = {}
    ir_total = 0
    if not lean:
        with suppress(Exception):
            from remedy.core.metabolism.skill_genome import get_skill_genome

            skill_snap = get_skill_genome().snapshot(lean=False)
        with suppress(Exception):
            from remedy.core.metabolism.cua_macros import get_cua_macros

            cua_snap = get_cua_macros().snapshot(lean=False)
        with suppress(Exception):
            from remedy.core.metabolism.action_ir import ir_coverage_count

            ir_total = ir_coverage_count()
    else:
        # Count-only genome/macros (no sort) — still useful for Advanced badges
        with suppress(Exception):
            from remedy.core.metabolism.skill_genome import get_skill_genome

            skill_snap = get_skill_genome().snapshot(lean=True)
        with suppress(Exception):
            from remedy.core.metabolism.cua_macros import get_cua_macros

            cua_snap = get_cua_macros().snapshot(lean=True)
    return {
        "evidence": get_evidence_ledger(sid).snapshot(lean=lean),
        "decisions": get_decision_tracker(sid).snapshot(lean=lean),
        "machine_map": get_machine_map(sid).snapshot(lean=lean),
        "governor": get_governor(sid).snapshot(lean=lean),
        "time_crystal": get_time_crystal(sid).snapshot(lean=lean),
        "skill_genome": skill_snap,
        "cua_macros": cua_snap,
        "ir_coverage_total": ir_total,
        "lean": bool(lean),
    }
