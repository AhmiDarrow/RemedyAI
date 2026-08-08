"""Legacy /api/chat/stream compatibility route."""

from __future__ import annotations

import contextlib
import json
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from remedy.interfaces.api_models import ChatRequest
from remedy.interfaces.api_support import (
    _sse_stream_text,
    _sync_runtime_llm_from_config,
    sse_headers,
)


def register_legacy_chat_stream_routes(
    app: FastAPI, *, runtime=None, gateway=None, memory=None
) -> None:
    """Register legacy chat stream (backward compatibility)."""
    _ = gateway

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
