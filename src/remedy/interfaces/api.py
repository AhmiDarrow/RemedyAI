"""FastAPI surface for pytest / TestClient only (empty route tree).

Production ``:7400`` is Go ``remedy-runtime``. Auth, CORS, and bootstrap
HTTP live in Go httpapi — this module keeps ``create_app`` for boundary
checks and request-log helpers.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from remedy import __version__ as _remedy_version
from remedy.interfaces.api_models import (
    ChatRequest,
    ChatResponse,
    StatusResponse,
    WebhookPayload,
)
from remedy.interfaces.api_support import (
    handle_slash_command,
    load_config,
    sse_headers,
)

# Quiet paths for helpers (Go owns these in production).
_SLOW_EXEMPT_PATHS = frozenset(
    {"/api/status", "/api/ping", "/api/turn-active", "/api/self-improve"}
)
_SLOW_WARN_MS = 500.0
_QUIET_SILENT_MS = 100.0
_CLIENT_GONE_NAMES = frozenset(
    {"EndOfStream", "ClientDisconnect", "BrokenResourceError"}
)


def should_warn_slow(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
) -> bool:
    """True when the request log should emit a SLOW warning."""
    _ = method
    if float(duration_ms) < _SLOW_WARN_MS:
        return False
    if int(status_code) >= 400:
        return True
    return str(path or "") not in _SLOW_EXEMPT_PATHS


def request_log_level(
    *,
    quiet: bool,
    status_code: int,
    duration_ms: float,
    slow: bool,
) -> str | None:
    """Return logging level name, or None to stay silent."""
    if slow:
        return "warning"
    if quiet and int(status_code) < 400 and float(duration_ms) < _QUIET_SILENT_MS:
        return None
    if quiet:
        return "debug"
    return "info"


def _is_client_gone(exc: BaseException) -> bool:
    """True when the HTTP client disconnected mid-request (not a server fault)."""
    if type(exc).__name__ in _CLIENT_GONE_NAMES:
        return True
    nxt = exc.__cause__ or exc.__context__
    return nxt is not None and type(nxt).__name__ in _CLIENT_GONE_NAMES


__all__ = [
    "create_app",
    "ChatRequest",
    "ChatResponse",
    "StatusResponse",
    "WebhookPayload",
    "handle_slash_command",
    "load_config",
    "sse_headers",
    "should_warn_slow",
    "request_log_level",
]


def create_app(
    runtime=None,
    gateway=None,
    memory=None,
    title: str = "Remedy AI",
    version: str = _remedy_version,
    *,
    api_key: str = "",
) -> FastAPI:
    """Build the in-process FastAPI app for pytest / TestClient.

    Empty route tree — not a production HTTP server.
    """
    handle_slash_command._skills_registry = (  # type: ignore[attr-defined]
        getattr(runtime, "skills", None) if runtime is not None else None
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield

    # Docs on for TestClient unless REMEDY_DISABLE_API_DOCS=1 (or frozen).
    _docs_env = str(os.environ.get("REMEDY_DISABLE_API_DOCS", "")).strip().lower()
    if _docs_env in ("0", "false", "no", "off"):
        disable_docs = False
    elif _docs_env in ("1", "true", "yes", "on"):
        disable_docs = True
    else:
        from remedy.core.runtime_identity import is_frozen_install

        disable_docs = is_frozen_install()

    app = FastAPI(
        title=title,
        version=version,
        description="Remedy AI Agent Framework — Desktop & Web API",
        lifespan=lifespan,
        docs_url=None if disable_docs else "/docs",
        redoc_url=None if disable_docs else "/redoc",
        openapi_url=None if disable_docs else "/openapi.json",
    )
    app.state.disable_api_docs = disable_docs
    if api_key:
        app.state.api_key = api_key

    from remedy.interfaces.routes import register_all_routes

    register_all_routes(app, runtime=runtime, gateway=gateway, memory=memory)
    return app
