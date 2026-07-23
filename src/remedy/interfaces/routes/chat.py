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


def register_chat_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- chat (legacy) -------------------------------------------------------
    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        if gateway is None:
            raise HTTPException(503, "Gateway not available")

        request_id = str(uuid4())
        session_id = req.session_id or str(uuid4())
        user_msg = req.message

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=user_msg[:60] if user_msg else "New Session",
                ))
            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=user_msg,
            ))

        event = GatewayEvent(
            id=uuid4(),
            kind=EventKind.MESSAGE,
            channel=ChannelKind.WEB,
            source_id=req.user_id or "anonymous",
            payload={
                "message": user_msg,
                "request_id": request_id,
                "session_id": session_id,
            },
            session_id=session_id,
        )

        from remedy.core.metrics import default_registry

        start = time.perf_counter()
        responses = await gateway.emit(event)
        elapsed_s = time.perf_counter() - start
        elapsed = elapsed_s * 1000
        default_registry.counter("remedy_chat_requests_total", path="chat").inc()
        default_registry.histogram("remedy_chat_duration_seconds", path="chat").observe(
            elapsed_s
        )

        response_text = ""
        for r in responses:
            if isinstance(r, str):
                response_text = r
                break

        if memory and response_text:
            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.ASSISTANT,
                content=response_text,
            ))

        return ChatResponse(
            response=response_text or "Processed.",
            request_id=request_id,
            session_id=session_id,
            processing_time_ms=round(elapsed, 1),
        )

