"""REST API server -- FastAPI-based interface for Remedy.

Exposes chat sessions, streaming messages, memory, skills, commands,
models, agents, and webhook endpoints for the desktop and web UI.

Models: api_models.py  |  Helpers: api_support.py  |  Routes: create_app() below.
"""

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
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

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
from remedy.interfaces.config import CONFIG_PATHS
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    catalog_models_for_provider,
    load_config as _load_toml_config,
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

# Re-export models for existing `from remedy.interfaces.api import ChatRequest` callers.
__all__ = [
    "create_app",
    "yaml_schema",
    "ChatRequest",
    "ChatResponse",
    "StatusResponse",
    "WebhookPayload",
    "handle_slash_command",
    "load_config",
    "sse_headers",
]


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = _remedy_version,
    *,
    api_key: str = "",
) -> FastAPI:
    # Let slash commands list skills without threading runtime everywhere.
    handle_slash_command._skills_registry = (  # type: ignore[attr-defined]
        getattr(runtime, "skills", None) if runtime is not None else None
    )

    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
    )

    # CORS: REMEDY_CORS_ORIGINS env wins, then config.toml `cors_origins`, else safe defaults.
    cors_origins_env = os.environ.get("REMEDY_CORS_ORIGINS", "").strip()
    if cors_origins_env == "*":
        cors_origins = ["*"]
    elif cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    else:
        try:
            _cfg = load_config()
        except Exception:
            _cfg = {}
        cfg_origins = _cfg.get("cors_origins") if isinstance(_cfg, dict) else None
        if cfg_origins == "*" or cfg_origins == ["*"]:
            cors_origins = ["*"]
        elif isinstance(cfg_origins, str) and cfg_origins.strip():
            cors_origins = [o.strip() for o in cfg_origins.split(",") if o.strip()]
        elif isinstance(cfg_origins, list) and cfg_origins:
            cors_origins = [str(o).strip() for o in cfg_origins if str(o).strip()]
        else:
            # Safe defaults for local desktop/dev
            cors_origins = [
                "http://localhost:1420",
                "http://127.0.0.1:1420",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "tauri://localhost",
                "http://tauri.localhost",
            ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=cors_origins != ["*"],
    )

    if api_key:
        @app.middleware("http")
        async def require_auth(request: Request, call_next):
            if request.url.path in ("/docs", "/redoc", "/openapi.json", "/dashboard", "/api/status"):
                return await call_next(request)
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {api_key}":
                return JSONResponse(status_code=401, content={"error": "Unauthorized"})
            return await call_next(request)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = (time.time() - start) * 1000
        # Health polls are high-frequency — don't spam the desktop console.
        path = request.url.path
        if path in ("/api/status",) or path.endswith("/api/status"):
            logger.debug("%s %s -> %d (%.0fms)", request.method, path, response.status_code, duration)
        else:
            logger.info(
                "%s %s -> %d (%.0fms)",
                request.method,
                path,
                response.status_code,
                duration,
            )
        return response


    from remedy.interfaces.routes import register_all_routes

    register_all_routes(app, runtime=runtime, gateway=gateway, memory=memory)
    return app


def yaml_schema(app: FastAPI) -> str:
    """Convert OpenAPI JSON to YAML."""
    data = app.openapi()
    import io
    out = io.StringIO()
    yaml.dump(data, out, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return out.getvalue()


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remedy AI - Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0; padding: 2rem; }
        .container { max-width: 900px; margin: 0 auto; }
        h1 { color: #7c3aed; font-size: 2rem; margin-bottom: 0.5rem; }
        .subtitle { color: #888; margin-bottom: 2rem; }
        .card { background: #12122a; border: 1px solid #1e1e3e; border-radius: 8px; padding: 1.5rem; margin-bottom: 1rem; }
        .card h2 { color: #a78bfa; margin-bottom: 1rem; font-size: 1.1rem; }
        .stat { display: flex; justify-content: space-between; padding: 0.3rem 0; border-bottom: 1px solid #1e1e3e; }
        .stat:last-child { border-bottom: none; }
        .stat-label { color: #888; }
        .stat-value { color: #e0e0e0; font-weight: 600; }
        .endpoint { font-family: monospace; background: #0a0a1a; padding: 0.5rem 1rem; border-radius: 4px; margin: 0.3rem 0; }
        .method { color: #7c3aed; font-weight: bold; margin-right: 0.5rem; }
        .path { color: #e0e0e0; }
        .ok { color: #22c55e; }
        .err { color: #ef4444; }
        .section-header { color: #6366f1; font-size: 0.9rem; margin: 1rem 0 0.5rem 0; text-transform: uppercase; letter-spacing: 0.05em; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Remedy AI</h1>
        <p class="subtitle">Self-improving, multi-channel AI agent framework v{{version}}</p>

        <div class="card">
            <h2>Status</h2>
            <div class="stat"><span class="stat-label">Version</span><span class="stat-value">{{version}}</span></div>
            <div class="stat"><span class="stat-label">API</span><span class="stat-value ok">Online</span></div>
        </div>

        <div class="card">
            <h2>API Endpoints</h2>
            <p class="section-header">Chat & Sessions</p>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/chat</span> — legacy sync chat</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/chat/stream</span> (SSE) — legacy stream</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions</span> — list chat sessions</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions</span> — create chat session</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions/{id}</span> — get session</div>
            <div class="endpoint"><span class="method">PATCH</span><span class="path">/api/sessions/{id}</span> — rename session</div>
            <div class="endpoint"><span class="method">DELETE</span><span class="path">/api/sessions/{id}</span> — delete session</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/abort</span> — stop generation</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/sessions/{id}/messages</span> — list messages</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/messages</span> — sync send</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/messages/stream</span> (SSE) — structured events</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/sessions/{id}/command</span> — slash command</div>
            <p class="section-header">Models & Agents</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/models</span> — list LLM models</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/agents</span> — list agent profiles</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/commands</span> — slash commands</div>
            <p class="section-header">Memory & Skills</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/memory/search?query=...</span> — search memory</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/memory/add</span> — add memory entry</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/skills</span> — list skills</div>
            <p class="section-header">Other</p>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/status</span> — system status</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/session-summaries</span> — legacy summaries</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/handoffs</span> — handoff notes</div>
            <div class="endpoint"><span class="method">POST</span><span class="path">/api/webhook/{source}</span> — receive webhook</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.yaml</span> — OpenAPI YAML</div>
            <div class="endpoint"><span class="method">GET</span><span class="path">/api/openapi.json</span> — OpenAPI JSON</div>
        </div>
    </div>
</body>
</html>"""
