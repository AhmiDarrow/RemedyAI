"""Skill nanobot — ensure feedback + lifecycle refine; effort ranking cache."""

from __future__ import annotations

from typing import Any


class SkillNanobot:
    def __init__(self) -> None:
        self.feedback_count = 0
        self.last_decision: dict[str, Any] | None = None
        self._rank_cache: list[str] = []

    def on_skill_result(
        self,
        skill_name: str,
        *,
        success: bool,
        learning_loop: Any | None = None,
        duration_ms: float = 0.0,
        skill: Any | None = None,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {"bot": "skill", "skill": skill_name, "success": success}
        if learning_loop is None:
            out["skipped"] = "no_learning_loop"
            return out
        try:
            learning_loop.record_skill_feedback(
                skill_name,
                success=success,
                duration_ms=duration_ms,
            )
            self.feedback_count += 1
            out["feedback_recorded"] = True
            if skill is not None and hasattr(learning_loop, "auto_refine_skill"):
                changed = learning_loop.auto_refine_skill(skill)
                out["refined"] = bool(changed)
                dec = getattr(learning_loop, "last_lifecycle_decision", None) or getattr(
                    learning_loop, "_last_decision", None
                )
                if dec is not None:
                    self.last_decision = {
                        "action": getattr(dec, "action", None),
                        "reasoning": getattr(dec, "reasoning", None),
                    }
                    out["decision"] = self.last_decision
        except Exception as e:
            out["error"] = str(e)
        return out

    def rank_catalog_lines(self, registry: Any, *, limit: int = 40) -> list[str]:
        """Cache effort-aware summary lines when registry supports match/summary."""
        try:
            if hasattr(registry, "summary_lines"):
                lines = list(registry.summary_lines(limit=limit) or [])
            elif hasattr(registry, "match_skills"):
                skills = registry.match_skills("", limit=limit) or []
                lines = [
                    f"- {getattr(s, 'name', s)}: {getattr(getattr(s, 'manifest', None), 'description', '')[:80]}"
                    for s in skills
                ]
            else:
                lines = []
            self._rank_cache = lines
            return lines
        except Exception:
            return list(self._rank_cache)

    def status(self) -> dict[str, Any]:
        return {
            "bot": "skill",
            "feedback_count": self.feedback_count,
            "last_decision": self.last_decision,
            "cached_catalog_lines": len(self._rank_cache),
        }
