"""Skill nanobot — feedback, ranking cache, library install suggestions."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class SkillNanobot:
    def __init__(self) -> None:
        self.feedback_count = 0
        self.last_decision: dict[str, Any] | None = None
        self._rank_cache: list[str] = []
        self.last_library_suggest: dict[str, Any] | None = None
        self.library_suggest_count = 0
        self._library_prefetch: dict[str, Any] | None = None

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
        # Always track duration on skill metadata when object is available (rank cost signal)
        if skill is not None and duration_ms > 0:
            try:
                m = skill.manifest
                meta = dict(m.metadata or {})
                prev = float(meta.get("avg_duration_ms") or duration_ms)
                meta["avg_duration_ms"] = round(0.7 * prev + 0.3 * float(duration_ms), 1)
                meta["last_duration_ms"] = float(duration_ms)
                m.metadata = meta
                out["duration_tracked"] = True
            except Exception:
                pass
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

    def suggest_library(
        self,
        user_text: str,
        *,
        intent: str = "chat",
        registry: Any | None = None,
        home: Path | str | None = None,
        session_id: str | None = None,
        cfg: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Soft library install tip (cache-only rank). Never installs."""
        from remedy.skills.library.suggest import (
            load_suggest_config,
            suggest_library_skill,
            system_hint_for,
        )

        scfg = load_suggest_config(cfg)
        if not scfg.get("enabled", True):
            return None

        installed: set[str] = set()
        top_score: float | None = None
        if registry is not None and hasattr(registry, "match_skills"):
            try:
                ranked = registry.match_skills(user_text or "", limit=5) or []
                for sk, _sc in ranked:
                    name = getattr(getattr(sk, "manifest", None), "name", None) or getattr(
                        sk, "name", None
                    )
                    if name:
                        installed.add(str(name))
                if ranked:
                    top_score = float(ranked[0][1])
                # Also collect all installed names for filter
                if hasattr(registry, "list_skills"):
                    for sk in registry.list_skills() or []:
                        n = getattr(getattr(sk, "manifest", None), "name", None)
                        if n:
                            installed.add(str(n))
                elif hasattr(registry, "_by_name"):
                    installed |= {str(n) for n in (registry._by_name or {})}
            except Exception:
                pass

        hit = suggest_library_skill(
            user_text or "",
            intent=intent,
            home=home,
            installed_names=installed,
            installed_top_score=top_score,
            session_id=session_id,
            min_score=float(scfg.get("min_score") or 0.35),
            min_chars=int(scfg.get("min_query_chars") or 24),
            installed_cover_threshold=float(
                scfg.get("installed_cover_threshold") or 0.55
            ),
            mark_suggested=True,
        )
        if hit is None:
            return None
        pub = hit.to_public()
        pub["system_hint"] = system_hint_for(hit)
        self.last_library_suggest = pub
        self.library_suggest_count += 1
        try:
            from remedy.core.metrics import default_registry

            default_registry.counter("remedy_library_suggest_total").inc()
        except Exception:
            pass
        return pub

    def prefetch_library(self, user_text: str, *, home: Path | str | None = None) -> None:
        """Warm rank for next turn (speculative; no suppress)."""
        try:
            from remedy.skills.library.suggest import rank_library_skills

            hits = rank_library_skills(
                user_text or "",
                home=home,
                limit=3,
                mark_suppressed=False,
            )
            if hits:
                self._library_prefetch = hits[0].to_public()
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        return {
            "bot": "skill",
            "feedback_count": self.feedback_count,
            "last_decision": self.last_decision,
            "cached_catalog_lines": len(self._rank_cache),
            "library_suggest_count": self.library_suggest_count,
            "last_library_suggest": self.last_library_suggest,
        }
