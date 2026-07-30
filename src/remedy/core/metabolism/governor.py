"""Closed-loop Quality Governor — observe → decide → act → score.

Silent control system. Advanced metrics only in public snapshots.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GovernorDecision:
    ts: float
    actions: list[str]
    reasons: list[str]

    def to_public(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "actions": list(self.actions),
            "reasons": list(self.reasons),
        }


@dataclass
class QualityGovernor:
    session_id: str = ""
    decisions: list[GovernorDecision] = field(default_factory=list)
    last_actions: list[str] = field(default_factory=list)
    # Control knobs (applied as silent hints / flags)
    compress_earlier: bool = False
    force_spread: bool = False
    shadow_strict: bool = False
    verify_next: bool = False
    skill_boost: str = ""
    tier_clamp: int | None = None  # max tier allowed
    loop_count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def observe_and_decide(
        self,
        *,
        quality: dict[str, Any] | None = None,
        metabolism: dict[str, Any] | None = None,
        tier: int = 1,
    ) -> GovernorDecision:
        """Produce control actions from metrics. Never network."""
        q = quality or {}
        m = metabolism or {}
        actions: list[str] = []
        reasons: list[str] = []

        stuck = float(q.get("stuck_rate") or 0)
        re_ex = float(q.get("re_explain_rate") or 0)
        waste = float(m.get("waste_batch_rate") or 0)
        eu = int(m.get("evidence_units") or 0)
        du = int(m.get("decision_units") or 0)
        fail_streak = int(q.get("max_tool_fail_streak") or 0)

        if stuck >= 0.15 or fail_streak >= 3:
            actions.append("recovery_remedy")
            actions.append("compress_earlier")
            reasons.append("stuck_or_fail_streak")
        if re_ex >= 0.1:
            actions.append("pin_constraints")
            reasons.append("re_explain")
        if waste >= 0.4 and tier >= 2:
            actions.append("delta_context_strict")
            actions.append("discourage_reread")
            reasons.append("high_waste")
        if eu == 0 and du == 0 and tier >= 2 and int(q.get("turns") or 0) > 2:
            actions.append("prefer_tools")
            reasons.append("no_eu_du")
        if tier >= 3 or m.get("force_spread_signal"):
            actions.append("force_spread")
            reasons.append("deep_or_partition")
        if fail_streak >= 2 and tier >= 2:
            actions.append("shadow_strict")
            reasons.append("recent_fails")
        if m.get("plan_step_done_claim") or m.get("tests_green_claim"):
            actions.append("critical_verify")
            reasons.append("judgment_point")

        dec = GovernorDecision(ts=time.time(), actions=actions, reasons=reasons)
        with self._lock:
            self.loop_count += 1
            # Skip list append + flag rewrites when control actions are unchanged
            # (common on quiet L1 chat — avoids thrash every turn).
            if actions == self.last_actions and self.decisions:
                return self.decisions[-1]
            self.decisions.append(dec)
            if len(self.decisions) > 40:
                self.decisions = self.decisions[-40:]
            self.last_actions = list(actions)
            self.compress_earlier = "compress_earlier" in actions
            self.force_spread = "force_spread" in actions
            self.shadow_strict = "shadow_strict" in actions
            self.verify_next = "critical_verify" in actions
        return dec

    def system_notes(self) -> str:
        with self._lock:
            acts = list(self.last_actions)
        if not acts:
            return ""
        notes: list[str] = []
        if "recovery_remedy" in acts:
            notes.append(
                "[Governor] Stuck signals — stop looping the same tool; "
                "re-read one error path, change approach, or verify."
            )
        if "compress_earlier" in acts:
            notes.append(
                "[Governor] Prefer lean context; collapse completed tool spans."
            )
        if "delta_context_strict" in acts or "discourage_reread" in acts:
            notes.append(
                "[Governor] High waste — do not re-read known paths; use evidence delta."
            )
        if "prefer_tools" in acts:
            notes.append("[Governor] Prefer tools over monologue for this turn.")
        if "force_spread" in acts:
            notes.append(
                "[Governor] Partitionable work — use spread_run for independent tasks."
            )
        if "pin_constraints" in acts:
            notes.append(
                "[Governor] User re-explained — honor stated constraints; do not drift."
            )
        if "critical_verify" in acts:
            notes.append(
                "[Governor] Judgment point — verify claims (tests/plan done) before asserting success."
            )
        return "\n".join(notes)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "loop_count": self.loop_count,
                "last_actions": list(self.last_actions),
                "compress_earlier": self.compress_earlier,
                "force_spread": self.force_spread,
                "shadow_strict": self.shadow_strict,
                "verify_next": self.verify_next,
                "recent": [d.to_public() for d in self.decisions[-6:]],
            }


_govs: dict[str, QualityGovernor] = {}
_lock = threading.Lock()


def get_governor(session_id: str | None = None) -> QualityGovernor:
    key = (session_id or "").strip() or "_default"
    with _lock:
        if key not in _govs:
            _govs[key] = QualityGovernor(session_id=key)
        return _govs[key]


def reset_governor(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _lock:
        _govs.pop(key, None)
