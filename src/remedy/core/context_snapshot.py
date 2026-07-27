"""Single-pass context snapshot for a turn — continuity layer metabolism.

One walk of messages produces token estimate, fill %, compress nudge,
intent label, policy pack, quality remedies, and optional brief touch.
Replaces scattered Token + Memory + quality double-work on the hot path.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextSnapshot:
    """Immutable-ish result of one continuity pass."""

    token_estimate: int = 0
    fill_pct: float = 0.0
    nudge: str | None = None  # soft | strong | None
    estimate_method: str = "heuristic"
    intent: str = "chat"
    policy_system: str = ""
    policy_id: str = "chat"
    remedy_system: str = ""
    brief_touched: bool = False
    proactive_merge: bool = False
    signals: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "token_estimate": self.token_estimate,
            "fill_pct": round(self.fill_pct, 4),
            "nudge": self.nudge,
            "estimate_method": self.estimate_method,
            "intent": self.intent,
            "policy_id": self.policy_id,
            "brief_touched": self.brief_touched,
            "proactive_merge": self.proactive_merge,
            "has_policy": bool(self.policy_system),
            "has_remedy": bool(self.remedy_system),
        }


def build_context_snapshot(
    *,
    messages: list[dict[str, Any]] | None,
    user_text: str = "",
    brief: Any | None = None,
    session_id: str = "",
    provider: str | None = None,
    model: str | None = None,
    context_window: int = 200_000,
    min_pct: float = 0.75,
    max_pct: float = 0.92,
    project_path: str | None = None,
    apply_brief_touch: bool = True,
    apply_remedies: bool = True,
) -> ContextSnapshot:
    """One-pass continuity snapshot (deterministic; no network).

    Uses the shared nano swarm bots (token, router, memory, pattern, skill)
    so status and remedies stay coherent across turns.
    """
    from remedy.core.intent_policy import format_policy_block, policy_for_intent
    from remedy.core.project_learning import load_project_profile, suggest_harness_pct
    from remedy.core.quality_remedies import remedies_from_quality
    from remedy.core.session_quality import get_session_quality
    from remedy.nanoswarm import get_swarm

    msgs = list(messages or [])
    snap = ContextSnapshot()
    swarm = get_swarm()

    # Project-level harness tuning (compound learning)
    min_use, max_use = min_pct, max_pct
    if project_path:
        try:
            prof = load_project_profile(project_path)
            min_use, max_use = suggest_harness_pct(prof, min_pct, max_pct)
            snap.signals["project_profile"] = prof.get("id")
        except Exception:
            pass

    # Token + memory nanobots (shared instances)
    mem_sig = swarm.memory.on_message(
        user_text or "",
        brief=brief if apply_brief_touch else None,
        messages=msgs,
        context_window=context_window,
        min_pct=min_use,
        max_pct=max_use,
        provider=provider,
        model=model,
    )
    snap.token_estimate = int(mem_sig.get("token_estimate") or 0)
    snap.fill_pct = float(mem_sig.get("fill_pct") or 0.0)
    nudge_val = mem_sig.get("nudge")
    snap.nudge = str(nudge_val) if nudge_val else None
    snap.estimate_method = str(mem_sig.get("estimate_method") or "heuristic")
    snap.brief_touched = bool(mem_sig.get("brief_touched"))
    snap.proactive_merge = bool(mem_sig.get("proactive_merge"))
    if mem_sig.get("brief_error"):
        snap.signals["brief_error"] = mem_sig["brief_error"]
    est = snap.token_estimate
    fill = snap.fill_pct
    nudge = snap.nudge

    # Intent → policy (shared router — not a fresh instance per turn)
    intent_out = swarm.router.classify_intent(user_text or "")
    intent = str(intent_out.get("label") or "chat")
    snap.intent = intent
    pack = policy_for_intent(intent, user_text=user_text or "")
    snap.policy_id = pack.get("id") or intent
    snap.policy_system = format_policy_block(pack)
    # Skill intent: reuse pre-ranked catalog (warmed by speculative prep / prior turns)
    if intent == "skill":
        try:
            lines = list(getattr(swarm.skill, "_rank_cache", None) or [])
            if lines:
                snap.policy_system = (
                    (snap.policy_system + "\n" if snap.policy_system else "")
                    + "[Continuity] Top skills to consider:\n"
                    + "\n".join(lines[:8])
                )
                snap.signals["skill_catalog_lines"] = len(lines)
        except Exception as e:
            snap.signals["skill_rank_error"] = str(e)

    snap.signals["policy"] = {
        "id": snap.policy_id,
        "suggest_tools": pack.get("suggest_tools") or [],
        "router_method": intent_out.get("method"),
        "ambiguous": bool(intent_out.get("ambiguous")),
    }

    # Spread planner — HEURISTICS ONLY on the hot path.
    # Never block chat on local Qwen (use_local only when the model calls spread_run).
    try:
        from remedy.core.spread.planner import plan_spread

        spread_plan = plan_spread(
            user_text or "",
            intent=intent,
            project_path=project_path,
            use_local=False,
        )
        snap.signals["spread"] = spread_plan.to_public()
        if spread_plan.spread:
            hint = spread_plan.system_hint()
            if hint:
                snap.policy_system = (
                    (snap.policy_system + "\n" if snap.policy_system else "") + hint
                )
            # Bias tool suggestions toward spread when fan-out is useful
            tools = list(pack.get("suggest_tools") or [])
            if "spread_run" not in tools:
                tools = ["spread_run", *tools]
            snap.signals["policy"]["suggest_tools"] = tools
            try:
                from remedy.core.metrics import default_registry

                default_registry.counter("remedy_spread_plans_total").inc()
            except Exception:
                pass
    except Exception as e:
        snap.signals["spread_error"] = str(e)

    # Pattern nanobot window (per-session) → stuck recovery when tools fail
    pat_rate: float | None = None
    pat_n = 0
    recent: list[str] = []
    try:
        pat_snap = swarm.pattern.for_session(session_id).snapshot()
        pat_n = int(pat_snap.get("step_count") or 0)
        pat_rate = pat_snap.get("success_rate")
        recent = list(pat_snap.get("recent") or [])
        if pat_n:
            snap.signals["pattern"] = {
                "step_count": pat_n,
                "success_rate": pat_rate,
                "recent": recent,
                "session_id": session_id or "_default",
            }
    except Exception:
        pass

    # Quality control loop → silent system remedies
    if apply_remedies:
        try:
            q = get_session_quality(session_id).snapshot()
            rem = remedies_from_quality(
                q,
                fill_pct=fill,
                nudge=nudge,
                pattern_success_rate=pat_rate,
                pattern_step_count=pat_n,
                pattern_recent=recent,
            )
            snap.remedy_system = str(rem.get("system") or "")
            snap.signals["remedies"] = rem.get("actions") or []
        except Exception as e:
            snap.signals["remedy_error"] = str(e)

    # Session quality turn record (once)
    try:
        get_session_quality(session_id).record_turn(
            estimated_context=est,
            user_text=user_text or "",
        )
        if nudge:
            get_session_quality(session_id).record_nudge(nudge)
    except Exception:
        pass

    def _append_remedy(text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        if snap.remedy_system:
            snap.remedy_system = snap.remedy_system + "\n" + t
        else:
            snap.remedy_system = t

    # Pack nanobot — silent hint when context is heavy
    try:
        pack_out = swarm.pack.pack_for_turn(
            messages=msgs,
            brief=brief if apply_brief_touch else None,
            context_window=context_window,
            fill_pct=fill,
            pattern_recent=recent,
            intent=intent,
        )
        snap.signals["pack"] = {
            "keep_recent_tool_pairs": pack_out.get("keep_recent_tool_pairs"),
            "aggressive": pack_out.get("aggressive"),
            "pins": len(pack_out.get("pins") or []),
        }
        _append_remedy(str(pack_out.get("system_hint") or ""))
    except Exception as e:
        snap.signals["pack_error"] = str(e)

    # Scout — first-tool orientation for tool/plan work (+ warm cache if ready)
    try:
        # Kick warm early (async); reuse cache when already filled
        if project_path and intent in ("tool", "plan", "skill"):
            with suppress(Exception):
                swarm.scout.schedule_warm(project_path, user_text=user_text or "")
        scout_out = swarm.scout.scout(
            user_text or "",
            intent=intent,
            project_path=project_path,
        )
        snap.signals["scout"] = {
            "active": scout_out.get("active"),
            "suggest_tools": scout_out.get("suggest_tools") or [],
            "warm": scout_out.get("warm"),
        }
        if scout_out.get("active"):
            _append_remedy(str(scout_out.get("system_hint") or ""))
            # Merge scout tools into policy signal
            st = list(snap.signals.get("policy", {}).get("suggest_tools") or [])
            for t in scout_out.get("suggest_tools") or []:
                if t not in st:
                    st.append(t)
            if "policy" in snap.signals:
                snap.signals["policy"]["suggest_tools"] = st[:12]
    except Exception as e:
        snap.signals["scout_error"] = str(e)

    # Goal — open checklist stale detection
    try:
        swarm.goal.sync_from_brief(brief if apply_brief_touch else None, session_id=session_id)
        gsnap = swarm.goal.snapshot(session_id)
        snap.signals["goal"] = gsnap
        _append_remedy(swarm.goal.system_hint(session_id))
    except Exception as e:
        snap.signals["goal_error"] = str(e)

    # Health — flaky provider note (no network probe)
    try:
        h = swarm.health.snapshot(provider=provider, model=model)
        snap.signals["health"] = {
            "error_rate": h.get("error_rate"),
            "rate_limit_hits": h.get("rate_limit_hits"),
            "avg_latency_ms": h.get("avg_latency_ms"),
            "flaky": h.get("flaky"),
            "samples": h.get("samples"),
        }
        if h.get("flaky"):
            _append_remedy(str(h.get("system_hint") or ""))
    except Exception as e:
        snap.signals["health_error"] = str(e)

    # Swarm status counters (shared coordinator — locked API, no private races)
    with suppress(Exception):
        swarm.note_event("message_added")

    snap.signals["min_pct"] = min_use
    snap.signals["max_pct"] = max_use
    return snap
