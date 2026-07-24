"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
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

