"""REST API server -- FastAPI-based interface for Remedy.

Exposes chat sessions, streaming messages, memory, skills, commands,
models, agents, and webhook endpoints for the desktop and web UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

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


class SendMessageRequest(BaseModel):
    message: str = Field(..., description="User message text")
    model: str | None = None
    agent: str | None = None


class CommandRequest(BaseModel):
    command: str = Field(..., description="Slash command to execute (e.g. /new)")


# -- API factory -------------------------------------------------------------


# -- SSE streaming -----------------------------------------------------------
async def _sse_stream_text(text: str, *, event: str | None = None) -> str:
    """Format a single SSE frame."""
    prefix = f"event: {event}\n" if event else ""
    payload = json.dumps({"text": text})
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
]

_BUILTIN_MODELS: list[dict] = [
    {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "default": True},
    {"id": "gpt-4o", "name": "GPT-4o", "provider": "openai", "default": False},
    {"id": "claude-3-haiku", "name": "Claude 3 Haiku", "provider": "anthropic", "default": False},
    {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "provider": "anthropic", "default": False},
]

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
            lines.append(f"  {s['title']} — {s['message_count']} msg — {s['id'][:8]}")
        return {"text": "Recent sessions:\n" + "\n".join(lines)}

    if stripped in ("/models", "/m"):
        lines = []
        for m in _BUILTIN_MODELS:
            d = " [default]" if m["default"] else ""
            lines.append(f"  {m['name']} ({m['id']}){d}")
        return {"text": "Available models:\n" + "\n".join(lines)}

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

    return {"text": f"Unknown command: {command}\nType /help for available commands."}


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = "0.7.0",
    *,
    api_key: str = "",
) -> FastAPI:
    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
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
        logger.info("%s %s -> %d (%.0fms)",
                     request.method, request.url.path,
                     response.status_code, duration)
        return response

    # -- health / status -----------------------------------------------------
    @app.get("/api/status", response_model=StatusResponse)
    async def get_status():
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

        return StatusResponse(
            version=version,
            uptime=gw_stats.get("uptime", "N/A"),
            gateway=gw_stats,
            memory_entries=mem_count,
            skills_count=skills_count,
            sessions_count=summary_sessions,
            chat_sessions_count=chat_sessions,
        )

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

    @app.post("/api/sessions")
    async def create_chat_session(req: CreateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")

        from remedy.models import ChatSession as CS
        session = CS(
            title=req.title,
            model=req.model,
            agent=req.agent,
            project_path=req.project_path,
        )
        saved = await memory.create_chat_session(session)
        return {
            "id": saved.id,
            "title": saved.title,
            "model": saved.model,
            "created_at": saved.created_at.isoformat() if saved.created_at else None,
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
            "message_count": session.message_count,
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.patch("/api/sessions/{session_id}")
    async def update_chat_session(session_id: str, req: UpdateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        session = await memory.update_chat_session(session_id, **fields)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
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
        if gateway is None:
            raise HTTPException(503, "Gateway not available")

        request_id = str(uuid4())

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=req.message[:60],
                    model=req.model,
                    agent=req.agent,
                ))
            elif req.model and req.model != existing.model:
                await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=req.message,
            ))

        event = GatewayEvent(
            id=uuid4(),
            kind=EventKind.MESSAGE,
            channel=ChannelKind.WEB,
            source_id="web",
            payload={"message": req.message, "request_id": request_id},
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

        return {
            "request_id": request_id,
            "session_id": session_id,
            "response": response_text or "Processed.",
            "processing_time_ms": round(elapsed, 1),
        }

    # -- SSE streaming (structured events) -----------------------------------
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, req: SendMessageRequest):
        if gateway is None:
            raise HTTPException(503, "Gateway not available")

        request_id = str(uuid4())

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=req.message[:60],
                    model=req.model,
                    agent=req.agent,
                ))
            elif req.model and req.model != existing.model:
                await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=req.message,
            ))

        async def event_stream():
            yield f"event: start\ndata: {json.dumps({'request_id': request_id, 'session_id': session_id})}\n\n"

            tokens = []

            try:
                event = GatewayEvent(
                    id=uuid4(),
                    kind=EventKind.MESSAGE,
                    channel=ChannelKind.WEB,
                    source_id="web",
                    payload={"message": req.message, "request_id": request_id},
                    session_id=session_id,
                )

                responses = await gateway.emit(event)
                full_response = ""

                for r in responses:
                    if isinstance(r, str):
                        full_response += r
                        yield await _sse_stream_text(r, event="token")

                if full_response and memory:
                    await memory.add_chat_message(ChatMessage(
                        session_id=session_id,
                        role=ChatMessageRole.ASSISTANT,
                        content=full_response,
                    ))

                yield f"event: done\ndata: {json.dumps({'request_id': request_id})}\n\n"

            except asyncio.CancelledError:
                yield await _sse_stream_text("Request cancelled.", event="error")
            except Exception as e:
                logger.exception("SSE stream error")
                yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )

    # -- legacy chat stream (maintained for backward compatibility) ----------
    @app.post("/api/chat/stream")
    async def chat_stream(req: ChatRequest):
        if gateway is None:
            raise HTTPException(503, "Gateway not available")

        request_id = str(uuid4())
        session_id = req.session_id or str(uuid4())

        async def event_stream():
            yield f"event: start\ndata: {json.dumps({'request_id': request_id, 'session_id': session_id})}\n\n"

            try:
                event = GatewayEvent(
                    id=uuid4(),
                    kind=EventKind.MESSAGE,
                    channel=ChannelKind.WEB,
                    source_id=req.user_id or "anonymous",
                    payload={"message": req.message, "request_id": request_id},
                    session_id=session_id,
                )
                responses = await gateway.emit(event)
                for r in responses:
                    if isinstance(r, str):
                        yield await _sse_stream_text(r, event="token")
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

            yield f"event: done\ndata: {json.dumps({'request_id': request_id})}\n\n"

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
        return {"models": _BUILTIN_MODELS, "default": "gpt-4o-mini"}

    @app.get("/api/agents")
    async def list_agents():
        return {"agents": _BUILTIN_AGENTS}

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

    # -- file search ----------------------------------------------------------
    @app.get("/api/files")
    async def list_files(path: str = Query(default=".")):
        """List files in a directory for @file autocompletion."""
        try:
            root = Path(path).resolve()
            if not root.exists():
                return {"files": [], "path": path}
            entries = []
            for p in sorted(root.iterdir()):
                if p.name.startswith(".") and p.name != ".":
                    continue
                entries.append({
                    "name": p.name,
                    "path": str(p.relative_to(Path.cwd())) if p.is_relative_to(Path.cwd()) else str(p),
                    "is_dir": p.is_dir(),
                })
            return {"files": entries[:200], "path": str(root)}
        except Exception:
            return {"files": [], "path": path}

    @app.get("/api/files/search")
    async def search_files(query: str = Query(..., min_length=1)):
        """Search the current directory tree for matching files."""
        try:
            results = []
            base = Path.cwd()
            for p in base.rglob(f"*{query}*"):
                if ".git" in p.parts or "__pycache__" in p.parts or "node_modules" in p.parts:
                    continue
                if p.name.startswith("."):
                    continue
                results.append({
                    "name": p.name,
                    "path": str(p.relative_to(base)),
                    "is_dir": p.is_dir(),
                })
                if len(results) >= 50:
                    break
            return {"query": query, "results": sorted(results, key=lambda r: len(r["path"]))}
        except Exception:
            return {"query": query, "results": []}

    # -- message revert ---------------------------------------------------------
    @app.post("/api/sessions/{session_id}/messages/{msg_id}/revert")
    async def revert_message(session_id: str, msg_id: str):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        reverted = await memory.revert_message(msg_id)
        if not reverted:
            raise HTTPException(404, "Message not found")
        return {"status": "reverted", "msg_id": msg_id}

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
