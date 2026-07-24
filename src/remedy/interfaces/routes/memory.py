"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from remedy import __version__ as _remedy_version
from remedy.core.errors import SecurityError
from remedy.core.security import safe_path
from remedy.interfaces.api_models import (
    AttachmentRef,
    AttachmentUploadRequest,
    ChatRequest,
    ChatResponse,
    CommandRequest,
    CreateSessionRequest,
    MemoryAddRequest,
    MemorySearchRequest,
    SendMessageRequest,
    SettingsUpdateRequest,
    SkillInfo,
    StatusResponse,
    UpdateSessionRequest,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    _apply_llm_to_runtime,
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    _BUILTIN_MODELS,
    _default_config_path,
    _find_config_path,
    _load_config_cached,
    _serialize_toml,
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    _write_config,
    handle_slash_command,
    load_config,
    sse_headers,
)
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    catalog_models_for_provider,
    needs_first_run_setup,
    normalize_llm_settings,
    provider_credentials_ready,
)
from remedy.interfaces.config import _is_local_url
from remedy.models import (
    ChannelKind,
    ChatMessageRole,
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
        # Persist status into SKILL.md when on disk
        with suppress(Exception):
            from remedy.core.learning_loop import LearningLoop

            home = Path(
                getattr(getattr(runtime, "config", None), "home_dir", None)
                or "~/.remedy"
            ).expanduser()
            loop = LearningLoop(skills_dir=home / "skills", memory=None, registry=reg)
            loop._write_skill_md(skill)  # noqa: SLF001
        return {"name": name, "status": st.value}

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

