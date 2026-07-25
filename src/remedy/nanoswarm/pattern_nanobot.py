"""Pattern nanobot — tool sequence windows + learn pre-gate signals."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PatternNanobot:
    """Sliding window of tool steps; reuses lifecycle policy for gates."""

    window: int = 12
    steps: list[dict[str, Any]] = field(default_factory=list)

    def on_tool_step(
        self,
        tool_name: str,
        *,
        success: bool = True,
        duration_ms: float = 0.0,
        **extra: Any,
    ) -> dict[str, Any]:
        self.steps.append(
            {
                "tool_name": tool_name,
                "success": success,
                "duration_ms": duration_ms,
                **extra,
            }
        )
        if len(self.steps) > self.window * 2:
            self.steps = self.steps[-self.window * 2 :]
        recent = [s["tool_name"] for s in self.steps[-self.window :]]
        return {
            "sequence": recent,
            "step_count": len(self.steps),
            "success_rate": (
                sum(1 for s in self.steps if s.get("success")) / max(1, len(self.steps))
            ),
        }

    def pregate_trace(
        self,
        *,
        overall_success: bool = True,
        title: str = "",
    ) -> dict[str, Any]:
        """Run SkillLifecyclePolicy.should_accept_trace on buffered steps."""
        try:
            from remedy.core.learning.lifecycle import (
                SkillLifecyclePolicy,
                compute_effort_score,
            )
            from remedy.core.learning.reflection import TraceStep

            steps = [
                TraceStep(
                    index=i,
                    tool_name=str(s.get("tool_name") or f"step_{i}"),
                    arguments={},
                    result_summary="",
                    success=bool(s.get("success", True)),
                    duration_ms=float(s.get("duration_ms") or 0),
                )
                for i, s in enumerate(self.steps)
            ]
            effort = compute_effort_score(steps=steps)
            ok = sum(1 for s in steps if s.success)
            rate = ok / max(1, len(steps))
            # light pattern signal: repeated tool pairs
            names = [s.tool_name for s in steps]
            has_pattern = len(names) >= 3 and len(set(names)) < len(names)
            decision = SkillLifecyclePolicy().should_accept_trace(
                step_count=len(steps),
                successful_steps=ok,
                overall_success=overall_success,
                step_success_rate=rate,
                has_reusable_pattern=has_pattern,
                title=title,
                effort=effort,
            )
            return {
                "action": decision.action,
                "reasoning": decision.reasoning,
                "confidence": decision.confidence,
                "effort": effort.score,
                "pre_approved": decision.action == "accept",
                "skip_learn": decision.action in ("reject", "skip"),
            }
        except Exception as e:
            return {"action": "unknown", "error": str(e), "skip_learn": False}

    def status(self) -> dict[str, Any]:
        return {
            "bot": "pattern",
            "buffered_steps": len(self.steps),
            "recent": [s.get("tool_name") for s in self.steps[-5:]],
        }
