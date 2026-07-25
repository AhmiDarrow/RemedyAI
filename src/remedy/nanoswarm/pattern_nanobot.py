"""Pattern nanobot — tool sequence windows + learn pre-gate signals.

Buffers are **per-session** so multi-tab work does not contaminate stuck
signals or learn pre-gates across unrelated chats.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _SessionPattern:
    """Sliding window of tool steps for one session."""

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

    def snapshot(self) -> dict[str, Any]:
        n = len(self.steps)
        rate = (
            sum(1 for s in self.steps if s.get("success")) / max(1, n) if n else None
        )
        return {
            "step_count": n,
            "success_rate": round(rate, 3) if rate is not None else None,
            "recent": [str(s.get("tool_name") or "") for s in self.steps[-6:]],
        }


class PatternNanobot:
    """Session-keyed pattern windows; reuses lifecycle policy for gates."""

    def __init__(self, window: int = 12) -> None:
        self.window = window
        self._sessions: dict[str, _SessionPattern] = {}
        self._lock = threading.Lock()

    def _key(self, session_id: str | None) -> str:
        return (session_id or "").strip() or "_default"

    def for_session(self, session_id: str | None = None) -> _SessionPattern:
        key = self._key(session_id)
        with self._lock:
            if key not in self._sessions:
                self._sessions[key] = _SessionPattern(window=self.window)
            return self._sessions[key]

    def on_tool_step(
        self,
        tool_name: str,
        *,
        success: bool = True,
        duration_ms: float = 0.0,
        session_id: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        out = self.for_session(session_id).on_tool_step(
            tool_name,
            success=success,
            duration_ms=duration_ms,
            **extra,
        )
        out["session_id"] = self._key(session_id)
        return out

    def pregate_trace(
        self,
        *,
        overall_success: bool = True,
        title: str = "",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        out = self.for_session(session_id).pregate_trace(
            overall_success=overall_success,
            title=title,
        )
        out["session_id"] = self._key(session_id)
        return out

    def clear_session(self, session_id: str | None = None) -> None:
        key = self._key(session_id)
        with self._lock:
            self._sessions.pop(key, None)

    # Back-compat: expose aggregate steps for status / single-session agents
    @property
    def steps(self) -> list[dict[str, Any]]:
        """Default-session steps (legacy attribute used by ContextSnapshot)."""
        return self.for_session(None).steps

    def status(self) -> dict[str, Any]:
        with self._lock:
            keys = list(self._sessions.keys())
            total = sum(len(s.steps) for s in self._sessions.values())
            default = self._sessions.get("_default")
            recent = (
                [s.get("tool_name") for s in default.steps[-5:]] if default else []
            )
        return {
            "bot": "pattern",
            "session_count": len(keys),
            "buffered_steps": total,
            "recent": recent,
            "sessions": keys[:20],
        }
