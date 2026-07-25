"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from remedy.interfaces.api_models import (
    MemoryAddRequest,
    SkillInfo,
    WebhookPayload,
)
from remedy.models import (
    ChannelKind,
    EventKind,
    GatewayEvent,
    MemoryEntryType,
)

logger = logging.getLogger(__name__)


def register_memory_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- memory search -------------------------------------------------------
    @app.get("/api/memory/search")
    async def search_memory(query: str = Query(...), limit: int = Query(default=10, le=50)):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        entries = await memory.search(query, limit=limit)
        return {
            "query": query,
            "results": [
                {
                    "id": str(e.id),
                    "title": e.title,
                    "content": e.content[:300],
                    "type": e.entry_type.value,
                    "importance": e.importance,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in entries
            ],
        }

    # -- memory add ----------------------------------------------------------
    @app.post("/api/memory/add")
    async def add_memory(req: MemoryAddRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        from remedy.models import MemoryEntry
        entry = MemoryEntry(
            title=req.title,
            content=req.content,
            entry_type=MemoryEntryType.NOTE,
            tags=req.tags,
            importance=req.importance,
        )
        saved = await memory.upsert(entry)
        return {"id": str(saved.id), "title": saved.title, "status": "saved"}

    # -- skills --------------------------------------------------------------
    def _skill_info(s) -> SkillInfo:
        meta = s.manifest.metadata or {}
        snap = {}
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

    @app.get("/api/skills/metrics/reuse")
    async def skills_reuse_metrics():
        """Closed-loop skill re-use: activations vs executions (Phase B3)."""
        from pathlib import Path

        from remedy.core.learning_loop import LearningLoop

        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None)
            or "~/.remedy"
        ).expanduser()
        loop = LearningLoop(
            skills_dir=home / "skills",
            memory=None,
            stats_path=home / "skill_stats.json",
            registry=getattr(runtime, "skills", None) if runtime else None,
        )
        return loop.get_reuse_metrics()

    @app.get("/api/skills/learning/summary")
    async def skills_learning_summary(limit: int = Query(default=12, le=50)):
        """What did I learn? — probation/auto-generated skills + last lifecycle note.

        Personal-partner observability: surface the learning loop without making
        users dig through ~/.remedy.
        """
        if runtime is None or not hasattr(runtime, "skills"):
            return {
                "recent": [],
                "probation_count": 0,
                "learned_count": 0,
                "active_learned_count": 0,
                "note": "Skills not available",
            }
        reg = runtime.skills
        all_skills = list(getattr(reg, "skills", []) or [])
        learned: list[dict] = []
        probation = 0
        active_learned = 0
        for s in all_skills:
            meta = s.manifest.metadata or {}
            auto = bool(meta.get("auto_generated"))
            status_val = (
                s.manifest.status.value
                if hasattr(s.manifest.status, "value")
                else str(s.manifest.status)
            )
            if status_val in ("discovered", "validated"):
                probation += 1
            if not auto:
                continue
            if status_val == "active":
                active_learned += 1
            mtime = 0.0
            path = s.manifest.path or meta.get("skill_path") or ""
            if path:
                with suppress(OSError):
                    p = Path(path)
                    target = p if p.is_file() else (p / "SKILL.md" if p.is_dir() else p)
                    if target.exists():
                        mtime = target.stat().st_mtime
            info = _skill_info(s)
            learned.append(
                {
                    **info.model_dump(),
                    "lifecycle_last": meta.get("lifecycle_last")
                    or meta.get("creation_gate")
                    or meta.get("lifecycle"),
                    "mtime": mtime,
                }
            )
        learned.sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
        recent = learned[:limit]
        for row in recent:
            row.pop("mtime", None)
        return {
            "recent": recent,
            "probation_count": probation,
            "learned_count": len(learned),
            "active_learned_count": active_learned,
            "note": (
                "Learned skills start on probation and promote only after multi-session success."
                if learned
                else "No auto-learned skills yet — multi-step successful work can create them."
            ),
        }

    @app.get("/api/skills/{name}")
    async def get_skill_detail(name: str):
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

    def _skill_home() -> Path:
        return Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()

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
        return {
            "name": name,
            "status": st.value,
            "quarantine": bool(meta.get("quarantine")),
            "lifecycle": meta.get("lifecycle"),
        }

    @app.post("/api/skills/{name}/quarantine")
    async def set_skill_quarantine(name: str, request: Request):
        """Toggle manual quarantine (blocks script activation until cleared)."""
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
        return {
            "name": name,
            "quarantine": on,
            "status": skill.manifest.status.value,
        }

    @app.put("/api/skills/{name}/body")
    async def update_skill_body(name: str, request: Request):
        """Replace skill instructions / full SKILL.md body (human editor)."""
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

    @app.post("/api/skills/export")
    async def export_skills_pack(request: Request):
        """Export selected (or all) skills as a ZIP pack."""
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        names = list((payload or {}).get("names") or [])
        import tempfile

        from remedy.skills.exporter import SkillExporter

        tmp = Path(tempfile.mkdtemp(prefix="remedy-skill-pack-"))
        exp = SkillExporter(tmp)
        skills = []
        if names:
            for n in names:
                s = runtime.skills.get(n)
                if s:
                    skills.append(s)
        else:
            skills = list(runtime.skills.skills)
        if not skills:
            raise HTTPException(400, "No skills to export")
        zip_path = exp.export_pack(skills)
        return FileResponse(
            str(zip_path),
            filename=zip_path.name,
            media_type="application/zip",
        )

    @app.post("/api/skills/import")
    async def import_skills_pack(request: Request):
        """Import a skill pack ZIP into quarantine until user promotes."""
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        form = await request.form()
        upload = form.get("file")
        if upload is None:
            raise HTTPException(400, "file required")
        import tempfile

        from remedy.skills.exporter import SkillExporter

        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()
        dest_root = home / "skills"
        tmp = Path(tempfile.mkdtemp(prefix="remedy-skill-import-"))
        data = await upload.read()  # type: ignore[union-attr]
        zip_path = tmp / "pack.zip"
        zip_path.write_bytes(data)
        exp = SkillExporter(tmp)
        imported = exp.import_pack_quarantine(zip_path, dest_root)
        n = 0
        for skill in imported:
            runtime.skills.register(skill)
            n += 1
        return {
            "imported": n,
            "names": [s.manifest.name for s in imported],
            "quarantine": True,
        }

    # -- webhook -------------------------------------------------------------
    @app.post("/api/webhook/{source}")
    async def receive_webhook(source: str, payload: WebhookPayload, request: Request):
        if gateway is None:
            raise HTTPException(503, "Gateway not available")
        # Require API key or shared secret when agent auth is enabled
        import os as _os

        expected = (
            getattr(getattr(request.app, "state", None), "api_key", None)
            or _os.environ.get("REMEDY_API_KEY")
            or _os.environ.get("REMEDY_WEBHOOK_SECRET")
            or ""
        )
        if expected:
            auth = request.headers.get("Authorization", "")
            secret = request.headers.get("X-Remedy-Webhook-Secret", "")
            bearer_ok = auth == f"Bearer {expected}"
            secret_ok = secret == expected or secret == _os.environ.get(
                "REMEDY_WEBHOOK_SECRET", ""
            )
            if not (bearer_ok or secret_ok):
                raise HTTPException(401, "Webhook auth required")

        body = await request.body()
        event = GatewayEvent(
            kind=EventKind.WEBHOOK,
            channel=ChannelKind.API,
            source_id=source,
            payload={
                "source": source,
                "event": payload.event,
                "data": payload.data,
                "raw": body.decode("utf-8", errors="replace")[:1000],
            },
        )

        await gateway.enqueue(event)
        return {"status": "accepted", "source": source}

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

