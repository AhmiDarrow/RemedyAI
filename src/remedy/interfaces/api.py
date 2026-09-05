"""Pure request-log helpers + re-exports (no FastAPI create_app).

Production ``:7400`` is Go ``remedy-runtime``. The former TestClient
empty-route harness is gone — boundary coverage lives in Go ``httpapi``
tests and ``tests/test_serve_runtime_owned.py``.
"""
from __future__ import annotations

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


__all__ = [
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
