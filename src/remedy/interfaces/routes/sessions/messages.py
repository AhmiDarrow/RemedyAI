"""Session API routes package."""
from __future__ import annotations

import contextlib
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query

from remedy.core.build_oracle import coerce_text_arg
from remedy.interfaces.api_models import (
    SendMessageRequest,
)
from remedy.interfaces.api_support import (
    load_config,
)
from remedy.interfaces.attachments import filter_jailed_attachments
from remedy.models import (
    ChatMessage,
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
        if await memory.get_chat_session(session_id) is None:
            raise HTTPException(404, "Session not found")
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
            from remedy.core.workspace import is_unset_project_path, is_volume_root_path

            if not raw or is_unset_project_path(raw) or is_volume_root_path(raw):
                items = load_todos(runtime, session_id=session_id)
                if open_todo_count(items) == 0:
                    return {"todos": []}
                return {"todos": todos_public(items)}
            p = Path(str(raw))
            root = p.parent if p.is_file() else p
            if is_volume_root_path(root):
                items = load_todos(runtime, session_id=session_id)
                if open_todo_count(items) == 0:
                    return {"todos": []}
                return {"todos": todos_public(items)}
        items = load_todos(runtime if root is None else None, root=root, session_id=session_id)
        if open_todo_count(items) == 0:
            return {"todos": []}
        return {"todos": todos_public(items)}

    @app.post("/api/sessions/{session_id}/steer")
    async def steer_message(session_id: str, req: SendMessageRequest):
        """Say something to a turn that is already running.

        The text joins the live ReAct loop at its next step (no stop, no
        restart) and is persisted as the owner's message so the transcript
        reads in order. ``{"steered": false}`` means no turn was running —
        send it as a normal message instead.
        """
        text = coerce_text_arg(req.message)
        if not text:
            raise HTTPException(400, "Message is empty")
        from remedy.core.turn_context import try_push_nudge

        ok, reason = try_push_nudge(session_id, text)
        if not ok:
            return {"steered": False, "reason": reason}
        if memory is not None:
            with contextlib.suppress(Exception):
                if await memory.get_chat_session(session_id) is not None:
                    await memory.add_chat_message(
                        ChatMessage(
                            session_id=session_id,
                            role=ChatMessageRole.USER,
                            content=text,
                        )
                    )
        return {"steered": True, "reason": "ok"}

    @app.post("/api/sessions/{session_id}/messages")
    async def send_message(session_id: str, req: SendMessageRequest):
        if runtime is None:
            raise HTTPException(503, "Runtime not available")

        request_id = str(uuid4())
        # Empty composer submits create noisy greeting turns; require real text
        # unless attachments are present.
        has_atts = bool(getattr(req, "attachments", None))
        if not coerce_text_arg(req.message) and not has_atts:
            raise HTTPException(400, "Message is empty")

        from remedy.core.turn_context import (
            release_session_stream_claim,
            stream_claim_epoch,
            try_claim_session_stream,
        )

        if not try_claim_session_stream(session_id):
            raise HTTPException(
                409,
                "Session already has a generation in progress. "
                "Stop the current turn first, then send again.",
            )
        claim_epoch = stream_claim_epoch(session_id)
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
                # Decide inside the suppress, raise outside it. HTTPException
                # IS an Exception, so raising in there swallowed the 404 and
                # the caller got the opaque 500 this branch exists to avoid.
                gone = False
                with contextlib.suppress(Exception):
                    gone = await memory.get_chat_session(session_id) is None
                if gone:
                    logger.info(
                        "send_message session gone mid-turn id=%s err=%s",
                        session_id,
                        exc,
                    )
                    raise HTTPException(404, "Session not found") from exc
            logger.exception("send_message failed session=%s", session_id)
            raise HTTPException(500, "Internal Server Error") from exc
        finally:
            release_session_stream_claim(session_id, epoch=claim_epoch)

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
            from remedy.models import ChatMessage

            existing = await memory.get_chat_session(session_id)
            sp, sm = resolve_session_llm_bind(
                session=existing,
                req_provider=getattr(req, "provider", None),
                req_model=req.model,
                use_live_rmb=False,
            )
            fields = session_llm_update_fields(provider=sp, model=sm)
            if existing is None:
                raise HTTPException(404, "Session not found")
            elif fields:
                await memory.update_chat_session(session_id, **fields)

        att_dicts = [a.model_dump() if hasattr(a, "model_dump") else dict(a)
                     for a in (getattr(req, "attachments", None) or [])]
        home_att = None
        with contextlib.suppress(Exception):
            home_att = load_config().get("home_dir")
        # The import is at module level on purpose: inside the suppress, an
        # import failure left every attachment unfiltered. A filter that
        # fails at runtime drops them all — closed, not open.
        try:
            att_dicts = filter_jailed_attachments(
                att_dicts, home_dir=home_att, session_id=session_id
            )
        except Exception:
            logger.exception("attachment path jail failed; dropping attachments")
            att_dicts = []
        user_text = coerce_text_arg(req.message)
        display_content = user_text
        if att_dicts:
            with contextlib.suppress(Exception):
                from remedy.interfaces.attachments import (
                    build_attachment_prompt_block,
                    inject_text_file_snippets,
                )

                display_content = (user_text + build_attachment_prompt_block(att_dicts)).strip()
                display_content = display_content + inject_text_file_snippets(
                    att_dicts, home_dir=home_att, session_id=session_id
                )

        if memory:
            from remedy.models import ChatMessage

            await memory.add_chat_message(ChatMessage(
                session_id=session_id,
                role=ChatMessageRole.USER,
                content=display_content or user_text or "(see attached files)",
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
            user_text or "(see attached files)",
            session_id=session_id,
            model=sess_model,
            attachments=att_dicts,
            plan_mode=bool(getattr(req, "plan_mode", False)),
            chat_mode=bool(getattr(req, "chat_mode", False)),
            provider=sess_provider,
        ):
            # Keep user-visible text only (tool lifecycle events are @@-prefixed).
            if isinstance(token, str) and token.startswith("@@"):
                continue
            response_text += token
            # Bail if session vanished mid-stream (user closed tab / deleted)
            if memory is not None and len(response_text) % 64 == 0:
                # Same swallowed-raise as above: the bail never fired, so every
                # remaining token was still generated for a session that no
                # longer existed and the 404 only arrived after the whole
                # stream had been paid for.
                gone = False
                with contextlib.suppress(Exception):
                    gone = await memory.get_chat_session(session_id) is None
                if gone:
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
