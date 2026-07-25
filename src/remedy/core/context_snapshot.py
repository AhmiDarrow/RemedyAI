"""Single-pass context snapshot for a turn — continuity layer metabolism.

One walk of messages produces token estimate, fill %, compress nudge,
intent label, policy pack, quality remedies, and optional brief touch.
Replaces scattered Token + Memory + quality double-work on the hot path.
"""

from __future__ import annotations

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
    """One-pass continuity snapshot (deterministic; no network)."""
    from remedy.core.intent_policy import policy_for_intent
    from remedy.core.project_learning import load_project_profile, suggest_harness_pct
    from remedy.core.quality_remedies import remedies_from_quality
    from remedy.core.session_quality import get_session_quality
    from remedy.nanoswarm.router_nanobot import RouterNanobot
    from remedy.nanoswarm.token_nanobot import get_token_nanobot

    msgs = list(messages or [])
    snap = ContextSnapshot()

    # Project-level harness tuning (compound learning)
    min_use, max_use = min_pct, max_pct
    if project_path:
        try:
            prof = load_project_profile(project_path)
            min_use, max_use = suggest_harness_pct(prof, min_pct, max_pct)
            snap.signals["project_profile"] = prof.get("id")
        except Exception:
            pass

    token = get_token_nanobot()
    est = token.measure_messages(msgs, provider=provider, model=model)
    fill = token.fill_pct(est, context_window=context_window)
    nudge = token.should_nudge_compress(
        est,
        context_window=context_window,
        min_pct=min_use,
        max_pct=max_use,
    )
    snap.token_estimate = est
    snap.fill_pct = fill
    snap.nudge = nudge
    snap.estimate_method = token.last_method

    # Intent → policy pack (cheap heuristic)
    router = RouterNanobot()
    intent_out = router.classify_intent(user_text or "")
    intent = str(intent_out.get("label") or "chat")
    snap.intent = intent
    pack = policy_for_intent(intent, user_text=user_text or "")
    snap.policy_id = pack.get("id") or intent
    from remedy.core.intent_policy import format_policy_block

    snap.policy_system = format_policy_block(pack)
    snap.signals["policy"] = {
        "id": snap.policy_id,
        "suggest_tools": pack.get("suggest_tools") or [],
    }

    # Brief touch + optional proactive merge (same pass as memory nanobot)
    if apply_brief_touch and brief is not None and (user_text or msgs):
        try:
            import re

            from remedy.memory.harness.compressor import (
                extract_paths_from_text,
                heuristic_merge_from_history,
            )

            content = user_text or ""
            for p in extract_paths_from_text(content):
                brief.add_artifact(p)
            for m in re.finditer(
                r"(?:decided to|deciding to|will use|choosing|strategy:)\s+([^.!?\n]{3,120})",
                content,
                re.I,
            ):
                d = m.group(1).strip()
                if d and d not in (brief.decisions or []):
                    brief.decisions = list(brief.decisions or []) + [d]
                    if len(brief.decisions) > 12:
                        brief.decisions = brief.decisions[-12:]
            snap.brief_touched = True
            if nudge:
                heuristic_merge_from_history(brief, msgs, intent_hint=user_text or None)
                snap.proactive_merge = True
        except Exception as e:
            snap.signals["brief_error"] = str(e)

    # Quality control loop → silent system remedies
    if apply_remedies:
        try:
            q = get_session_quality(session_id).snapshot()
            rem = remedies_from_quality(q, fill_pct=fill, nudge=nudge)
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

    # Keep swarm status fields in sync (no nested locks — avoid hot-path stalls)
    try:
        import time as _time

        from remedy.nanoswarm import get_swarm

        swarm = get_swarm()
        swarm._event_count += 1  # noqa: SLF001 — status counters only
        swarm._last_event = "message_added"  # noqa: SLF001
        swarm._last_ts = _time.time()  # noqa: SLF001
        swarm.memory.last_fill_pct = fill
        swarm.memory.last_nudge = nudge
        if snap.brief_touched:
            swarm.memory.updates += 1
        swarm.router.last_label = intent
        swarm.router.last_method = "heuristic"
    except Exception:
        pass

    snap.signals["min_pct"] = min_use
    snap.signals["max_pct"] = max_use
    return snap
