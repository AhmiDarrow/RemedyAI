"""Partner-loop routes: goals, approvals, knowledge pack import."""

from __future__ import annotations

from typing import Any

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
        return {
            "pending_approvals": len(pending),
            "open_goals": goals_open,
            "access_scope": scope,
            "harness_mode": harness,
            "brief_intent": brief_intent[:200],
            "approvals": [APPROVALS.to_public(i) for i in pending[:5]],
        }
