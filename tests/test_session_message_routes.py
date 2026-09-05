"""Session message HTTP is Go-owned; keep a TestClient boundary check.

Production ``GET/POST /api/sessions/{id}/messages`` (and edit) live in
``native/go/httpapi`` (``sessions.go`` / CognitionTurnRunner). The FastAPI
twin is gone.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_session_message_http_routes_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/sessions/{session_id}/messages",
        "/api/sessions/{session_id}/messages/{msg_id}/edit",
        "/api/sessions/{session_id}/messages/stream",
    ):
        assert path not in paths
