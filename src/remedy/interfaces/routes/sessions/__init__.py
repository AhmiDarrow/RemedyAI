"""Chat session HTTP routes — split by concern."""

from __future__ import annotations

from fastapi import FastAPI

from remedy.interfaces.routes.sessions.attachments import register_attachments_routes
from remedy.interfaces.routes.sessions.crud import register_crud_routes
from remedy.interfaces.routes.sessions.llm import register_llm_routes
from remedy.interfaces.routes.sessions.messages import register_messages_routes
from remedy.interfaces.routes.sessions.stream import register_stream_routes
from remedy.interfaces.routes.sessions.legacy_chat import (
    register_legacy_chat_stream_routes,
)


def register_sessions_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register all session-related routes (closes over runtime/gateway/memory)."""
    kw = {"runtime": runtime, "gateway": gateway, "memory": memory}
    register_crud_routes(app, **kw)
    register_llm_routes(app, **kw)
    register_messages_routes(app, **kw)
    register_attachments_routes(app, **kw)
    register_stream_routes(app, **kw)
    register_legacy_chat_stream_routes(app, **kw)
    from remedy.interfaces.routes.session_events import register_session_event_routes

    register_session_event_routes(app)


__all__ = ["register_sessions_routes"]
