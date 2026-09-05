"""Session attachment HTTP is Go-owned; keep a TestClient boundary check.

Production upload/get live in ``native/go/httpapi/session_attachments.go``.
The FastAPI twin is gone; path-jail coverage stays in Go tests.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_session_attachment_http_routes_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/sessions/{session_id}/attachments",
        "/api/sessions/{session_id}/attachments/{filename}",
    ):
        assert path not in paths
