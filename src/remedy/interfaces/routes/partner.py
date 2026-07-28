"""Partner-loop routes: goals, approvals, knowledge pack import."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class ApprovalResolveRequest(BaseModel):
    approve: bool = True
    scope: str = Field(default="session", description="session | always")


class KnowledgeImportRequest(BaseModel):
    path: str = Field(..., description="Folder of .md/.txt notes to import")
    tag: str = Field(default="knowledge-pack")
    max_files: int = Field(default=200, ge=1, le=2000)


class GoalCreateRequest(BaseModel):
    title: str
    description: str = ""


class PlanCreateRequest(BaseModel):
    title: str
    goal: str = ""
    steps: list[str | dict] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    session_id: str | None = None
    status: str = "draft"


class PlanStatusRequest(BaseModel):
    status: str = Field(..., description="draft | approved | active | done | cancelled")


def register_partner_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    _ = gateway

    @app.get("/api/approvals")
    async def list_approvals(session_id: str | None = None):
        from remedy.core.approvals import APPROVALS

        items = APPROVALS.list_pending(session_id=session_id)
        return {"approvals": [APPROVALS.to_public(i) for i in items]}

    @app.post("/api/approvals/{approval_id}/resolve")
    async def resolve_approval(approval_id: str, req: ApprovalResolveRequest):
        from remedy.core.approvals import APPROVALS

        item = APPROVALS.resolve(
            approval_id,
            approve=req.approve,
            scope=req.scope if req.scope in ("session", "always") else "session",
        )
        if item is None:
            raise HTTPException(404, "Approval not found")
        return {
            "status": item.status,
            "approval": APPROVALS.to_public(item),
            "hint": (
                "Approved — ask Remedy to retry the same command."
                if item.status == "approved"
                else "Denied — do not run the command."
            ),
        }

    @app.get("/api/goals")
    async def list_goals():
        if runtime is None or not hasattr(runtime, "list_tasks"):
            return {"goals": []}
        tasks = runtime.list_tasks()
        goals = [t for t in tasks if "goal" in (t.tags or [])]
        if not goals:
            goals = list(tasks)
        return {
            "goals": [
                {
                    "id": str(t.id),
                    "title": t.title,
                    "description": t.description,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "result_summary": t.result_summary,
                    "tags": t.tags,
                }
                for t in goals
            ]
        }

    @app.post("/api/goals")
    async def create_goal(req: GoalCreateRequest):
        if runtime is None or not hasattr(runtime, "create_task"):
            raise HTTPException(503, "Runtime not available")
        task = runtime.create_task(
            req.title.strip(),
            description=req.description or "",
            tags=["goal"],
        )
        return {
            "id": str(task.id),
            "title": task.title,
            "status": task.status.value,
        }

    def _plan_store():
        from pathlib import Path

        from remedy.core.plan_store import PlanStore

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        return PlanStore(home or Path.home() / ".remedy")

    @app.get("/api/plans")
    async def list_plans(session_id: str | None = None, limit: int = 30):
        store = _plan_store()
        plans = store.list_plans(session_id=session_id, limit=min(max(limit, 1), 100))
        return {"plans": [p.to_dict() for p in plans]}

    @app.get("/api/plans/latest")
    async def latest_plan(
        session_id: str | None = None,
        actionable: bool = False,
    ):
        """Latest plan for the session (or global latest when session_id omitted).

        When *session_id* is provided, never fall back to another session's plan —
        a fresh chat must show an empty Plan banner until plan_save in that session.

        *actionable*=true skips done/cancelled (Plan banner / Build kickoff).
        """
        store = _plan_store()
        plan = store.latest_for_session(
            session_id if session_id else None,
            actionable_only=bool(actionable),
        )
        if plan is None:
            return {"plan": None}
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.get("/api/plans/{plan_id}")
    async def get_plan(plan_id: str):
        store = _plan_store()
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, "Plan not found")
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.post("/api/plans")
    async def create_plan(req: PlanCreateRequest):
        store = _plan_store()
        plan = store.create(
            req.title.strip(),
            goal=req.goal or req.title,
            steps=list(req.steps or []),
            risks=list(req.risks or []),
            session_id=req.session_id,
            status=req.status or "draft",
        )
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.post("/api/plans/{plan_id}/status")
    async def set_plan_status(plan_id: str, req: PlanStatusRequest):
        store = _plan_store()
        plan = store.set_status(plan_id, req.status.strip().lower())
        if plan is None:
            raise HTTPException(404, "Plan not found or invalid status")
        return {"plan": plan.to_dict(), "markdown": plan.summary_markdown()}

    @app.get("/api/checkpoints")
    async def list_checkpoints(session_id: str | None = None, limit: int = 20):
        from pathlib import Path

        from remedy.core.checkpoint import CheckpointStore

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        store = CheckpointStore(home or Path.home() / ".remedy")
        items = store.list_for_session(session_id, limit=min(max(limit, 1), 50))
        return {"checkpoints": [c.to_dict() for c in items]}

    @app.get("/api/checkpoints/latest")
    async def latest_checkpoint(session_id: str | None = None):
        from pathlib import Path

        from remedy.core.checkpoint import CheckpointStore

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        store = CheckpointStore(home or Path.home() / ".remedy")
        cp = store.latest(session_id)
        if cp is None:
            return {"checkpoint": None}
        return {"checkpoint": cp.to_dict(), "markdown": cp.summary_markdown()}

    @app.post("/api/memory/import")
    async def import_knowledge(req: KnowledgeImportRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        from remedy.memory.knowledge_pack import import_knowledge_pack

        result = await import_knowledge_pack(
            memory,
            req.path,
            max_files=req.max_files,
            tag=req.tag or "knowledge-pack",
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error") or "Import failed")
        return result

    @app.get("/api/partner/status")
    async def partner_status():
        """Compact status for desktop status bar / harness chip."""
        from remedy.core.approvals import APPROVALS

        # Keep thumbs-up (auto) in sync with config.toml every poll.
        try:
            from remedy.interfaces.api_support import load_config

            APPROVALS.sync_from_config(load_config() or {})
        except Exception:
            pass
        pending = APPROVALS.list_pending()
        goals_open = 0
        harness = "auto"
        scope = "project"
        brief_intent = ""
        if runtime is not None:
            if hasattr(runtime, "list_tasks"):
                from remedy.models import TaskStatus

                goals_open = len(
                    [
                        t
                        for t in runtime.list_tasks()
                        if t.status
                        not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.FAILED)
                    ]
                )
            scope = getattr(runtime, "_access_scope", None) or getattr(
                runtime, "access_scope", lambda: "project"
            )
            if callable(scope):
                scope = scope()
            harness = getattr(runtime, "_harness_mode", "auto")
            brief = getattr(runtime, "_session_brief", None)
            if brief is not None:
                brief_intent = getattr(brief, "intent", "") or ""
        swarm: dict = {}
        health_pub: dict = {}
        try:
            from remedy.nanoswarm import get_swarm

            st = get_swarm().status()
            swarm = {
                "active": True,
                "event_count": st.get("event_count"),
                "local_model_id": st.get("local_model_id"),
                "last_event": st.get("last_event"),
                "fill_pct": (st.get("bots") or {}).get("memory", {}).get("last_fill_pct"),
                "token_method": (st.get("bots") or {}).get("token", {}).get("last_method"),
            }
            # Proactive failover signal for status bar
            prov = getattr(runtime, "_llm_provider", None) if runtime is not None else None
            mod = getattr(runtime, "_llm_model", None) if runtime is not None else None
            connected: list[str] = []
            try:
                from remedy.interfaces.api_support import load_config
                from remedy.interfaces.config import get_provider_keys
                from remedy.interfaces.secret_store import public_secret_status

                cfg = load_config()
                keys = get_provider_keys(cfg)
                connected = list(keys.keys())
                pub = public_secret_status()
                for k in (pub.get("provider_keys_set") or {}):
                    if k not in connected:
                        connected.append(k)
                # Always allow demo/ollama as soft fallbacks when flaky
                for extra in ("demo", "ollama"):
                    if extra not in connected:
                        connected.append(extra)
            except Exception:
                connected = ["demo", "ollama"]
            health_pub = get_swarm().health.failover_suggestion(
                provider=str(prov) if prov else None,
                model=str(mod) if mod else None,
                connected_providers=connected,
            )
        except Exception:
            swarm = {"active": False}
            health_pub = {}

        quality: dict = {}
        try:
            from remedy.core.session_quality import get_session_quality

            sid = getattr(runtime, "_session_id", None) if runtime is not None else None
            quality = get_session_quality(str(sid or "")).snapshot()
        except Exception:
            quality = {}

        return {
            "pending_approvals": len(pending),
            "approval_mode": APPROVALS.mode,
            "open_goals": goals_open,
            "access_scope": scope,
            "harness_mode": harness,
            "brief_intent": brief_intent[:200],
            "approvals": [APPROVALS.to_public(i) for i in pending[:5]],
            # Advanced: only meaningful when user opted into Full+ in the UI
            "nanoswarm": swarm,
            "session_quality": quality,
            "provider_health": health_pub,
        }
