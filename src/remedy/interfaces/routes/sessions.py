"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
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

        # Stamp provider+model at create so a second project session does not
        # inherit a null bind and thrash the status bar against the first tab.
        sess_provider = (req.llm_provider or "").strip().lower() or None
        sess_model = (req.model or "").strip() or None
        if sess_model or sess_provider:
            with contextlib.suppress(Exception):
                from remedy.core.session_llm import session_llm_update_fields

                fields = session_llm_update_fields(
                    provider=sess_provider, model=sess_model
                )
                sess_provider = fields.get("llm_provider") or sess_provider
                sess_model = fields.get("model") or sess_model

        session = CS(
            title=req.title,
            model=sess_model or req.model,
            agent=req.agent,
            project_path=project_path,
            llm_provider=sess_provider,
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
        sid = str(session_id or "").strip()
        # Stop in-flight turn + shell children before dropping the row
        with contextlib.suppress(Exception):
            from remedy.core.turn_context import abort_session as _abort_turn

            _abort_turn(sid)
        if runtime is not None:
            with contextlib.suppress(Exception):
                ss = getattr(runtime, "_streaming_sessions", None)
                if isinstance(ss, set):
                    ss.discard(sid)
        deleted = await memory.delete_chat_session(sid)
        if not deleted:
            raise HTTPException(404, "Session not found")
        # Cascade session-scoped disk artifacts (attachments / plans / undo)
        cascade: dict = {}
        with contextlib.suppress(Exception):
            from remedy.core.session_reset import purge_session_disk_artifacts

            home = load_config().get("home_dir")
            cascade = purge_session_disk_artifacts(sid, home)
        with contextlib.suppress(Exception):
            from remedy.memory.middleman import forget_session_middleman

            forget_session_middleman(sid)
        return {
            "status": "deleted",
            "session_id": sid,
            "cascade": cascade,
        }

    @app.post("/api/sessions/{session_id}/abort")
    async def abort_session(session_id: str):
        """Cooperatively stop in-flight generation for this session."""
        from remedy.core.turn_context import abort_session as _abort_turn

        n = _abort_turn(session_id)
        if runtime is not None:
            # Clear global streaming flag when this session was the active one.
            with contextlib.suppress(Exception):
                # Per-session stream set; clear this id only (not all tabs).
                ss = getattr(runtime, "_streaming_sessions", None)
                if isinstance(ss, set):
                    ss.discard(str(session_id))
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

        # Only block if *this* session is streaming — other tabs may run freely.
        if runtime is not None:
            is_busy = False
            if hasattr(runtime, "is_session_streaming"):
                is_busy = bool(runtime.is_session_streaming(session_id))
            else:
                from remedy.core.turn_context import is_session_streaming

                is_busy = is_session_streaming(session_id)
            if is_busy:
                raise HTTPException(
                    409,
                    "Stop generation in this session before switching provider/model",
                )

        cfg = load_config()
        raw_provider = (req.provider or cfg.get("llm_provider") or "openai")
        raw_model = (req.model or cfg.get("llm_model") or "").strip()
        # Fail closed on garbage ids (e.g. not-a-real-model-zzz) before toast/persist.
        try:
            from remedy.interfaces.config import validate_provider_model

            if raw_model:
                validate_provider_model(str(raw_provider), raw_model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

        # Session-only switch: persist the tab bind — do NOT thrash the shared
        # runtime or global config (other tabs may be streaming another provider).
        # make_default=true: also update global defaults + live runtime (Settings).
        if req.make_default:
            _apply_llm_to_runtime(
                runtime,
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )

        # Always remember last model for this provider; global default only when asked
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
        # Cap payload size for list views (stream/export keep full bodies).
        _CONTENT_CAP = 32_000
        _TOOL_CAP = 8_000

        def _trunc(s: object, n: int) -> object:
            if not isinstance(s, str) or len(s) <= n:
                return s
            return s[:n] + f"\n…[truncated {len(s) - n} chars]"

        def _trunc_tool_results(trs: object) -> object:
            if not isinstance(trs, list):
                return trs
            out: list = []
            for tr in trs:
                if isinstance(tr, dict):
                    item = dict(tr)
                    if "output" in item:
                        item["output"] = _trunc(item.get("output"), _TOOL_CAP)
                    out.append(item)
                else:
                    out.append(_trunc(tr, _TOOL_CAP))
            return out

        return {
            "messages": [
                {
                    "id": str(m.id),
                    "role": m.role.value,
                    "content": _trunc(m.content, _CONTENT_CAP),
                    "thinking": _trunc(m.thinking, _CONTENT_CAP // 2)
                    if m.thinking
                    else m.thinking,
                    "tool_calls": m.tool_calls,
                    "tool_results": _trunc_tool_results(m.tool_results),
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
        # Empty composer submits create noisy greeting turns; require real text
        # unless attachments are present.
        has_atts = bool(getattr(req, "attachments", None))
        if not str(req.message or "").strip() and not has_atts:
            raise HTTPException(400, "Message is empty")

        try:
            return await _send_message_body(
                session_id=session_id,
                req=req,
                request_id=request_id,
                runtime=runtime,
                memory=memory,
                gateway=gateway,
            )
        except HTTPException:
            raise
        except Exception as exc:
            # Session deleted mid-turn (FK / missing row) → 404, not opaque 500
            if memory is not None:
                with contextlib.suppress(Exception):
                    still = await memory.get_chat_session(session_id)
                    if still is None:
                        logger.info(
                            "send_message session gone mid-turn id=%s err=%s",
                            session_id,
                            exc,
                        )
                        raise HTTPException(404, "Session not found") from exc
            logger.exception("send_message failed session=%s", session_id)
            raise HTTPException(500, "Internal Server Error") from exc

    async def _send_message_body(
        *,
        session_id: str,
        req: SendMessageRequest,
        request_id: str,
        runtime,
        memory,
        gateway,
    ):
        if memory:
            from remedy.core.session_llm import (
                resolve_session_llm_bind,
                session_llm_update_fields,
            )
            from remedy.models import ChatMessage, ChatSession

            existing = await memory.get_chat_session(session_id)
            sp, sm = resolve_session_llm_bind(
                session=existing,
                req_provider=getattr(req, "provider", None),
                req_model=req.model,
            )
            fields = session_llm_update_fields(provider=sp, model=sm)
            if existing is None:
                default_proj = load_config().get("project_path")
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=req.message[:60],
                    model=fields.get("model") or req.model,
                    llm_provider=fields.get("llm_provider"),
                    agent=req.agent,
                    project_path=default_proj,
                ))
            elif fields:
                await memory.update_chat_session(session_id, **fields)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=req.message,
            ))

        # Sticky per-session provider+model (never model-only against another host).
        from remedy.core.session_llm import resolve_session_llm_bind

        sess_provider = getattr(req, "provider", None)
        sess_model = req.model
        if memory:
            with contextlib.suppress(Exception):
                ex = await memory.get_chat_session(session_id)
                sess_provider, sess_model = resolve_session_llm_bind(
                    session=ex,
                    req_provider=getattr(req, "provider", None),
                    req_model=req.model,
                )

        # Always re-sync credentials from disk (wizard/settings may have just saved).
        _sync_runtime_llm_from_config(
            runtime,
            model_override=sess_model,
            provider_override=sess_provider,
            llm_only=True,
        )

        from remedy.core.metrics import default_registry

        start = time.perf_counter()
        response_text = ""
        async for token in runtime.stream_response(
            req.message,
            session_id=session_id,
            model=sess_model,
            plan_mode=bool(getattr(req, "plan_mode", False)),
            provider=sess_provider,
        ):
            # Keep user-visible text only (tool lifecycle events are @@-prefixed).
            if isinstance(token, str) and token.startswith("@@"):
                continue
            response_text += token
            # Bail if session vanished mid-stream (user closed tab / deleted)
            if memory is not None and len(response_text) % 64 == 0:
                with contextlib.suppress(Exception):
                    if await memory.get_chat_session(session_id) is None:
                        raise HTTPException(404, "Session not found")
        elapsed_s = time.perf_counter() - start
        elapsed = elapsed_s * 1000
        default_registry.counter(
            "remedy_chat_requests_total", path="session_message"
        ).inc()
        default_registry.histogram(
            "remedy_chat_duration_seconds", path="session_message"
        ).observe(elapsed_s)

        if memory and response_text:
            from remedy.models import ChatMessage

            # Session may have been deleted during generation
            still = await memory.get_chat_session(session_id)
            if still is None:
                raise HTTPException(404, "Session not found")
            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.ASSISTANT,
                content=response_text,
            ))
            # Desktop reply → Telegram/Discord/etc. when session is messenger-origin.
            with contextlib.suppress(Exception):
                from remedy.gateway.session_bridge import mirror_desktop_reply_to_messenger
                from remedy.interfaces.session_events import publish_session_event

                ex = await memory.get_chat_session(session_id)
                if ex is not None:
                    await mirror_desktop_reply_to_messenger(gateway, ex, response_text)
                    if getattr(ex, "origin_channel", None):
                        await publish_session_event(
                            "message_added",
                            session_id,
                            origin_channel=getattr(ex, "origin_channel", None),
                            title=getattr(ex, "title", None),
                            role="assistant",
                        )

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
        # Prevent path traversal (basename + relative_to — not startswith prefix)
        safe = Path(filename).name
        if not safe or safe in (".", ".."):
            raise HTTPException(400, "Invalid path")
        try:
            root = directory.resolve()
            path = (directory / safe).resolve()
            path.relative_to(root)
        except (OSError, ValueError) as exc:
            raise HTTPException(400, "Invalid path") from exc
        if not path.is_file():
            raise HTTPException(404, "Attachment not found")
        return FileResponse(path, filename=safe)

    # -- SSE streaming (structured events) -----------------------------------
    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        # Serialize same-session streams (multi-tab / double-submit safety).
        # Multi-session parallelism remains allowed.
        is_busy = False
        with contextlib.suppress(Exception):
            if hasattr(runtime, "is_session_streaming"):
                is_busy = bool(runtime.is_session_streaming(session_id))
            else:
                from remedy.core.turn_context import is_session_streaming

                is_busy = is_session_streaming(session_id)
        if is_busy:
            raise HTTPException(
                409,
                "Session already has a generation in progress. "
                "Stop the current turn first, then send again.",
            )

        request_id = str(uuid4())
        att_dicts = [a.model_dump() for a in (req.attachments or [])]
        # Path jail: drop client-forged paths outside attachments tree before
        # any snippet inject / multimodal / vision decode.
        home_att = None
        with contextlib.suppress(Exception):
            home_att = load_config().get("home_dir")
        with contextlib.suppress(Exception):
            from remedy.interfaces.attachments import filter_jailed_attachments

            att_dicts = filter_jailed_attachments(
                att_dicts, home_dir=home_att, session_id=session_id
            )
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
                display_content = display_content + inject_text_file_snippets(
                    att_dicts, home_dir=home_att, session_id=session_id
                )

        if memory:
            from remedy.models import ChatMessage, ChatSession

            def _looks_like_path_title(text: str) -> bool:
                t = (text or "").strip()
                if not t:
                    return False
                if re.match(r"^[A-Za-z]:[\\/]", t):
                    return True
                if t.startswith("\\\\") or t.startswith("/Users/") or t.startswith("/home/"):
                    return True
                if "\\" in t and re.search(
                    r"\.(png|jpe?g|gif|webp|bmp|heic|pdf|docx?)$", t, re.I
                ):
                    return True
                return bool(re.match(r"^Screenshot\b", t, re.I) and re.search(r"\.(png|jpe?g|gif|webp)$", t, re.I))

            def _title_from_attachment_name(name: str, *, max_len: int = 52) -> str:
                raw = (name or "").strip().replace("/", "\\")
                if not raw:
                    return "Attachment"
                base = raw.rsplit("\\", 1)[-1]
                pretty = re.sub(
                    r"\.(png|jpe?g|gif|webp|bmp|heic)$", "", base, flags=re.I
                )
                t = re.sub(r"[_-]+", " ", pretty)
                t = " ".join(t.split()).strip() or "Image"
                if re.match(r"^Screenshot\b", t, re.I):
                    t = re.sub(r"\s+\d{4}.*$", "", t).strip() or "Screenshot"
                if len(t) > max_len:
                    t = t[: max_len - 1].rstrip() + "…"
                return t

            def _title_from_prompt(text: str, *, max_len: int = 52) -> str:
                t = " ".join((text or "").strip().split())
                if not t:
                    return "New Session"
                # Drop attachment display blocks from title.
                if "📎" in t:
                    t = t.split("📎", 1)[0].strip() or t
                if t.startswith("(") and "see attached" in t.lower():
                    name = (att_dicts[0].get("name") if att_dicts else "") or "Attachments"
                    t = _title_from_attachment_name(str(name), max_len=max_len)
                elif _looks_like_path_title(t):
                    t = _title_from_attachment_name(t, max_len=max_len)
                if len(t) > max_len:
                    t = t[: max_len - 1].rstrip() + "…"
                return t or "New Session"

            from remedy.core.session_llm import (
                resolve_session_llm_bind,
                session_llm_update_fields,
            )

            existing = await memory.get_chat_session(session_id)
            sp, sm = resolve_session_llm_bind(
                session=existing,
                req_provider=getattr(req, "provider", None),
                req_model=req.model,
            )
            fields = session_llm_update_fields(provider=sp, model=sm)
            if existing is None:
                default_proj = load_config().get("project_path")
                title_src = user_text or (
                    att_dicts[0].get("name") if att_dicts else "Attachments"
                )
                await memory.create_chat_session(ChatSession(
                    id=session_id,
                    title=_title_from_prompt(str(title_src)),
                    model=fields.get("model") or req.model,
                    llm_provider=fields.get("llm_provider"),
                    agent=req.agent,
                    project_path=default_proj,
                ))
            else:
                # Auto-name placeholder sessions from the first real prompt.
                # Also replace path-only titles (e.g. full OneDrive screenshot path)
                # when the user later sends a real message or plan prompt.
                cur_title = (existing.title or "").strip()
                placeholder = (
                    not cur_title
                    or cur_title.lower()
                    in (
                        "new session",
                        "new chat",
                        "untitled",
                        "attachments",
                        "attachment",
                        "image",
                        "screenshot",
                    )
                    or _looks_like_path_title(cur_title)
                )
                if placeholder and (user_text or att_dicts):
                    new_title = _title_from_prompt(
                        user_text
                        or str(
                            att_dicts[0].get("name") if att_dicts else "Attachments"
                        )
                    )
                    # Prefer real user text over another path-ish attachment name
                    if user_text.strip() and not _looks_like_path_title(new_title):
                        await memory.update_chat_session(
                            session_id, title=new_title
                        )
                    elif placeholder and (
                        _looks_like_path_title(cur_title) or not cur_title
                    ):
                        await memory.update_chat_session(
                            session_id, title=new_title
                        )
                # Persist paired bind — never model without provider (cross-tab corruption).
                if fields and (
                    fields.get("model") != existing.model
                    or fields.get("llm_provider") != getattr(existing, "llm_provider", None)
                ):
                    await memory.update_chat_session(session_id, **fields)

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=display_content,
                model=req.model,
                agent=req.agent,
            ))

        # Sticky per-session provider+model (multi-tab multi-provider).
        from remedy.core.session_llm import resolve_session_llm_bind

        sess_provider = getattr(req, "provider", None)
        sess_model = req.model
        if memory:
            with contextlib.suppress(Exception):
                ex = await memory.get_chat_session(session_id)
                sess_provider, sess_model = resolve_session_llm_bind(
                    session=ex,
                    req_provider=getattr(req, "provider", None),
                    req_model=req.model,
                )

        # Always re-sync credentials from disk (first-run wizard / settings).
        api_key = _sync_runtime_llm_from_config(
            runtime,
            model_override=sess_model,
            provider_override=sess_provider,
            llm_only=True,
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
                # Always enter stream_response: L0 (list skills / model / version /
                # whoami / status) works without a provider key. Non-L0 without a
                # key still gets a clear notice from the agent (streamed as text).
                aborted = False

                async for token in runtime.stream_response(
                    user_text or "(see attached files)",
                    session_id=session_id,
                    model=sess_model,
                    attachments=att_dicts,
                    plan_mode=bool(getattr(req, "plan_mode", False)),
                    provider=sess_provider,
                ):
                    if isinstance(token, str) and token.startswith("@@aborted"):
                        status = "aborted"
                        aborted = True
                        # Cooperative stop — not an error. Client Stop / abort.
                        yield (
                            "event: aborted\ndata: "
                            + json.dumps(
                                {
                                    "type": "aborted",
                                    "message": "Generation stopped",
                                    "request_id": request_id,
                                }
                            )
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
                    elif token.startswith("@@status:"):
                        # Vision decode / mid-turn status — never chat body.
                        label = token[len("@@status:") :].strip()
                        if label:
                            yield (
                                "event: progress\ndata: "
                                + json.dumps(
                                    {
                                        "type": "progress",
                                        "label": label,
                                    },
                                    default=str,
                                )
                                + "\n\n"
                            )
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
                    elif isinstance(token, str) and token.startswith("@@"):
                        # Unknown control token — never leak into the bubble.
                        logger.debug(
                            "Skipping unknown stream control token: %s", token[:80]
                        )
                        continue
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

                # Metrics: no provider key notice from agent (L0 never hits this).
                if (
                    not aborted
                    and status == "ok"
                    and not api_key
                    and full_response
                    and "no API key" in full_response
                ):
                    status = "no_key"

                # Abort path already emitted event:aborted — skip usage/done.
                if aborted:
                    return

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
                        # Provider totals already sum each LLM round; never add
                        # char-heuristic estimate on top (that inflated costs).
                        final_usage = merge_usage(usage_acc)
                    elif usage_acc:
                        final_usage = merge_usage(usage_acc)
                    else:
                        final_usage = merge_usage(est)
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
                    # Desktop→messenger: mirror so Telegram users see desktop replies.
                    with contextlib.suppress(Exception):
                        from remedy.gateway.session_bridge import (
                            mirror_desktop_reply_to_messenger,
                        )
                        from remedy.interfaces.session_events import publish_session_event

                        ex = await memory.get_chat_session(session_id)
                        if ex is not None:
                            await mirror_desktop_reply_to_messenger(
                                gateway, ex, full_response
                            )
                            if getattr(ex, "origin_channel", None):
                                await publish_session_event(
                                    "message_added",
                                    session_id,
                                    origin_channel=getattr(ex, "origin_channel", None),
                                    title=getattr(ex, "title", None),
                                    role="assistant",
                                )

                done_payload: dict = {"type": "done", "request_id": request_id}
                if isinstance(final_usage, dict):
                    done_payload["usage"] = final_usage
                yield f"event: done\ndata: {json.dumps(done_payload)}\n\n"

            except asyncio.CancelledError:
                # Client disconnect / ASGI cancel — kill shell children + CUA jobs
                # so Stop / tab close does not leave tools running.
                status = "cancelled"
                with contextlib.suppress(Exception):
                    from remedy.core.turn_context import abort_session as _abort_turn

                    _abort_turn(session_id)
                if runtime is not None:
                    with contextlib.suppress(Exception):
                        ss = getattr(runtime, "_streaming_sessions", None)
                        if isinstance(ss, set):
                            ss.discard(str(session_id))
                raise
            except Exception as e:
                status = "error"
                logger.exception("SSE stream error")
                # Never stream raw exception text — may embed keys / tokens.
                try:
                    from remedy.core.metabolism.redact import redact_text

                    safe_msg = redact_text(str(e))[:800]
                except Exception:
                    safe_msg = "Stream error (details redacted)"
                if not safe_msg.strip():
                    safe_msg = "Stream error"
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': safe_msg})}\n\n"
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
                # Honor per-session LLM (same as /messages/stream).
                sess_provider = None
                sess_model = getattr(req, "model", None)
                if memory is not None:
                    with contextlib.suppress(Exception):
                        ex = await memory.get_chat_session(session_id)
                        if ex is not None:
                            sess_provider = getattr(ex, "llm_provider", None)
                            if not sess_model:
                                sess_model = getattr(ex, "model", None)
                _sync_runtime_llm_from_config(
                    runtime,
                    model_override=sess_model,
                    provider_override=sess_provider,
                    llm_only=True,
                )
                async for token in runtime.stream_response(
                    req.message,
                    session_id=session_id,
                    model=sess_model,
                    provider=sess_provider,
                ):
                    yield await _sse_stream_text(token, event="token")
            except Exception as e:
                status = "error"
                try:
                    from remedy.core.metabolism.redact import redact_text

                    safe_msg = redact_text(str(e))[:800]
                except Exception:
                    safe_msg = "Stream error (details redacted)"
                if not safe_msg.strip():
                    safe_msg = "Stream error"
                yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': safe_msg})}\n\n"

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

