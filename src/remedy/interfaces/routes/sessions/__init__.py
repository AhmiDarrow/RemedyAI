"""TestClient-only: chat session HTTP routes (split by concern).

Go ``remedy-runtime`` owns production ``:7400``; this registrar is for pytest.
SSE ``/messages/stream`` and ``/steer`` are Go-owned (no FastAPI twin).
"""

from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.sessions.attachments import register_attachments_routes
from remedy.interfaces.routes.sessions.crud import register_crud_routes
from remedy.interfaces.routes.sessions.explain import register_explain_routes
from remedy.interfaces.routes.sessions.llm import register_llm_routes
from remedy.interfaces.routes.sessions.messages import register_messages_routes


def register_sessions_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register all session-related routes (closes over runtime/gateway/memory)."""
    kw = {"runtime": runtime, "gateway": gateway, "memory": memory}
    register_crud_routes(app, **kw)
    register_llm_routes(app, **kw)
    register_messages_routes(app, **kw)
    register_attachments_routes(app, **kw)
    # /messages/stream + /steer — Go CognitionTurnRunner / httpapi only.
    register_explain_routes(app, **kw)
    # SSE GET /api/events/sessions is Go-owned (no FastAPI twin registrar).


__all__ = ["register_sessions_routes"]
