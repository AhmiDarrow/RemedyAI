"""TestClient-only: core skills HTTP routes (+ legacy summaries/handoffs).

Go ``remedy-runtime`` owns production ``:7400``; this registrar is for pytest.
Memory search/facts/wipe and skills extras (packs/metrics/export/…) are Go-owned.
"""
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from remedy.interfaces.api_models import SkillInfo

logger = logging.getLogger(__name__)


def register_memory_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # /api/memory/search|facts|add|persona-wipe live on Go httpapi only.

    # -- skills --------------------------------------------------------------
    def _skill_info(s) -> SkillInfo:
        meta = s.manifest.metadata or {}
        snap: dict[str, Any] = {}
        with suppress(Exception):
            if hasattr(runtime.skills, "health_snapshot"):
                snap = runtime.skills.health_snapshot(s.manifest.name) or {}
        rate = meta.get("success_rate")
        if rate is None and snap.get("success_rate") is not None:
            rate = snap.get("success_rate")
        try:
            rate_f = float(rate) if rate is not None else None
        except (TypeError, ValueError):
            rate_f = None
        return SkillInfo(
            name=s.manifest.name,
            description=s.manifest.description,
            version=s.manifest.version,
            kind=s.manifest.kind.value if hasattr(s.manifest.kind, "value") else str(s.manifest.kind),
            status=s.manifest.status.value if hasattr(s.manifest.status, "value") else str(s.manifest.status),
            tags=list(s.manifest.tags or []),
            effort_weight=float(meta.get("effort_weight") or snap.get("effort_weight") or 0.0),
            effort_band=meta.get("effort_band") or snap.get("effort_band"),
            auto_generated=bool(meta.get("auto_generated") or snap.get("auto_generated")),
            quarantine=bool(meta.get("quarantine") or snap.get("quarantine")),
            success_rate=rate_f,
            path=s.manifest.path or s.source_skill_dir or snap.get("path"),
            related=list(snap.get("related") or []),
            activations_session=int(snap.get("activations_session") or 0),
            lifecycle=meta.get("lifecycle") or snap.get("lifecycle"),
        )

    @app.get("/api/skills", response_model=list[SkillInfo])
    async def list_skills(q: str = Query(default=""), limit: int = Query(default=200, le=500)):
        if runtime is None or not hasattr(runtime, "skills"):
            return []
        reg = runtime.skills
        if q and hasattr(reg, "match_skills"):
            ranked = reg.match_skills(q, limit=limit, include_disabled=True)
            skills = [s for s, _ in ranked]
        else:
            skills = list(reg.skills)[:limit]
        return [_skill_info(s) for s in skills]

    # skills metrics/learning/packs/export/import/archive live on Go httpapi only.
    # Keep these path segments from being swallowed by /api/skills/{name}.
    _GO_SKILL_SEGMENTS = frozenset(
        {
            "packs",
            "metrics",
            "learning",
            "export",
            "import",
            "archive-unused",
            "library",
        }
    )

    def _skill_home() -> Path:
        return Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()

    def _reject_go_skill_segment(name: str) -> None:
        if name in _GO_SKILL_SEGMENTS:
            raise HTTPException(404, "Not Found")

    @app.get("/api/skills/{name}")
    async def get_skill_detail(name: str):
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        skill = runtime.skills.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")
        info = _skill_info(skill)
        body = None
        if hasattr(runtime.skills, "skill_body"):
            body = runtime.skills.skill_body(name)
        return {
            **info.model_dump(),
            "instructions_preview": (skill.instructions or "")[:2000],
            "body": body,
            "scripts": list(skill.scripts or []),
            "references": list(skill.references or []),
        }

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str, purge: bool = Query(default=True)):
        """Remove a user-installed skill from the registry and (by default) disk.

        The Skills panel ✗ control is *feedback*, not delete — this endpoint is
        the real remove path for library installs, imports, and learned skills.

        Safety: only deletes directories under ``~/.remedy/skills/``. Bundled package
        skills (shipped under the Remedy install) cannot be deleted this way.
        """
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        reg = runtime.skills
        skill = reg.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")

        import shutil

        from remedy.bundled_skills import bundled_skills_dir
        from remedy.skills.library.security import is_safe_skill_name

        if not is_safe_skill_name(name):
            raise HTTPException(400, "Invalid skill name")

        home = _skill_home()
        user_skills = (home / "skills").resolve()
        # Prefer canonical user-skills path by name (prevents metadata path tricks)
        canonical = (user_skills / name).resolve()
        try:
            canonical.relative_to(user_skills)
        except ValueError as e:
            raise HTTPException(400, "Refusing delete: path escapes user skills dir") from e

        meta = dict(skill.manifest.metadata or {})
        raw_path = (
            getattr(skill, "source_skill_dir", None)
            or skill.manifest.path
            or meta.get("skill_path")
            or ""
        )
        skill_path = Path(str(raw_path)).expanduser() if raw_path else canonical
        try:
            skill_path = skill_path.resolve()
        except OSError:
            skill_path = canonical

        # Never allow deleting outside the user skills tree
        try:
            skill_path.relative_to(user_skills)
            under_user = True
        except ValueError:
            under_user = False

        bundled_root = bundled_skills_dir().resolve()
        try:
            skill_path.relative_to(bundled_root)
            is_bundled = True
        except ValueError:
            is_bundled = False

        if is_bundled or not under_user:
            raise HTTPException(
                400,
                "Cannot delete bundled or non-user skills. "
                "Archive or quarantine them instead. "
                "Only skills under ~/.remedy/skills/ can be removed.",
            )

        target = skill_path if skill_path.is_dir() else skill_path.parent
        try:
            target = target.resolve()
            target.relative_to(user_skills)
        except ValueError as e:
            raise HTTPException(400, "Refusing delete: path escapes user skills dir") from e
        # Basename must match skill name (no deleting a different skill dir)
        if target.name != name or target == user_skills:
            # Fall back to canonical path only when metadata path is wrong
            if canonical.is_dir() and canonical.name == name:
                target = canonical
            else:
                raise HTTPException(
                    400,
                    "Refusing delete: skill path does not match skill name.",
                )

        removed_files = False
        if purge and target.is_dir() and target != user_skills:
            shutil.rmtree(target, ignore_errors=True)
            removed_files = not target.exists()

        if hasattr(reg, "remove"):
            reg.remove(name)
        with suppress(Exception):
            from remedy.skills.shared import invalidate_shared_registry

            invalidate_shared_registry()

        return {
            "name": name,
            "status": "deleted",
            "removed_files": removed_files,
            "path": str(target),
        }

    def _persist_skill(skill) -> None:
        with suppress(Exception):
            from remedy.core.learning_loop import LearningLoop

            home = _skill_home()
            loop = LearningLoop(
                skills_dir=home / "skills", memory=None, registry=runtime.skills
            )
            loop._write_skill_md(skill)  # noqa: SLF001

    @app.post("/api/skills/{name}/status")
    async def set_skill_status(name: str, request: Request):
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        reg = runtime.skills
        skill = reg.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        status = (payload or {}).get("status")
        if not status:
            raise HTTPException(400, "status required")
        from remedy.models import SkillStatus as _SS

        try:
            st = _SS(str(status).strip().lower())
        except ValueError as e:
            raise HTTPException(400, f"invalid status: {status}") from e
        if hasattr(reg, "set_status"):
            reg.set_status(name, st)
        else:
            skill.manifest.status = st
        meta = dict(skill.manifest.metadata or {})
        # Human-in-the-loop force promote / clear quarantine
        if (payload or {}).get("force_promote") or st == _SS.ACTIVE:
            meta["lifecycle"] = "manual-promote"
            meta["lifecycle_last"] = "Manually force-promoted by user"
            meta["manual_override"] = "promote"
            meta["quarantine"] = False
        if "quarantine" in (payload or {}):
            meta["quarantine"] = bool(payload.get("quarantine"))
            if meta["quarantine"]:
                meta["lifecycle"] = "manual-quarantine"
                meta["lifecycle_last"] = "Manually quarantined by user"
                meta["manual_override"] = "quarantine"
                if st == _SS.ACTIVE:
                    st = _SS.DISABLED
                    if hasattr(reg, "set_status"):
                        reg.set_status(name, st)
                    else:
                        skill.manifest.status = st
        skill.manifest.metadata = meta
        _persist_skill(skill)
        with suppress(Exception):
            from remedy.skills.shared import invalidate_shared_registry

            invalidate_shared_registry()
        return {
            "name": name,
            "status": st.value,
            "quarantine": bool(meta.get("quarantine")),
            "lifecycle": meta.get("lifecycle"),
        }

    @app.post("/api/skills/{name}/quarantine")
    async def set_skill_quarantine(name: str, request: Request):
        """Toggle manual quarantine (blocks script activation until cleared)."""
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        skill = runtime.skills.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        on = bool((payload or {}).get("quarantine", True))
        meta = dict(skill.manifest.metadata or {})
        meta["quarantine"] = on
        meta["manual_override"] = "quarantine" if on else "clear-quarantine"
        meta["lifecycle_last"] = (
            "Manually quarantined by user" if on else "Quarantine cleared by user"
        )
        skill.manifest.metadata = meta
        if on:
            from remedy.models import SkillStatus as _SS

            skill.manifest.status = _SS.DISABLED
        _persist_skill(skill)
        with suppress(Exception):
            from remedy.skills.shared import invalidate_shared_registry

            invalidate_shared_registry()
        return {
            "name": name,
            "quarantine": on,
            "status": skill.manifest.status.value,
        }

    @app.put("/api/skills/{name}/body")
    async def update_skill_body(name: str, request: Request):
        """Replace skill instructions / full SKILL.md body (human editor)."""
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        skill = runtime.skills.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        body = (payload or {}).get("body")
        instructions = (payload or {}).get("instructions")
        if body is None and instructions is None:
            raise HTTPException(400, "body or instructions required")
        text = str(body if body is not None else instructions)
        # If full SKILL.md with frontmatter, peel instructions after ---
        if text.lstrip().startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                text = parts[2].lstrip("\n")
        skill.instructions = text
        meta = dict(skill.manifest.metadata or {})
        meta["manual_edit"] = True
        meta["lifecycle_last"] = "Instructions edited by user"
        skill.manifest.metadata = meta
        _persist_skill(skill)
        return {
            "name": name,
            "status": "saved",
            "chars": len(skill.instructions or ""),
        }

    @app.post("/api/skills/{name}/feedback")
    async def skill_feedback(name: str, request: Request):
        _reject_go_skill_segment(name)
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        skill = runtime.skills.get(name)
        if skill is None:
            raise HTTPException(404, f"Skill not found: {name}")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        success = bool((payload or {}).get("success", True))
        from remedy.core.learning_loop import LearningLoop

        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()
        loop = LearningLoop(
            skills_dir=home / "skills",
            memory=getattr(runtime, "memory", None),
            registry=runtime.skills,
        )
        fixes = loop.record_skill_feedback(name, success=success)
        changed = loop.auto_refine_skill(skill)
        return {
            "name": name,
            "success": success,
            "status": skill.manifest.status.value,
            "refined": changed,
            "suggestions": fixes,
            "decision": (
                loop.last_lifecycle_decision.reason
                if loop.last_lifecycle_decision
                else None
            ),
        }

    # skills export/import/archive-unused live on Go httpapi only.

    # Generic CI webhook POST /api/webhook/{source} lives on Go httpapi only.

    # -- legacy session summaries  -------------------------------------------
    @app.get("/api/session-summaries")
    async def list_session_summaries(limit: int = Query(default=10, le=50)):
        if memory is None:
            return {"sessions": []}
        summaries = await memory.list_sessions(limit=limit)
        return {
            "sessions": [
                {
                    "session_id": s.session_id,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "tasks_completed": s.tasks_completed,
                    "skills_created": s.skills_created,
                    "summary": s.summary,
                }
                for s in summaries
            ]
        }

    # -- handoffs  -----------------------------------------------------------
    @app.get("/api/handoffs")
    async def list_handoffs(limit: int = Query(default=10, le=50)):
        if memory is None:
            return {"handoffs": []}
        handoffs = await memory.list_handoffs(limit=limit)
        return {
            "handoffs": [
                {
                    "id": str(h.id),
                    "title": h.title,
                    "content": h.content[:200],
                    "acknowledged": h.acknowledged,
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                }
                for h in handoffs
            ]
        }

