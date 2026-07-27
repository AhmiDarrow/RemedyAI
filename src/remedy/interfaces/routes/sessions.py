"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from remedy.interfaces.api_models import (
    AttachmentUploadRequest,
    BulkSessionProjectRequest,
    ChatRequest,
    CreateSessionRequest,
    SendMessageRequest,
    SessionLlmRequest,
    UpdateSessionRequest,
)
from remedy.interfaces.api_support import (
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    load_config,
    sse_headers,
)
from remedy.models import (
    ChatMessageRole,
)

logger = logging.getLogger(__name__)


def register_sessions_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register routes (closes over runtime/gateway/memory)."""
    # -- chat sessions -------------------------------------------------------
    @app.get("/api/sessions")
    async def list_chat_sessions(
        limit: int = Query(default=100, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        if memory is None:
            return {"sessions": [], "offset": offset, "limit": limit, "has_more": False}
        from remedy.interfaces.session_public import session_to_public

        sessions = await memory.list_chat_sessions(limit=limit, offset=offset)
        has_more = len(sessions) >= limit
        return {
            "sessions": [session_to_public(s) for s in sessions],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        }

    def _default_project_path() -> str | None:
        """Resolved default workspace from config / runtime."""
        from remedy.core.workspace import default_project_from_config

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

        from remedy.core.workspace import (
            ensure_project_dir,
            is_unset_project_path,
            resolve_project_path,
        )
        from remedy.models import ChatSession as CS

        # Omit project_path (None) → inherit global settings project if set.
        # Explicit "" / "." → no-project session (root / full access) — default for New Session.
        # New Project folder is first-run only (config init), never forced onto every session.
        if req.project_path is not None:
            raw_project = req.project_path
        else:
            raw_project = load_config().get("project_path")
        project_path = None
        if not is_unset_project_path(raw_project):
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
            "llm_provider": getattr(saved, "llm_provider", None),
            "message_count": saved.message_count,
            "origin_channel": getattr(saved, "origin_channel", None),
            "external_chat_id": getattr(saved, "external_chat_id", None),
            "external_user": getattr(saved, "external_user", None),
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
            "llm_provider": getattr(session, "llm_provider", None),
            "message_count": session.message_count,
            "origin_channel": getattr(session, "origin_channel", None),
            "external_chat_id": getattr(session, "external_chat_id", None),
            "external_user": getattr(session, "external_user", None),
            "created_at": session.created_at.isoformat() if session.created_at else None,
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.patch("/api/sessions/{session_id}")
    async def update_chat_session(session_id: str, req: UpdateSessionRequest):
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        # exclude_unset: only fields the client sent (so project_path can be cleared)
        fields = {
            k: v
            for k, v in req.model_dump(exclude_unset=True).items()
            if v is not None or k == "project_path"
        }
        if "project_path" in fields:
            from remedy.core.workspace import (
                ensure_project_dir,
                is_unset_project_path,
                resolve_project_path,
            )

            raw = fields["project_path"]
            if is_unset_project_path(raw):
                fields["project_path"] = None
            else:
                try:
                    fields["project_path"] = str(
                        ensure_project_dir(resolve_project_path(str(raw)))
                    )
                except Exception:
                    fields["project_path"] = str(resolve_project_path(str(raw)))
        session = await memory.update_chat_session(session_id, **fields)
        if session is None:
            raise HTTPException(404, "Session not found")
        return {
            "id": session.id,
            "title": session.title,
            "model": session.model,
            "agent": session.agent,
            "project_path": session.project_path,
            "llm_provider": getattr(session, "llm_provider", None),
            "updated_at": session.updated_at.isoformat() if session.updated_at else None,
        }

    @app.post("/api/sessions/bulk-project")
    async def bulk_set_session_project(req: BulkSessionProjectRequest):
        """Attach many sessions to one project folder (or clear → no project)."""
        if memory is None:
            raise HTTPException(503, "Memory store not available")
        from remedy.core.workspace import (
            ensure_project_dir,
            is_unset_project_path,
            resolve_project_path,
        )

        ids = [str(i).strip() for i in (req.session_ids or []) if str(i).strip()]
        if not ids:
            raise HTTPException(400, "session_ids required")
        if len(ids) > 200:
            raise HTTPException(400, "At most 200 sessions per bulk move")

        project_path: str | None = None
        if not is_unset_project_path(req.project_path):
            try:
                project_path = str(
                    ensure_project_dir(resolve_project_path(str(req.project_path)))
                )
            except Exception:
                project_path = str(resolve_project_path(str(req.project_path)))

        updated: list[str] = []
        missing: list[str] = []
        for sid in ids:
            sess = await memory.update_chat_session(sid, project_path=project_path)
            if sess is None:
                missing.append(sid)
            else:
                updated.append(sid)
        return {
            "status": "ok",
            "project_path": project_path,
            "updated": updated,
            "missing": missing,
            "count": len(updated),
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
        """Cooperatively stop in-flight generation for this session."""
        from remedy.core.turn_context import abort_session as _abort_turn

        n = _abort_turn(session_id)
        if runtime is not None:
            # Clear global streaming flag when this session was the active one.
            with contextlib.suppress(Exception):
                if str(getattr(runtime, "_session_id", "") or "") == str(session_id):
                    runtime._streaming = False  # type: ignore[attr-defined]
        return {"status": "aborted", "session_id": session_id, "notified": n}

    @app.put("/api/sessions/{session_id}/llm")
    async def set_session_llm(session_id: str, req: SessionLlmRequest):
        """Switch provider/model for this session (NanoToken remeasure on apply).

        Blocks while the runtime is streaming when detectable. Optionally
        persists as global default (make_default=true).
        """
        from remedy.interfaces.api_support import (
            _apply_llm_to_runtime,
            _default_config_path,
            _find_config_path,
            _write_config,
        )
        from remedy.interfaces.config import normalize_llm_settings

        if runtime is not None and getattr(runtime, "_streaming", False):
            raise HTTPException(
                409,
                "Stop generation before switching provider/model",
            )

        cfg = load_config()
        provider, model, base_url = normalize_llm_settings(
            req.provider,
            req.model or cfg.get("llm_model"),
            cfg.get("llm_base_url") if str(req.provider).lower() == str(cfg.get("llm_provider") or "").lower() else None,
        )
        # Load messages for remeasure
        messages: list[dict] = []
        if memory is not None:
            try:
                msgs = await memory.get_chat_messages(session_id, limit=80, offset=0)
                for m in msgs:
                    messages.append(
                        {
                            "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                            "content": m.content or "",
                        }
                    )
            except Exception:
                messages = []

        if runtime is not None:
            with contextlib.suppress(Exception):
                runtime._session_id = session_id
            if messages:
                with contextlib.suppress(Exception):
                    runtime._last_send_messages = messages

        # Resolve key for new provider
        api_key = None
        try:
            from remedy.interfaces.config import resolve_provider_api_key

            api_key = resolve_provider_api_key(cfg, provider)
        except Exception:
            api_key = None
        if not api_key and provider in ("ollama", "demo"):
            api_key = "local"

        _apply_llm_to_runtime(
            runtime,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )

        # Persist last_model_by_provider always; global default when asked
        config_path = _find_config_path() or _default_config_path()
        last_by = dict(cfg.get("last_model_by_provider") or {})
        last_by[provider] = model
        cfg["last_model_by_provider"] = last_by
        if req.make_default:
            cfg["llm_provider"] = provider
            cfg["llm_model"] = model
            cfg["llm_base_url"] = base_url
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            _write_config(config_path, cfg)
        except Exception as exc:
            logger.warning("session llm config write failed: %s", exc)

        # Persist per-session provider/model (tabs independent)
        if memory is not None:
            with contextlib.suppress(Exception):
                await memory.update_chat_session(
                    session_id, model=model, llm_provider=provider
                )

        remeasure = None
        with contextlib.suppress(Exception):
            from remedy.nanoswarm.token_nanobot import get_token_nanobot

            remeasure = get_token_nanobot().last_remeasure(session_id)

        window = None
        with contextlib.suppress(Exception):
            from remedy.nanoswarm.token_nanobot import resolve_context_window

            window = resolve_context_window(provider, model)

        return {
            "status": "ok",
            "session_id": session_id,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "make_default": req.make_default,
            "remeasure": remeasure,
            "context_window": window,
            "toast": (
                f"Now using {provider} · {model}"
                + (f" · window {window}" if window else "")
                + (" · remeasured history" if remeasure else "")
            ),
        }

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

        # Prefer per-session provider (status-bar switch) over global config.
        sess_provider = None
        sess_model = req.model
        if memory:
            with contextlib.suppress(Exception):
                ex = await memory.get_chat_session(session_id)
                if ex is not None:
                    sess_provider = getattr(ex, "llm_provider", None)
                    if not sess_model:
                        sess_model = getattr(ex, "model", None)

        # Always re-sync credentials from disk (wizard/settings may have just saved).
        _sync_runtime_llm_from_config(
            runtime,
            model_override=sess_model,
            provider_override=sess_provider,
        )

        from remedy.core.metrics import default_registry

        start = time.perf_counter()
        response_text = ""
        async for token in runtime.stream_response(
            req.message,
            session_id=session_id,
            model=sess_model,
            plan_mode=bool(getattr(req, "plan_mode", False)),
        ):
            # Keep user-visible text only (tool lifecycle events are @@-prefixed).
            if isinstance(token, str) and token.startswith("@@"):
                continue
            response_text += token
        elapsed_s = time.perf_counter() - start
        elapsed = elapsed_s * 1000
        default_registry.counter(
            "remedy_chat_requests_total", path="session_message"
        ).inc()
        default_registry.histogram(
            "remedy_chat_duration_seconds", path="session_message"
        ).observe(elapsed_s)

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
        with contextlib.suppress(Exception):
            home = load_config().get("home_dir")
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
        with contextlib.suppress(Exception):
            home = load_config().get("home_dir")
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

            def _title_from_prompt(text: str, *, max_len: int = 52) -> str:
                t = " ".join((text or "").strip().split())
                if not t:
                    return "New Session"
                # Drop attachment display blocks from title.
                if "📎" in t:
                    t = t.split("📎", 1)[0].strip() or t
                if t.startswith("(") and "see attached" in t.lower():
                    name = (att_dicts[0].get("name") if att_dicts else "") or "Attachments"
                    t = str(name)
                if len(t) > max_len:
                    t = t[: max_len - 1].rstrip() + "…"
                return t or "New Session"

            existing = await memory.get_chat_session(session_id)
            if existing is None:
                default_proj = load_config().get("project_path")
                title_src = user_text or (
                    att_dicts[0].get("name") if att_dicts else "Attachments"
                )
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=_title_from_prompt(str(title_src)),
                    model=req.model,
                    agent=req.agent,
                    project_path=default_proj,
                ))
            else:
                # Auto-name placeholder sessions from the first real prompt.
                cur_title = (existing.title or "").strip()
                placeholder = (
                    not cur_title
                    or cur_title.lower() in ("new session", "new chat", "untitled")
                )
                if placeholder and (user_text or att_dicts):
                    await memory.update_chat_session(
                        session_id,
                        title=_title_from_prompt(
                            user_text
                            or str(att_dicts[0].get("name") if att_dicts else "Attachments")
                        ),
                    )
                if req.model and req.model != existing.model:
                    await memory.update_chat_session(session_id, model=req.model)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=display_content,
                model=req.model,
                agent=req.agent,
            ))

        # Prefer per-session provider (status-bar switch) over global config.
        # Bug: only model_override was applied → DeepSeek API + grok-4.5 model name.
        sess_provider = None
        sess_model = req.model
        if memory:
            with contextlib.suppress(Exception):
                ex = await memory.get_chat_session(session_id)
                if ex is not None:
                    sess_provider = getattr(ex, "llm_provider", None)
                    if not sess_model:
                        sess_model = getattr(ex, "model", None)

        # Always re-sync credentials from disk (first-run wizard / settings).
        api_key = _sync_runtime_llm_from_config(
            runtime,
            model_override=sess_model,
            provider_override=sess_provider,
        )

        async def event_stream():
            from remedy.core.metrics import default_registry

            t0 = time.perf_counter()
            status = "ok"
            yield (
                f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
            )

            try:
                full_response = ""
                full_thinking = ""
                collected_tool_calls: list[dict] = []
                collected_tool_results: list[dict] = []
                usage_acc: dict | None = None
                if not api_key:
                    status = "no_key"
                    msg = (
                        "No LLM API key configured. Complete first-run setup or open Settings, "
                        "set your provider API key, and Save — then try again."
                    )
                    yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                    return

                async for token in runtime.stream_response(
                    user_text or "(see attached files)",
                    session_id=session_id,
                    model=sess_model,
                    attachments=att_dicts,
                    plan_mode=bool(getattr(req, "plan_mode", False)),
                ):
                    if isinstance(token, str) and token.startswith("@@aborted"):
                        status = "aborted"
                        yield (
                            "event: error\ndata: "
                            + json.dumps({"type": "error", "message": "Generation stopped"})
                            + "\n\n"
                        )
                        break
                    if token.startswith("@@tool_call:"):
                        raw = token[len("@@tool_call:") :]
                        tool_name = raw
                        args: dict = {}
                        try:
                            if raw.strip().startswith("{"):
                                obj = json.loads(raw)
                                tool_name = str(obj.get("name") or "tool")
                                a = obj.get("args")
                                if isinstance(a, dict):
                                    args = a
                        except Exception:
                            tool_name = raw.split("|", 1)[0].strip() or "tool"
                        collected_tool_calls.append({"name": tool_name, "args": args})
                        yield (
                            "event: tool_call\ndata: "
                            + json.dumps(
                                {
                                    "type": "tool_call",
                                    "name": tool_name,
                                    "args": args,
                                },
                                default=str,
                            )
                            + "\n\n"
                        )
                    elif token.startswith("@@tool_result:"):
                        raw = token[len("@@tool_result:") :]
                        tool_name = raw
                        preview = ""
                        ok = True
                        try:
                            if raw.strip().startswith("{"):
                                obj = json.loads(raw)
                                tool_name = str(obj.get("name") or "tool")
                                preview = str(obj.get("preview") or "")
                                ok = bool(obj.get("ok", True))
                            else:
                                tool_name = raw.split("|", 1)[0].strip() or "tool"
                        except Exception:
                            tool_name = raw.split("|", 1)[0].strip() or "tool"
                        collected_tool_results.append(
                            {
                                "name": tool_name,
                                "output": preview,
                                "error": None if ok else (preview or "tool failed"),
                            }
                        )
                        yield (
                            "event: tool_result\ndata: "
                            + json.dumps(
                                {
                                    "type": "tool_result",
                                    "name": tool_name,
                                    "preview": preview,
                                    "ok": ok,
                                },
                                default=str,
                            )
                            + "\n\n"
                        )
                    elif token.startswith("@@library_suggest:"):
                        raw = token[len("@@library_suggest:") :].strip()
                        try:
                            payload = json.loads(raw)
                        except Exception:
                            payload = {}
                        if isinstance(payload, dict) and payload.get("id"):
                            yield (
                                "event: library_suggest\ndata: "
                                + json.dumps(
                                    {"type": "library_suggest", **payload},
                                    default=str,
                                )
                                + "\n\n"
                            )
                    elif token.startswith("@@progress:"):
                        # Generic task/job progress for the desktop progress bar.
                        raw = token[len("@@progress:") :]
                        try:
                            payload = json.loads(raw) if raw else {}
                        except Exception:
                            payload = {"label": raw or "Working…"}
                        if not isinstance(payload, dict):
                            payload = {"label": str(payload)}
                        event = {"type": "progress", **payload}
                        yield f"event: progress\ndata: {json.dumps(event)}\n\n"
                    elif token.startswith("@@thinking:"):
                        thought = token[len("@@thinking:") :]
                        if thought:
                            full_thinking += thought
                            yield await _sse_stream_text(thought, event="thinking")
                    elif token.startswith("@@usage:"):
                        raw_u = token[len("@@usage:") :]
                        try:
                            from remedy.core.usage import merge_usage

                            part = json.loads(raw_u) if raw_u else {}
                            if isinstance(part, dict):
                                usage_acc = merge_usage(usage_acc, part)
                                yield (
                                    "event: usage\ndata: "
                                    + json.dumps({"type": "usage", **usage_acc})
                                    + "\n\n"
                                )
                        except Exception:
                            pass
                    elif token.startswith("@@image_markdown:"):
                        # ComfyUI (etc.): image markdown with data-URI — show immediately.
                        md = token[len("@@image_markdown:"):]
                        if md:
                            full_response += ("\n\n" if full_response else "") + md
                            yield await _sse_stream_text(md, event="token")
                    elif token == "@@tool_calls":
                        pass
                    else:
                        # Never stream DSML / fake tool markup into the chat bubble.
                        from remedy.core.react_policy import (
                            looks_like_pseudo_tools,
                            strip_tool_markup,
                        )

                        if looks_like_pseudo_tools(token):
                            cleaned = strip_tool_markup(token)
                            if not cleaned:
                                continue
                            token = cleaned
                        full_response += token
                        yield await _sse_stream_text(token, event="token")

                # Final usage: prefer provider totals; fall back to char estimate.
                final_usage: dict | None = None
                try:
                    from remedy.core.usage import estimate_turn_usage, merge_usage

                    est = estimate_turn_usage(
                        user_text=user_text or "",
                        assistant_text=full_response or "",
                        thinking_text=full_thinking or "",
                        model=req.model or getattr(runtime, "_llm_model", None),
                        provider=getattr(runtime, "_llm_provider", None),
                    )
                    if usage_acc and usage_acc.get("source") == "provider":
                        final_usage = merge_usage(usage_acc)
                    else:
                        final_usage = merge_usage(est, usage_acc)
                    yield (
                        "event: usage\ndata: "
                        + json.dumps({"type": "usage", **final_usage})
                        + "\n\n"
                    )
                except Exception:
                    final_usage = None

                if full_response and memory:
                    tok = None
                    if isinstance(final_usage, dict):
                        tok = int(final_usage.get("total_tokens") or 0) or None
                    await memory.add_chat_message(ChatMessage(
                        session_id=session_id,
                        role=ChatMessageRole.ASSISTANT,
                        content=full_response,
                        thinking=full_thinking.strip() or None,
                        tool_calls=collected_tool_calls,
                        tool_results=collected_tool_results,
                        model=req.model or getattr(runtime, "_llm_model", None),
                        tokens=tok,
                    ))

                done_payload: dict = {"type": "done", "request_id": request_id}
                if isinstance(final_usage, dict):
                    done_payload["usage"] = final_usage
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

            except asyncio.CancelledError:
                status = "cancelled"
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': 'Request cancelled.'})}\n\n"
            except Exception as e:
                status = "error"
                logger.exception("SSE stream error")
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
            finally:
                default_registry.counter(
                    "remedy_chat_requests_total", path="session_stream", status=status
                ).inc()
                default_registry.histogram(
                    "remedy_chat_duration_seconds", path="session_stream"
                ).observe(time.perf_counter() - t0)

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
            from remedy.core.metrics import default_registry

            t0 = time.perf_counter()
            status = "ok"
            yield (
                f"event: start\ndata: {json.dumps({'type': 'start', 'request_id': request_id, 'session_id': session_id})}\n\n"
            )

            try:
                async for token in runtime.stream_response(req.message, session_id=session_id):
                    yield await _sse_stream_text(token, event="token")
            except Exception as e:
                status = "error"
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

            yield f"event: done\ndata: {json.dumps({'type': 'done', 'request_id': request_id})}\n\n"
            default_registry.counter(
                "remedy_chat_requests_total", path="chat_stream", status=status
            ).inc()
            default_registry.histogram(
                "remedy_chat_duration_seconds", path="chat_stream"
            ).observe(time.perf_counter() - t0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )

    from remedy.interfaces.routes.session_events import register_session_event_routes

    register_session_event_routes(app)

