"""REST API server -- FastAPI-based interface for Remedy.

Exposes chat sessions, streaming messages, memory, skills, commands,
models, agents, and webhook endpoints for the desktop and web UI.
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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from remedy import __version__ as _remedy_version
from remedy.core.errors import SecurityError
from remedy.core.security import safe_path
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


# -- request / response models -----------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message to the agent")
    session_id: str | None = Field(default=None)
    user_id: str | None = Field(default="default")
    channel: str | None = Field(default="api")


class ChatResponse(BaseModel):
    response: str
    request_id: str
    session_id: str | None = None
    processing_time_ms: float = 0.0


class MemorySearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=50)


class MemoryAddRequest(BaseModel):
    title: str = Field(..., description="Title for the memory entry")
    content: str = Field(..., description="Memory content")
    tags: list[str] = Field(default_factory=list, description="Optional tags")
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


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
    chat_sessions_count: int = 0


class WebhookPayload(BaseModel):
    source: str
    event: str = "default"
    data: dict[str, Any] = Field(default_factory=dict)
    signature: str | None = None


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Session")
    model: str | None = None
    agent: str | None = None
    project_path: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    agent: str | None = None
    project_path: str | None = None


class SendMessageRequest(BaseModel):
    message: str = Field(..., description="User message text")
    model: str | None = None
    agent: str | None = None


class CommandRequest(BaseModel):
    command: str = Field(..., description="Slash command to execute (e.g. /new)")


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    project_path: str | None = None
    name: str | None = None
    persona: str | None = None
    setup_completed: bool | None = None


# -- API factory -------------------------------------------------------------


# -- SSE streaming -----------------------------------------------------------
async def _sse_stream_text(text: str, *, event: str | None = None) -> str:
    """Format a single SSE frame."""
    prefix = f"event: {event}\n" if event else ""
    payload_obj: dict = {"text": text}
    if event:
        payload_obj["type"] = event
    payload = json.dumps(payload_obj)
    return f"{prefix}data: {payload}\n\n"


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


# -- built-in slash commands -------------------------------------------------

_BUILTIN_COMMANDS: list[dict] = [
    {"name": "/help", "description": "Show available commands", "aliases": [], "arguments": None},
    {"name": "/new", "description": "Create a new chat session", "aliases": [], "arguments": None},
    {"name": "/sessions", "description": "List recent sessions", "aliases": [], "arguments": None},
    {"name": "/compact", "description": "Compact / summarize the current session", "aliases": [], "arguments": None},
    {"name": "/models", "description": "List available models", "aliases": [], "arguments": None},
    {"name": "/thinking", "description": "Toggle thinking visibility", "aliases": [], "arguments": None},
    {"name": "/memory", "description": "Search memory", "aliases": [], "arguments": "query"},
    {"name": "/skills", "description": "List available skills", "aliases": [], "arguments": None},
    {"name": "/handoff", "description": "List handoff notes", "aliases": [], "arguments": None},
    {"name": "/init", "description": "Scan the project and generate AGENTS.md", "aliases": [], "arguments": "path"},
]

# Legacy flat list kept for slash-command fallback only; list_models uses
# PROVIDER_CATALOG and filters strictly by the configured provider.
_BUILTIN_MODELS: list[dict] = []
for _prov, _meta in PROVIDER_CATALOG.items():
    for _m in _meta.get("models") or []:
        _BUILTIN_MODELS.append(
            {
                "id": _m["id"],
                "name": _m.get("name", _m["id"]),
                "provider": _prov,
                "default": False,
            }
        )

_BUILTIN_AGENTS: list[dict] = [
    {"name": "default", "description": "Remedy — general-purpose agent", "build_mode": True},
    {"name": "remedy", "description": "Remedy — meta-orchestrator with skill routing", "build_mode": True},
    {"name": "explore", "description": "Codebase explorer for search and analysis", "build_mode": False},
    {"name": "general", "description": "General-purpose agent for complex tasks", "build_mode": True},
]


async def handle_slash_command(
    command: str, session_id: str | None, memory
) -> dict:
    """Execute a slash command and return a result."""
    stripped = command.strip().lower()

    if stripped in ("/help", "/h"):
        cmds = "\n".join(f"  {c['name']} — {c['description']}" for c in _BUILTIN_COMMANDS)
        return {"text": f"Available commands:\n{cmds}"}

    if stripped in ("/new", "/n"):
        return {"text": "Session marked for creation.", "action": "new_session"}

    if stripped in ("/sessions", "/s"):
        if memory is None:
            return {"text": "Memory store not available."}
        sessions = await memory.list_chat_sessions(limit=10)
        if not sessions:
            return {"text": "No sessions found."}
        lines = []
        for s in sessions:
            sid = getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "")
            title = getattr(s, "title", None) or (s.get("title") if isinstance(s, dict) else "Untitled")
            count = getattr(s, "message_count", None)
            if count is None and isinstance(s, dict):
                count = s.get("message_count", 0)
            lines.append(f"  {title} — {count or 0} msg — {str(sid)[:8]}")
        return {"text": "Recent sessions:\n" + "\n".join(lines)}

    if stripped in ("/models", "/m"):
        return {
            "text": (
                "Model list is filtered by your configured provider. "
                "Use the model picker in the status bar, or GET /api/models."
            ),
            "action": "list_models",
        }

    if stripped == "/thinking":
        return {"text": "Thinking visibility toggled."}

    if stripped.startswith("/memory "):
        query = command[len("/memory "):].strip()
        if not query or memory is None:
            return {"text": "Usage: /memory <query>"}
        entries = await memory.search(query, limit=5)
        if not entries:
            return {"text": "No memory entries found."}
        lines = []
        for e in entries:
            lines.append(f"  **{e.title}** — {e.content[:120]}")
        return {"text": "Memory results:\n" + "\n".join(lines)}

    if stripped in ("/memory", "/mem"):
        return {"text": "Usage: /memory <query>"}

    if stripped in ("/skills", "/sk"):
        return {"text": "Use GET /api/skills for the skill listing."}

    if stripped in ("/handoff", "/ho"):
        if memory is None:
            return {"text": "Memory store not available."}
        handoffs = await memory.list_handoffs(limit=5)
        if not handoffs:
            return {"text": "No handoff notes found."}
        lines = []
        for h in handoffs:
            lines.append(f"  **{h.title}** — {h.content[:100]}")
        return {"text": "Handoffs:\n" + "\n".join(lines)}

    if stripped == "/compact":
        return {"text": "Session compaction requested (stub)."}

    if stripped.startswith("/init"):
        parts = stripped.split(" ", 1)
        path = parts[1] if len(parts) > 1 else "."
        return {"text": f"Project scan requested for: {path}\nUse the API endpoint POST /api/projects/scan?path=... for detailed results.", "action": "init_scan"}

    return {"text": f"Unknown command: {command}\nType /help for available commands."}


def _default_config_path() -> Path:
    """Canonical user config path (matches desktop sidecar --home)."""
    return Path.home() / ".remedy" / "config.toml"


def _find_config_path() -> Path | None:
    # Prefer the home config so desktop and CLI always share one persistent file.
    primary = _default_config_path()
    if primary.exists():
        return primary
    for p in CONFIG_PATHS:
        expanded = p.expanduser().resolve()
        if expanded.exists():
            return expanded
    return None


def load_config() -> dict[str, Any]:
    path = _find_config_path()
    if path is None:
        return {}
    return _load_toml_config(path)


def _apply_llm_to_runtime(
    runtime: Any,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str | None = None,
    persona: str | None = None,
    name: str | None = None,
    project_path: str | None = None,
) -> None:
    """Push LLM settings into the live runtime so chat uses the saved config."""
    if runtime is None:
        return
    if hasattr(runtime, "reconfigure_llm"):
        kwargs: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "persona": persona,
            "name": name,
        }
        if project_path is not None:
            kwargs["project_path"] = project_path
        runtime.reconfigure_llm(**kwargs)
        return
    # Fallback for older runtimes without reconfigure_llm
    if provider:
        runtime._llm_provider = provider
    if model:
        runtime._llm_model = model
    if base_url:
        runtime._llm_base_url = base_url
    if api_key is not None and api_key != "":
        runtime._llm_api_key = api_key
    if project_path is not None and hasattr(runtime, "set_project_path"):
        runtime.set_project_path(project_path, as_default=True)


def _sync_runtime_llm_from_config(
    runtime: Any,
    *,
    model_override: str | None = None,
) -> str:
    """Reload provider/model/url/key from disk into the live runtime.

    Returns the effective API key (may be empty). Always re-reads config so
    settings saved after server start (or first-run wizard) are used on the
    next chat message without a restart.
    """
    if runtime is None:
        return ""
    cfg = load_config()
    provider = str(
        cfg.get("llm_provider")
        or getattr(runtime, "_llm_provider", None)
        or os.environ.get("REMEDY_LLM_PROVIDER")
        or "openai"
    )
    model = str(
        model_override
        or cfg.get("llm_model")
        or getattr(runtime, "_llm_model", None)
        or os.environ.get("REMEDY_LLM_MODEL")
        or ""
    )
    base_url = str(
        cfg.get("llm_base_url")
        or getattr(runtime, "_llm_base_url", None)
        or os.environ.get("REMEDY_LLM_BASE_URL")
        or ""
    )
    api_key = str(
        cfg.get("llm_api_key")
        or os.environ.get("REMEDY_LLM_API_KEY")
        or getattr(runtime, "_llm_api_key", "")
        or ""
    )
    # Local providers: ensure a dummy key so stream path does not fall back.
    if not api_key and (
        provider.lower() == "ollama" or (base_url and _is_local_url(base_url))
    ):
        api_key = "local"

    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key if api_key else None,
    )
    return str(getattr(runtime, "_llm_api_key", "") or api_key or "")


def _write_config(path: Path, cfg: dict[str, Any]) -> None:
    lines = []
    lines.append("# Remedy AI Configuration\n\n")
    for key, value in cfg.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]\n")
            for k, v in value.items():
                lines.append(f"{k} = {_serialize_toml(v)}\n")
            lines.append("\n")
        else:
            lines.append(f"{key} = {_serialize_toml(value)}\n")
    content = "".join(lines)
    path.write_text(content, encoding="utf-8")


def _serialize_toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_serialize_toml(v) for v in value)
        return f"[{items}]"
    return json.dumps(str(value))


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = _remedy_version,
    *,
    api_key: str = "",
) -> FastAPI:
    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
    )

    cors_origins_env = os.environ.get("REMEDY_CORS_ORIGINS", "").strip()
    if cors_origins_env == "*":
        cors_origins = ["*"]
    elif cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
    else:
        # Safe defaults for local desktop/dev; override with REMEDY_CORS_ORIGINS
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

    # -- health / status -----------------------------------------------------
    # Cache COUNT(*) results briefly — status is polled often from the desktop UI.
    _status_cache: dict[str, Any] = {"ts": 0.0, "payload": None}

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
            "version": version,
            "uptime": gw_stats.get("uptime", "N/A"),
            "gateway": gw_stats,
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        _status_cache["ts"] = now
        _status_cache["payload"] = {
            "version": version,
            "uptime": payload["uptime"],
            "memory_entries": mem_count,
            "skills_count": skills_count,
            "sessions_count": summary_sessions,
            "chat_sessions_count": chat_sessions,
        }
        return StatusResponse(**payload)

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

        start = time.time()
        responses = await gateway.emit(event)
        elapsed = (time.time() - start) * 1000

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

    # -- chat sessions -------------------------------------------------------
    @app.get("/api/sessions")
    async def list_chat_sessions(
        limit: int = Query(default=50, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        if memory is None:
            return {"sessions": []}
        sessions = await memory.list_chat_sessions(limit=limit, offset=offset)
        return {"sessions": sessions}

    def _default_project_path() -> str | None:
        """Resolved default workspace from config / runtime."""
        from remedy.core.workspace import default_project_from_config, resolve_project_path

        cfg = load_config()
        if runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                return str(runtime.effective_project_path())
            except Exception:
                pass
        return str(default_project_from_config(cfg))

    @app.post("/api/sessions")
    async def create_chat_session(req: CreateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        from remedy.core.workspace import ensure_project_dir, resolve_project_path
        from remedy.models import ChatSession as CS

        # Inherit global project_path when the client does not pass one.
        raw_project = req.project_path or load_config().get("project_path")
        project_path = None
        if raw_project and str(raw_project).strip() and str(raw_project).strip() not in (".", "./"):
            try:
                project_path = str(ensure_project_dir(resolve_project_path(str(raw_project))))
            except Exception:
                project_path = str(resolve_project_path(str(raw_project)))

        session = CS(
            title=req.title,
            model=req.model,
            agent=req.agent,
            project_path=project_path,
        )
        saved = await memory.create_chat_session(session)
        return {
            "id": saved.id,
            "title": saved.title,
            "model": saved.model,
            "agent": saved.agent,
            "project_path": saved.project_path,
            "message_count": saved.message_count,
            "created_at": saved.created_at.isoformat() if saved.created_at else None,
            "updated_at": saved.updated_at.isoformat() if saved.updated_at else None,
        }

    @app.get("/api/sessions/{session_id}")
    async def get_chat_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        session = await memory.get_chat_session(session_id)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
            "project_path": session.project_path,
            "message_count": session.message_count,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.patch("/api/sessions/{session_id}")
    async def update_chat_session(session_id: str, req: UpdateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        if "project_path" in fields and fields["project_path"]:
            from remedy.core.workspace import ensure_project_dir, resolve_project_path

            try:
                fields["project_path"] = str(
                    ensure_project_dir(resolve_project_path(str(fields["project_path"])))
                )
            except Exception:
                fields["project_path"] = str(resolve_project_path(str(fields["project_path"])))
        session = await memory.update_chat_session(session_id, **fields)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
            "project_path": session.project_path,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.delete("/api/sessions/{session_id}")
    async def delete_chat_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        deleted = await memory.delete_chat_session(session_id)
        if not deleted:
            raise HTTPException(404, "Session not found")
        return {"status": "deleted", "session_id": session_id}

    @app.post("/api/sessions/{session_id}/abort")
    async def abort_session(session_id: str):
        return {"status": "aborted", "session_id": session_id}

    # -- messages ------------------------------------------------------------
    @app.get("/api/sessions/{session_id}/messages")
    async def list_messages(
        session_id: str,
        limit: int = Query(default=100, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        msgs = await memory.get_chat_messages(session_id, limit=limit, offset=offset)
        return {
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role.value,
                    "content": m.content,
                    "thinking": m.thinking,
                    "tool_calls": m.tool_calls,
                    "tool_results": m.tool_results,
                    "model": m.model,
                    "agent": m.agent,
                    "tokens": m.tokens,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "reverted": m.reverted,
                }
                for m in msgs
            ]
        }

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        request_id = str(uuid4())

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                default_proj = load_config().get("project_path")
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=req.message[:60],
                    model=req.model,
                    agent=req.agent,
                    project_path=default_proj,
                ))
            elif req.model and req.model != existing.model:
                await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=req.message,
            ))

        # Always re-sync credentials from disk (wizard/settings may have just saved).
        _sync_runtime_llm_from_config(runtime, model_override=req.model)

        start = time.time()
        response_text = ""
        async for token in runtime.stream_response(
            req.message, session_id=session_id, model=req.model
        ):
            # Keep user-visible text only (tool lifecycle events are @@-prefixed).
            if isinstance(token, str) and token.startswith("@@"):
                continue
            response_text += token
        elapsed = (time.time() - start) * 1000

        if memory and response_text:
            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.ASSISTANT,
                content=response_text,
            ))

        return {
            "request_id": request_id,
            "session_id": session_id,
            "response": response_text or "Processed.",
            "processing_time_ms": round(elapsed, 1),
        }

    # -- SSE streaming (structured events) -----------------------------------
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        request_id = str(uuid4())

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                default_proj = load_config().get("project_path")
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=req.message[:60],
                    model=req.model,
                    agent=req.agent,
                    project_path=default_proj,
                ))
            elif req.model and req.model != existing.model:
                await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=req.message,
                model=req.model,
                agent=req.agent,
            ))

        # Always re-sync credentials from disk (first-run wizard / settings).
        api_key = _sync_runtime_llm_from_config(runtime, model_override=req.model)

        async def event_stream():
            yield (
                f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
            )

            try:
                full_response = ""
                if not api_key:
                    msg = (
                        "No LLM API key configured. Complete first-run setup or open Settings, "
                        "set your provider API key, and Save — then try again."
                    )
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                    return

                async for token in runtime.stream_response(
                    req.message, session_id=session_id, model=req.model
                ):
                    if token.startswith("@@tool_call:"):
                        tool_name = token[len("@@tool_call:"):]
                        yield (
                            f"event: tool_call\ndata: {json.dumps({'type': 'tool_call', 'name': tool_name})}\n\n"
                        )
                    elif token.startswith("@@tool_result:"):
                        tool_name = token[len("@@tool_result:"):]
                        yield (
                            f"event: tool_result\ndata: {json.dumps({'type': 'tool_result', 'name': tool_name})}\n\n"
                        )
                    elif token == "@@tool_calls":
                        pass
                    else:
                        full_response += token
                        yield await _sse_stream_text(token, event="token")

                if full_response and memory:
                    await memory.add_chat_message(ChatMessage(
                        session_id=session_id,
                        role=ChatMessageRole.ASSISTANT,
                        content=full_response,
                        model=req.model or getattr(runtime, "_llm_model", None),
                    ))

                yield (
                    f"event: done\ndata: {json.dumps({'type': 'done', 'request_id': request_id})}\n\n"
                )

            except asyncio.CancelledError:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': 'Request cancelled.'})}\n\n"
            except Exception as e:
                logger.exception("SSE stream error")
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )

    # -- legacy chat stream (maintained for backward compatibility) ----------
    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        request_id = str(uuid4())
        session_id = req.session_id or str(uuid4())

        async def event_stream():
            yield (
                f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
            )

            try:
                async for token in runtime.stream_response(req.message, session_id=session_id):
                    yield await _sse_stream_text(token, event="token")
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

            yield f"event: done\ndata: {json.dumps({'type': 'done', 'request_id': request_id})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )

    # -- commands (slash palette) --------------------------------------------
    @app.get("/api/commands")
    async def list_commands():
        return {"commands": _BUILTIN_COMMANDS}

    @app.post("/api/sessions/{session_id}/command")
    async def execute_command(session_id: str, req: CommandRequest):
        result = await handle_slash_command(req.command, session_id, memory)
        return {"session_id": session_id, "command": req.command, **result}

    # -- models & agents -----------------------------------------------------
    @app.get("/api/models")
    async def list_models():
        """List models for the *configured* provider only.

        Built-in catalogs are filtered by provider. Live discovery hits the
        configured base_url so DeepSeek never lists Claude, etc.
        """
        cfg = load_config()
        configured_provider = (
            (runtime._llm_provider if runtime is not None else None)
            or cfg.get("llm_provider")
            or os.environ.get("REMEDY_LLM_PROVIDER")
            or "openai"
        )
        configured_id = (
            (runtime._llm_model if runtime is not None else None)
            or cfg.get("llm_model")
            or os.environ.get("REMEDY_LLM_MODEL")
            or ""
        )
        base_url = (
            (runtime._llm_base_url if runtime is not None else None)
            or cfg.get("llm_base_url")
            or os.environ.get("REMEDY_LLM_BASE_URL")
            or ""
        )

        # Prefer live runtime / disk. Normalize is response-only (no GET writes).
        configured_provider = str(configured_provider or "openai").lower()
        configured_id = str(configured_id or "")
        base_url = str(base_url or "")
        # Soft-normalize for closed providers only when shaping default id display
        # — never persist from GET.
        _np, _nm, _nu = normalize_llm_settings(configured_provider, configured_id, base_url)
        if configured_provider not in ("openrouter", "custom", "ollama"):
            configured_provider, configured_id, base_url = _np, _nm, _nu
        elif not base_url:
            base_url = _nu
        if not configured_id:
            configured_id = _nm

        catalog = catalog_models_for_provider(configured_provider)
        api_key = ""
        if runtime is not None:
            api_key = getattr(runtime, "_llm_api_key", "") or ""
        if not api_key:
            api_key = str(cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or "")

        # Short-lived discovery cache (process-local) to avoid latency on every UI refresh.
        cache_key = f"{configured_provider}|{base_url}|{bool(api_key)}"
        cache = getattr(app.state, "_model_discovery_cache", None)
        if cache is None:
            app.state._model_discovery_cache = {}
            cache = app.state._model_discovery_cache
        now = time.time()
        cached = cache.get(cache_key)
        from_cache = bool(cached and (now - cached[0]) < 30)
        if from_cache:
            discovered = list(cached[1])
        else:
            discovered = []

        verify_ssl = not _is_local_url(base_url)

        # OpenAI-compatible /models (DeepSeek, OpenAI, Ollama /v1, OpenRouter, …)
        # Skip Anthropic here — its Messages API is not OpenAI /models compatible.
        if not from_cache and configured_provider != "anthropic" and base_url:
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
                    models_url = base_url.rstrip("/") + "/models"
                    headers: dict[str, str] = {}
                    if api_key and api_key != "local":
                        headers["Authorization"] = f"Bearer {api_key}"
                    async with session.get(models_url, headers=headers, ssl=verify_ssl) as resp:
                        if resp.ok:
                            body = await resp.json()
                            for m in body.get("data", []):
                                mid = m.get("id", m.get("name", ""))
                                if not mid:
                                    continue
                                discovered.append(
                                    {
                                        "id": mid,
                                        "name": mid,
                                        "provider": configured_provider,
                                        "default": False,
                                    }
                                )
            except Exception as exc:
                logger.debug("Model discovery failed for %s: %s", base_url, exc)

        # Ollama native tags API
        if not from_cache and (configured_provider == "ollama" or "11434" in (base_url or "")):
            try:
                ollama_url = base_url.rstrip("/").removesuffix("/v1") + "/api/tags"
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                    async with session.get(ollama_url) as resp:
                        if resp.ok:
                            body = await resp.json()
                            for m in body.get("models", []):
                                mid = m.get("name", "")
                                if not mid:
                                    continue
                                name = mid.rstrip(":latest") if mid.endswith(":latest") else mid
                                if not any(d["id"] == name for d in discovered):
                                    discovered.append(
                                        {
                                            "id": name,
                                            "name": name,
                                            "provider": "ollama",
                                            "default": False,
                                        }
                                    )
            except Exception as exc:
                logger.debug("Ollama discovery failed: %s", exc)

        if not from_cache:
            cache[cache_key] = (now, list(discovered))

        # Merge: discovered first, then provider catalog only (never other providers).
        # For openrouter/custom keep full discovered set; for closed catalogs prefer
        # catalog + discovered but never inject foreign builtins.
        seen: set[str] = set()
        merged: list[dict] = []
        for m in discovered + catalog:
            mid = m["id"]
            if mid in seen:
                continue
            # On closed providers, drop discovered ids that clearly belong elsewhere
            if configured_provider not in ("openrouter", "custom", "ollama"):
                from remedy.interfaces.config import infer_provider_from_model

                owner = infer_provider_from_model(mid)
                if owner and owner != configured_provider:
                    continue
            seen.add(mid)
            merged.append(
                {
                    "id": mid,
                    "name": m.get("name", mid),
                    "provider": configured_provider,
                    "default": False,
                }
            )

        if not merged:
            merged = catalog_models_for_provider(configured_provider) or [
                {
                    "id": configured_id or "default",
                    "name": configured_id or "default",
                    "provider": configured_provider,
                    "default": True,
                }
            ]

        if configured_id and not any(m["id"] == configured_id for m in merged):
            merged.insert(
                0,
                {
                    "id": configured_id,
                    "name": configured_id,
                    "provider": configured_provider,
                    "default": True,
                },
            )

        default_id = configured_id or merged[0]["id"]
        for m in merged:
            m["default"] = m["id"] == default_id

        return {
            "models": merged,
            "default": default_id,
            "provider": configured_provider,
            "base_url": base_url,
        }

    @app.get("/api/agents")
    async def list_agents():
        return {"agents": _BUILTIN_AGENTS}

    # -- custom commands (markdown-based, ~/.remedy/commands/) ----------------
    @app.get("/api/commands/custom")
    async def list_custom_commands():
        cmd_dir = Path.home() / ".remedy" / "commands"
        if not cmd_dir.exists():
            return {"commands": []}
        commands: list[dict] = []
        for f in sorted(cmd_dir.glob("*.md")):
            name = f.stem
            desc = ""
            # try to read YAML frontmatter
            content = f.read_text(encoding="utf-8", errors="replace")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if isinstance(fm, dict):
                            name = fm.get("description", name)
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            commands.append({"name": name, "description": desc, "file": str(f)})
        return {"commands": commands}

    @app.get("/api/commands/custom/{name}")
    async def get_custom_command(name: str):
        cmd_dir = Path.home() / ".remedy" / "commands"
        path = safe_path(cmd_dir, name + ".md")
        if not path or not path.exists():
            raise HTTPException(404, f"Command '{name}' not found")
        return {"content": path.read_text(encoding="utf-8", errors="replace")}

    # -- custom agents (markdown-based, ~/.remedy/agents/) -------------------
    @app.get("/api/agents/custom")
    async def list_custom_agents():
        agent_dir = Path.home() / ".remedy" / "agents"
        if not agent_dir.exists():
            return {"agents": []}
        agents: list[dict] = []
        for f in sorted(agent_dir.glob("*.md")):
            name = f.stem
            desc = ""
            content = f.read_text(encoding="utf-8", errors="replace")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if isinstance(fm, dict):
                            name = fm.get("name", name)
                            desc = fm.get("description", "")
                    except Exception:
                        pass
            agents.append({"name": name, "description": desc, "file": str(f)})
        return {"agents": agents}

    @app.get("/api/agents/custom/{name}")
    async def get_custom_agent(name: str):
        agent_dir = Path.home() / ".remedy" / "agents"
        path = safe_path(agent_dir, name + ".md")
        if not path or not path.exists():
            raise HTTPException(404, f"Agent '{name}' not found")
        return {"content": path.read_text(encoding="utf-8", errors="replace")}

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

    # -- file search (project-jailed) ----------------------------------------
    def _files_base(session_id: str | None = None) -> Path:
        """Workspace root: session project_path > config project_path > env > cwd."""
        from remedy.core.workspace import (
            default_project_from_config,
            ensure_project_dir,
            resolve_project_path,
        )

        cfg = load_config()
        raw: str | None = None
        # Session override (async path sets this via query — sync helpers use config only
        # unless caller passes session path).
        if session_id and memory is not None:
            # Best-effort: memory methods are async; use config/runtime for sync helper.
            pass
        if runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                return ensure_project_dir(runtime.effective_project_path())
            except Exception:
                pass
        raw = (
            cfg.get("project_path")
            or os.environ.get("REMEDY_PROJECT_PATH")
            or os.environ.get("REMEDY_FILES_ROOT")
            or None
        )
        try:
            return ensure_project_dir(resolve_project_path(raw))
        except Exception:
            return default_project_from_config(cfg)

    def _resolve_jailed(path: str, base: Path) -> Path:
        """Resolve path under base; reject traversal."""
        from remedy.core.workspace import jail_path

        if path in (".", "", None):
            return base
        try:
            return jail_path(path, base)
        except SecurityError:
            candidate = Path(path).expanduser().resolve()
            candidate.relative_to(base)
            return candidate

    @app.get("/api/workspace")
    async def get_workspace(session_id: str | None = Query(default=None)):
        """Return the active project/workspace root for UI and tools."""
        from remedy.core.workspace import ensure_project_dir, list_workspace_entries, resolve_project_path

        root: Path | None = None
        source = "cwd"
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                root = resolve_project_path(sess.project_path)
                source = "session"
        if root is None and runtime is not None and hasattr(runtime, "effective_project_path"):
            try:
                root = runtime.effective_project_path()
                source = "runtime"
            except Exception:
                root = None
        if root is None:
            root = _files_base()
            source = "config"
        try:
            root = ensure_project_dir(root)
        except Exception:
            pass
        return {
            "project_path": str(root),
            "source": source,
            "entries": list_workspace_entries(root),
        }

    @app.get("/api/files")
    async def list_files(
        path: str = Query(default="."),
        session_id: str | None = Query(default=None),
    ):
        """List files in a directory for @file autocompletion (jailed to project)."""
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                from remedy.core.workspace import ensure_project_dir, resolve_project_path

                try:
                    base = ensure_project_dir(resolve_project_path(sess.project_path))
                except Exception:
                    base = _files_base()
            else:
                base = _files_base()
        else:
            base = _files_base()
        try:
            root = _resolve_jailed(path, base)
        except (SecurityError, ValueError):
            return {"files": [], "path": path, "error": "path outside allowed directory", "root": str(base)}
        try:
            if not root.exists():
                return {"files": [], "path": path, "root": str(base)}
            entries = []
            for p in sorted(root.iterdir()):
                if p.name.startswith(".") and p.name != ".":
                    continue
                try:
                    rel = str(p.relative_to(base))
                except ValueError:
                    continue
                entries.append({
                    "name": p.name,
                    "path": rel,
                    "is_dir": p.is_dir(),
                })
            return {
                "files": entries[:200],
                "path": str(root.relative_to(base) if root != base else "."),
                "root": str(base),
            }
        except Exception:
            return {"files": [], "path": path, "root": str(base)}

    @app.get("/api/files/search")
    async def search_files(
        query: str = Query(..., min_length=1),
        session_id: str | None = Query(default=None),
    ):
        """Search the project directory tree for matching files."""
        if session_id and memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess and sess.project_path:
                from remedy.core.workspace import ensure_project_dir, resolve_project_path

                try:
                    base = ensure_project_dir(resolve_project_path(sess.project_path))
                except Exception:
                    base = _files_base()
            else:
                base = _files_base()
        else:
            base = _files_base()
        # Prevent glob injection / path escapes via query
        safe_query = query.replace("/", "").replace("\\", "").replace("..", "")
        if not safe_query:
            return {"query": query, "results": [], "root": str(base)}
        try:
            results = []
            for p in base.rglob(f"*{safe_query}*"):
                if ".git" in p.parts or "__pycache__" in p.parts or "node_modules" in p.parts:
                    continue
                if p.name.startswith("."):
                    continue
                try:
                    rel = str(p.relative_to(base))
                except ValueError:
                    continue
                results.append({
                    "name": p.name,
                    "path": rel,
                    "is_dir": p.is_dir(),
                })
                if len(results) >= 50:
                    break
            return {
                "query": query,
                "results": sorted(results, key=lambda r: len(r["path"])),
                "root": str(base),
            }
        except Exception:
            return {"query": query, "results": [], "root": str(base)}

    # -- message edit / undo (user messages only) -----------------------------
    @app.post("/api/sessions/{session_id}/messages/{msg_id}/edit")
    async def edit_from_message(session_id: str, msg_id: str):
        """Begin edit-and-resend: soft-delete this user message and everything after.

        Returns the original user text so the client can load it into the composer.
        """
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        msg = await memory.get_chat_message(msg_id)
        if msg is None or msg.session_id != session_id:
            raise HTTPException(404, "Message not found")
        if msg.role != ChatMessageRole.USER:
            raise HTTPException(
                400,
                "Only user messages can be edited. Use Edit on your message to revise and resend.",
            )
        if msg.reverted:
            raise HTTPException(400, "Message already reverted")
        count = await memory.revert_from(session_id, msg_id)
        return {
            "status": "ready_to_edit",
            "msg_id": msg_id,
            "content": msg.content,
            "reverted_count": count,
        }

    @app.post("/api/sessions/{session_id}/messages/{msg_id}/revert")
    async def revert_message(session_id: str, msg_id: str):
        """Legacy alias → edit-from (user messages only, cascade to later msgs)."""
        return await edit_from_message(session_id, msg_id)

    # -- session export -------------------------------------------------------
    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        session = await memory.get_chat_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        messages = await memory.get_chat_messages(session_id, limit=10000)
        session_title = getattr(session, "title", "Session") or "Session"
        lines = [f"# {session_title}", "", f"**Session ID:** `{session_id}`", f"**Messages:** {len(messages)}", ""]
        for m in messages:
            role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "user")
            content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
            created = getattr(m, "created_at", None) or (m.get("created_at") if isinstance(m, dict) else "")
            agent = getattr(m, "agent", None) or (m.get("agent") if isinstance(m, dict) else "")
            model = getattr(m, "model", None) or (m.get("model") if isinstance(m, dict) else "")
            header = f"**{role.capitalize()}**"
            if agent:
                header += f" ({agent})"
            if model:
                header += f" — {model}"
            if created:
                header += f" `{created[:19]}`"
            lines.append(header)
            lines.append("")
            if content:
                lines.append(content)
                lines.append("")
            lines.append("---")
            lines.append("")
        markdown = "\n".join(lines)
        safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in session_title)[:60]
        filename = f"remedy-export-{safe_name}.md"
        return {"markdown": markdown, "filename": filename}

    # -- settings -----------------------------------------------------------
    @app.get("/api/settings")
    async def get_settings():
        cfg = load_config()
        config_path = _find_config_path()
        # First-run: show wizard when needs_first_run_setup says so.
        # setup_completed True (including Skip) → never re-show automatically.
        setup_completed = not needs_first_run_setup(cfg, config_path=config_path)

        # Return configured values; soft-normalize only for response display.
        # Never write disk from GET (avoids races with PUT and Ollama false-heals).
        raw_provider = cfg.get("llm_provider", os.environ.get("REMEDY_LLM_PROVIDER", "openai"))
        raw_model = cfg.get("llm_model", os.environ.get("REMEDY_LLM_MODEL", "gpt-4o-mini"))
        raw_url = cfg.get("llm_base_url", os.environ.get("REMEDY_LLM_BASE_URL", "https://api.openai.com/v1"))
        provider, model, base_url = normalize_llm_settings(raw_provider, raw_model, raw_url)
        # Preserve flexible-provider models (ollama/custom/openrouter) as stored.
        if str(raw_provider or "").lower() in ("ollama", "custom", "openrouter"):
            provider = str(raw_provider or provider).lower()
            if raw_model:
                model = str(raw_model)
            if raw_url:
                base_url = str(raw_url)

        runtime_key = ""
        if runtime is not None:
            runtime_key = str(getattr(runtime, "_llm_api_key", "") or "")
        key_set = bool(
            cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or runtime_key
        )

        return {
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "llm_api_key_set": key_set,
            "llm_ready": provider_credentials_ready(cfg) or bool(runtime_key),
            "name": cfg.get("name", "Remedy"),
            "persona": cfg.get("persona", "default"),
            "project_path": cfg.get("project_path")
            or (
                str(runtime.effective_project_path())
                if runtime is not None and hasattr(runtime, "effective_project_path")
                else os.getcwd()
            ),
            "version": version,
            "config_exists": config_path is not None,
            "setup_completed": setup_completed,
            "needs_setup": not setup_completed,
            "config_path": str(config_path) if config_path else str(_default_config_path()),
        }

    @app.put("/api/settings")
    async def update_settings(req: SettingsUpdateRequest):
        config_path = _find_config_path()
        if config_path is None:
            config_path = _default_config_path()
            config_path.parent.mkdir(parents=True, exist_ok=True)

        cfg = load_config()
        updates = req.model_dump(exclude_none=True)

        if "llm_api_key" in updates and not updates["llm_api_key"]:
            del updates["llm_api_key"]

        # Merge then normalize provider/model/url so cross-provider combos
        # (e.g. deepseek + claude-3-haiku) cannot be persisted.
        merged = {**cfg, **updates}
        provider, model, base_url = normalize_llm_settings(
            merged.get("llm_provider"),
            merged.get("llm_model"),
            merged.get("llm_base_url"),
        )
        updates["llm_provider"] = provider
        updates["llm_model"] = model
        updates["llm_base_url"] = base_url

        # Normalize project_path to an absolute directory when provided.
        if "project_path" in updates and updates["project_path"] is not None:
            from remedy.core.workspace import ensure_project_dir, resolve_project_path

            raw_pp = str(updates["project_path"]).strip()
            if raw_pp and raw_pp not in (".", "./"):
                try:
                    updates["project_path"] = str(
                        ensure_project_dir(resolve_project_path(raw_pp))
                    )
                except Exception:
                    updates["project_path"] = str(resolve_project_path(raw_pp))
            else:
                # Explicit clear → store empty so sessions fall back to cwd
                updates["project_path"] = ""

        cfg.update(updates)
        _write_config(config_path, cfg)

        # Invalidate model discovery cache after provider/url changes.
        cache = getattr(app.state, "_model_discovery_cache", None)
        if isinstance(cache, dict):
            cache.clear()

        # Hot-reload live agent so the next chat uses the new endpoint/model/persona/project.
        api_key_for_runtime = updates.get("llm_api_key")
        _apply_llm_to_runtime(
            runtime,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key_for_runtime,
            persona=updates.get("persona"),
            name=updates.get("name"),
            project_path=updates.get("project_path", cfg.get("project_path")),
        )

        changes = list(updates.keys())
        return {
            "status": "saved",
            "changes": changes,
            "config_path": str(config_path),
            "llm_provider": provider,
            "llm_model": model,
            "llm_base_url": base_url,
            "persona": cfg.get("persona"),
            "project_path": cfg.get("project_path"),
        }

    # -- updates ------------------------------------------------------------
    @app.get("/api/updates/check")
    async def check_updates():
        current = version
        latest_python = None
        latest_desktop = None
        release_url = None
        error = None

        try:
            import json as _json
            import urllib.request as _urllib

            req = _urllib.Request(
                "https://pypi.org/pypi/remedy-ai/json",
                headers={"Accept": "application/json"},
            )
            with _urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode())
                latest_python = data["info"]["version"]
        except Exception as e:
            error = f"PyPI check failed: {e}"

        try:
            import json as _json
            import urllib.request as _urllib

            req = _urllib.Request(
                "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json",
                headers={"Accept": "application/json"},
            )
            with _urllib.request.urlopen(req, timeout=8) as resp:
                data = _json.loads(resp.read().decode())
                latest_desktop = data.get("version")
                release_url = data.get("url")
        except Exception:
            pass

        update_available = False
        if latest_python:
            from remedy.interfaces.updater import _parse_version
            if _parse_version(latest_python) > _parse_version(current):
                update_available = True
        if latest_desktop:
            from remedy.interfaces.updater import _parse_version
            if _parse_version(latest_desktop) > _parse_version(current):
                update_available = True

        return {
            "current_version": current,
            "latest_python": latest_python,
            "latest_desktop": latest_desktop,
            "release_url": release_url,
            "update_available": update_available,
            "error": error,
        }

    # -- OpenAPI schema export -----------------------------------------------
    @app.get("/api/openapi.yaml", include_in_schema=False)
    async def export_openapi_yaml():
        return Response(
            content=yaml_schema(app),
            media_type="application/yaml",
        )

    @app.get("/api/openapi.json", include_in_schema=False)
    async def export_openapi_json():
        return Response(
            content=json.dumps(app.openapi(), indent=2),
            media_type="application/json",
        )

    # -- project init scanner -------------------------------------------------
    @app.post("/api/projects/scan")
    async def scan_project(path: str = Query(default=".")):
        target = Path(path).resolve()
        if not target.exists():
            raise HTTPException(404, f"Path not found: {path}")
        if not target.is_dir():
            target = target.parent

        files: dict[str, list[str]] = {"python": [], "javascript": [], "typescript": [], "rust": [], "other": []}
        exts_map = {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
            ".rs": "rust", ".c": "other", ".cpp": "other", ".h": "other",
            ".json": "other", ".yaml": "other", ".yml": "other", ".toml": "other",
            ".md": "other", ".txt": "other", ".css": "other", ".html": "other",
        }
        ignored = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".next", "target"}
        for f in target.rglob("*"):
            if f.is_file() and not any(p in ignored for p in f.parts):
                ext = f.suffix.lower()
                cat = exts_map.get(ext, "other")
                rel = str(f.relative_to(target))
                files[cat].append(rel)
                if len(files[cat]) >= 100:
                    continue

        summary = {
            "path": str(target),
            "file_counts": {k: len(v) for k, v in files.items()},
            "top_files": files,
            "python_deps": "",
            "js_deps": "",
        }

        # try reading pyproject.toml or package.json for deps
        pp = target / "pyproject.toml"
        if pp.exists():
            summary["python_deps"] = pp.read_text(encoding="utf-8", errors="replace")[:2000]
        pj = target / "package.json"
        if pj.exists():
            summary["js_deps"] = pj.read_text(encoding="utf-8", errors="replace")[:2000]

        return summary

    # -- dashboard (simple HTML) ---------------------------------------------
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        html = DASHBOARD_HTML.replace("{{version}}", version)
        return Response(content=html, media_type="text/html")

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
