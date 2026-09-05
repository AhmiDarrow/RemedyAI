"""Turn explain HTTP has no FastAPI twin; Go owns production sessions.

``GET /api/sessions/{id}/turns/{turn_id}/explain`` is absent from TestClient.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_explain_turn_http_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    assert "/api/sessions/{session_id}/turns/{turn_id}/explain" not in paths
