"""Session stream + CRUD HTTP are Go-owned; keep TestClient absences.

Production ``POST /api/sessions/{id}/messages/stream`` lives in
``native/go/httpapi`` (``stream.go``) via CognitionTurnRunner. Session CRUD /
messages / llm FastAPI twins are gone too.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app


def test_session_stream_and_crud_http_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/sessions",
        "/api/sessions/{session_id}",
        "/api/sessions/{session_id}/messages",
        "/api/sessions/{session_id}/messages/stream",
        "/api/sessions/{session_id}/llm",
        "/api/sessions/{session_id}/abort",
    ):
        assert path not in paths


def test_session_stream_post_unmatched() -> None:
    client = TestClient(create_app(api_key=""))
    r = client.post(
        "/api/sessions/does-not-exist/messages/stream",
        json={"message": "hi"},
    )
    assert r.status_code in (404, 405)
