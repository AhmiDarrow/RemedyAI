"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path

# suppress used by archive-unused / packs
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

    # Static skill routes MUST be registered before /api/skills/{name}
    # or Starlette treats "packs" as a skill name.
    @app.get("/api/skills/packs")
    async def get_skill_packs():
        """List skill packs (power-user grouping)."""
        from pathlib import Path

        from remedy.skills.shared import load_skill_packs

        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()
        data = load_skill_packs(home)
        budget = 80
        try:
            from remedy.interfaces.api_support import load_config

            budget = int((load_config() or {}).get("skills_active_budget") or 80)
        except Exception:
            pass
        active_count = 0
        if runtime is not None and hasattr(runtime, "skills"):
            for s in runtime.skills.skills:
                st = s.manifest.status
                stv = st.value if hasattr(st, "value") else str(st)
                if stv not in ("archived", "disabled", "deprecated"):
                    if not (s.manifest.metadata or {}).get("quarantine"):
                        active_count += 1
        return {
            **data,
            "active_count": active_count,
            "active_budget": budget,
            "budget_banner": (
                f"{active_count} / {budget} in active set — archive or pack to stay sharp"
                if active_count >= int(budget * 0.85)
                else None
            ),
        }

    @app.put("/api/skills/packs")
    async def put_skill_packs(request: Request):
        """Save skill packs definition + enabled pack list."""
        from pathlib import Path

        from remedy.skills.shared import save_skill_packs

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required") from None
        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()
        data = {
            "packs": payload.get("packs") if isinstance(payload.get("packs"), dict) else {},
            "enabled": list(payload.get("enabled") or []),
        }
        path = save_skill_packs(data, home)
        return {"status": "ok", "path": str(path), **data}

    def _skill_home() -> Path:
        return Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()

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

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str, purge: bool = Query(default=True)):
        """Remove a user-installed skill from the registry and (by default) disk.

        The Skills panel ✗ control is *feedback*, not delete — this endpoint is
        the real remove path for library installs, imports, and learned skills.

        Safety: only deletes directories under ``~/.remedy/skills/``. Bundled package
        skills (shipped under the Remedy install) cannot be deleted this way.
        """
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

    @app.post("/api/skills/archive-unused")
    async def archive_unused_skills(request: Request):
        """Archive skills unused for N days (power-user 100+ library hygiene).

        Does not delete packs. Skips bundled/quarantine unless include_quarantine.
        Body: { days?: 90, dry_run?: false, include_quarantine?: false }
        """
        if runtime is None or not hasattr(runtime, "skills"):
            raise HTTPException(503, "Skills not available")
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        days = int((payload or {}).get("days") or 90)
        dry_run = bool((payload or {}).get("dry_run", False))
        include_q = bool((payload or {}).get("include_quarantine", False))
        days = max(7, min(3650, days))

        from datetime import UTC, datetime, timedelta
        from pathlib import Path

        from remedy.core.learning.refiner import SkillRefiner
        from remedy.models import SkillStatus as _SS

        home = Path(
            getattr(getattr(runtime, "config", None), "home_dir", None) or "~/.remedy"
        ).expanduser()
        refiner = SkillRefiner(stats_path=home / "skill_stats.json")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        candidates: list[dict] = []
        archived: list[str] = []

        for skill in list(runtime.skills.skills):
            m = skill.manifest
            st = m.status
            stv = st.value if hasattr(st, "value") else str(st)
            if stv in ("archived", "deprecated"):
                continue
            meta = m.metadata or {}
            if meta.get("quarantine") and not include_q:
                continue
            # Prefer never archiving non-auto bundled packs that are active defaults
            if not meta.get("auto_generated") and stv == "active" and not meta.get(
                "user_installed"
            ):
                # still allow if truly unused for long — only auto/user-touched
                if not meta.get("manual_override") and not meta.get("source"):
                    # skip pure bundled unless executions exist and went cold
                    pass

            stats = refiner.get_stats(m.name) if hasattr(refiner, "get_stats") else None
            last_dt = None
            if stats is not None:
                last_dt = getattr(stats, "last_activated", None) or getattr(
                    stats, "last_executed", None
                )
            if last_dt is None and hasattr(refiner, "last_success_at"):
                last_dt = refiner.last_success_at(m.name)

            # Never used at all + not recently created: use zero activity
            never = last_dt is None and (
                not stats
                or (
                    int(getattr(stats, "total_executions", 0) or 0) == 0
                    and int(getattr(stats, "activations", 0) or 0) == 0
                )
            )
            cold = False
            if last_dt is not None:
                try:
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=UTC)
                    cold = last_dt < cutoff
                except Exception:
                    cold = False

            # Only archive auto-generated / learned or explicitly user-overridden when never used
            is_learned = bool(meta.get("auto_generated"))
            if not (cold or (never and is_learned)):
                continue
            if never and not is_learned:
                # Bundled unused — leave alone
                continue

            row = {
                "name": m.name,
                "status": stv,
                "last_activity": last_dt.isoformat() if last_dt else None,
                "auto_generated": is_learned,
            }
            candidates.append(row)
            if dry_run:
                continue
            try:
                if hasattr(runtime.skills, "set_status"):
                    runtime.skills.set_status(m.name, _SS.ARCHIVED)
                else:
                    skill.manifest.status = _SS.ARCHIVED
                meta = dict(skill.manifest.metadata or {})
                meta["lifecycle"] = "bulk-archive"
                meta["lifecycle_last"] = f"Archived: unused >{days}d"
                skill.manifest.metadata = meta
                _persist_skill(skill)
                archived.append(m.name)
            except Exception:
                continue

        if archived and not dry_run:
            with suppress(Exception):
                from remedy.skills.shared import invalidate_shared_registry

                invalidate_shared_registry()
        return {
            "days": days,
            "dry_run": dry_run,
            "candidates": candidates,
            "archived": archived,
            "count": len(archived) if not dry_run else len(candidates),
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
            import hmac

            auth = request.headers.get("Authorization", "")
            secret = request.headers.get("X-Remedy-Webhook-Secret", "")
            expected_bearer = f"Bearer {expected}"
            bearer_ok = hmac.compare_digest(
                auth.encode("utf-8"), expected_bearer.encode("utf-8")
            )
            webhook_secret = (_os.environ.get("REMEDY_WEBHOOK_SECRET") or "").strip()
            secret_ok = bool(secret) and (
                hmac.compare_digest(secret.encode("utf-8"), expected.encode("utf-8"))
                or (
                    bool(webhook_secret)
                    and hmac.compare_digest(
                        secret.encode("utf-8"), webhook_secret.encode("utf-8")
                    )
                )
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

