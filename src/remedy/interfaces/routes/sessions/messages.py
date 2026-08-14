"""Session API routes package."""
from __future__ import annotations

import contextlib
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from remedy.interfaces.api_models import (
    SendMessageRequest,
)
from remedy.interfaces.api_support import (
    load_config,
)
from remedy.models import (
    ChatMessageRole,
)

logger = logging.getLogger(__name__)



def register_messages_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register messages session routes."""
    _ = gateway  # may be unused in some modules
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

    @app.get("/api/sessions/{session_id}/todos")
    async def list_session_todos(session_id: str):
        """Live build checklist for the session project (user-visible)."""
        from pathlib import Path

        from remedy.core.build_todos import load_todos, open_todo_count, todos_public

        root = None
        if memory is not None:
            sess = await memory.get_chat_session(session_id)
            if sess is None:
                raise HTTPException(404, "Session not found")
            raw = getattr(sess, "project_path", None)
            if raw:
                p = Path(str(raw))
                root = p.parent if p.is_file() else p
        items = load_todos(runtime, root=root)
        if open_todo_count(items) == 0:
            return {"todos": []}
        return {"todos": todos_public(items)}

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
