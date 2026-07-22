"""REST API server -- FastAPI-based interface for Remedy.

Exposes the gateway, memory, skills, and learning systems via HTTP.
Provides:
- POST /api/chat — send a message, get an agent response
- GET /api/memory/search — search memory
- GET /api/skills — list available skills
- GET /api/status — gateway/system status
- Webhook endpoints for external triggers
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from remedy.models import (
    ChannelKind,
    EventKind,
    GatewayEvent,
    MemoryEntryType,
)

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    session_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default="default")
    channel: Optional[str] = Field(default="api")


class ChatResponse(BaseModel):
    response: str
    request_id: str
    session_id: Optional[str] = None
    processing_time_ms: float = 0.0


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class SkillInfo(BaseModel):
    name: str
    description: str
    version: str
    kind: str
    status: str
    tags: list[str] = []


class StatusResponse(BaseModel):
    status: str = "ok"
    version: str
    uptime: str
    gateway: dict
    memory_entries: int = 0
    skills_count: int = 0
    sessions_count: int = 0


class WebhookPayload(BaseModel):
    source: str
    event: str = "default"
    data: dict[str, Any] = Field(default_factory=dict)
    signature: Optional[str] = None


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = "0.1.0",
) -> FastAPI:
    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework REST API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- middleware: request logging + timing --------------------------------
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = (time.time() - start) * 1000
        logger.info("%s %s -> %d (%.0fms)",
                     request.method, request.url.path,
                     response.status_code, duration)
        return response

    # -- health / status -----------------------------------------------------
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        gw_stats = gateway.stats() if gateway else {"running": False}
        mem_count = 0
        if memory:
            try:
                entries = await memory.list_recent(limit=1)
                mem_count = len(entries)
            except Exception:
                pass

        return StatusResponse(
            version=version,
            uptime=gw_stats.get("uptime", "N/A"),
            gateway=gw_stats,
            memory_entries=mem_count,
        )

    # -- chat ----------------------------------------------------------------
    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        if gateway is None:
            raise HTTPException(503, "Gateway not available")

        request_id = str(uuid4())
        event = GatewayEvent(
            id=uuid4(),
            kind=EventKind.MESSAGE,
            channel=ChannelKind.WEB,
            source_id=req.user_id or "anonymous",
            payload={
                "message": req.message,
                "request_id": request_id,
                "session_id": req.session_id,
            },
            session_id=req.session_id,
        )

        start = time.time()
        responses = await gateway.emit(event)
        elapsed = (time.time() - start) * 1000

        response_text = ""
        for r in responses:
            if isinstance(r, str):
                response_text = r
                break

        return ChatResponse(
            response=response_text or "Processed.",
            request_id=request_id,
            session_id=req.session_id,
            processing_time_ms=round(elapsed, 1),
        )

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
    async def add_memory(req: MemorySearchRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        from remedy.models import MemoryEntry
        entry = MemoryEntry(
            title=req.query,
            content=req.query,
            entry_type=MemoryEntryType.NOTE,
        )
        saved = await memory.upsert(entry)
        return {"id": str(saved.id), "title": saved.title, "status": "saved"}

    # -- skills --------------------------------------------------------------
    @app.get("/api/skills", response_model=list[SkillInfo])
    async def list_skills():
        if runtime is None or not hasattr(runtime, "skills"):
            return []
        return [
            SkillInfo(
                name=s.manifest.name,
                description=s.manifest.description,
                version=s.manifest.version,
                kind=s.manifest.kind.value,
                status=s.manifest.status.value,
                tags=s.manifest.tags,
            )
            for s in runtime.skills.skills
        ]

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

    # -- sessions ------------------------------------------------------------
    @app.get("/api/sessions")
    async def list_sessions(limit: int = Query(default=10, le=50)):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        sessions = await memory.list_sessions(limit=limit)
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
                for s in sessions
            ]
        }

    # -- handoffs ------------------------------------------------------------
    @app.get("/api/handoffs")
    async def list_handoffs(limit: int = Query(default=10, le=50)):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
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

    return app
