"""Local media HTTP is Go-owned; keep a boundary check on the TestClient surface."""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_media_http_route_absent_from_testclient() -> None:
    """HTTP /api/media is Go-owned (native/go/httpapi/workspace_test.go)."""
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    assert "/api/media" not in paths
    assert "/api/files" not in paths
    assert "/api/workspace" not in paths
