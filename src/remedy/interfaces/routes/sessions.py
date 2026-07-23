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


def register_sessions_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
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

    # -- session attachments (drag-drop / paste / picker) --------------------
    @app.post("/api/sessions/{session_id}/attachments")
    async def upload_attachment_json(session_id: str, req: AttachmentUploadRequest):
        """Store a dropped/pasted file (JSON + base64) and return a path ref.

        Prefer this over multipart so frozen desktop sidecars do not need
        python-multipart.
        """
        import base64

        from remedy.interfaces.attachments import MAX_ATTACHMENT_BYTES, save_upload

        try:
            raw = base64.b64decode(req.data_base64, validate=False)
        except Exception as e:
            raise HTTPException(400, f"Invalid base64 payload: {e}") from e
        if not raw:
            raise HTTPException(400, "Empty file")
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(
                413,
                f"File too large (max {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB)",
            )
        home = None
        try:
            home = load_config().get("home_dir")
        except Exception:
            pass
        try:
            meta = save_upload(
                session_id=session_id,
                filename=req.filename or "upload.bin",
                data=raw,
                content_type=req.content_type,
                home_dir=home,
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return meta

    @app.get("/api/sessions/{session_id}/attachments/{filename}")
    async def get_attachment(session_id: str, filename: str):
        from remedy.interfaces.attachments import session_attachments_dir

        home = None
        try:
            home = load_config().get("home_dir")
        except Exception:
            pass
        directory = session_attachments_dir(session_id, home)
        # Prevent path traversal
        safe = Path(filename).name
        path = (directory / safe).resolve()
        if not str(path).startswith(str(directory.resolve())):
            raise HTTPException(400, "Invalid path")
        if not path.is_file():
            raise HTTPException(404, "Attachment not found")
        return FileResponse(path, filename=safe)

    # -- SSE streaming (structured events) -----------------------------------
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        request_id = str(uuid4())
        att_dicts = [a.model_dump() for a in (req.attachments or [])]
        user_text = (req.message or "").strip()
        if not user_text and not att_dicts:
            raise HTTPException(400, "Message or attachment required")

        # Expand display content for chat history (paths + text snippets).
        from remedy.interfaces.attachments import (
            build_attachment_prompt_block,
            inject_text_file_snippets,
        )

        display_content = user_text
        if att_dicts:
            display_content = (
                f"{user_text}{build_attachment_prompt_block(att_dicts)}"
                if user_text
                else build_attachment_prompt_block(att_dicts).lstrip()
            )
            # Keep history readable but not huge — skip full snippets for images-only.
            if any(a.get("is_text") for a in att_dicts):
                display_content = display_content + inject_text_file_snippets(att_dicts)

        if memory:
            from remedy.models import ChatMessage, ChatSession
            existing = await memory.get_chat_session(session_id)
            if existing is None:
                default_proj = load_config().get("project_path")
                title_src = user_text or (
                    att_dicts[0].get("name") if att_dicts else "Attachments"
                )
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=str(title_src)[:60],
                    model=req.model,
                    agent=req.agent,
                    project_path=default_proj,
                ))
            elif req.model and req.model != existing.model:
                await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=display_content,
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
                    user_text or "(see attached files)",
                    session_id=session_id,
                    model=req.model,
                    attachments=att_dicts,
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

