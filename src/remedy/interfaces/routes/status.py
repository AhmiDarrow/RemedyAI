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


def register_status_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- health / status -----------------------------------------------------
    # Cache COUNT(*) results briefly — status is polled often from the desktop UI.
    _status_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

    @app.get("/api/metrics")
    async def get_metrics():
        """In-process metrics snapshot (counters / gauges / histograms)."""
        from remedy.core.metrics import default_health, default_registry

        health = await default_health.check()
        return {
            "version": _remedy_version,
            "metrics": default_registry.snapshot(),
            "health": health,
            "lines": default_registry.describe(),
        }

    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
        now = time.time()
        cached = _status_cache.get("payload")
        if cached is not None and (now - float(_status_cache.get("ts") or 0)) < 2.0:
            # Refresh only volatile uptime fields.
            gw_stats = gateway.stats() if gateway else {"running": False}
            return StatusResponse(
                **{
                    **cached,
                    "uptime": gw_stats.get("uptime", cached.get("uptime", "N/A")),
                    "gateway": gw_stats,
                }
            )

        gw_stats = gateway.stats() if gateway else {"running": False}
        mem_count = 0
        skills_count = 0
        summary_sessions = 0
        chat_sessions = 0
        if memory:
            try:
                db = memory._ensure_db()
                mem_count = db.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
                summary_sessions = db.execute("SELECT COUNT(*) FROM session_summaries").fetchone()[0]
                chat_sessions = db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
            except Exception:
                pass

        if runtime and hasattr(runtime, "skills") and runtime.skills:
            try:
                skills_count = len(runtime.skills.skills)
            except Exception:
                pass

        payload = {
            "version": _remedy_version,
            "uptime": gw_stats.get("uptime", "N/A"),
            "gateway": gw_stats,
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        _status_cache["ts"] = now
        _status_cache["payload"] = {
            "version": _remedy_version,
            "uptime": payload["uptime"],
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        return StatusResponse(**payload)

