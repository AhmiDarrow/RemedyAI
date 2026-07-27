"""API route registration for Remedy FastAPI app."""
from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException

from remedy.interfaces.api_models import (
    ChatRequest,
    ChatResponse,
)
from remedy.models import (
    ChannelKind,
    ChatMessageRole,
    EventKind,
    GatewayEvent,
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

        # Join all string chunks; skip the gateway "Processing …" status line.
        parts: list[str] = []
        for r in responses:
            if not isinstance(r, str):
                continue
            text = r.strip()
            if not text:
                continue
            if text.startswith("[") and "Processing " in text[:80]:
                continue
            parts.append(r)
        response_text = "".join(parts).strip()

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

