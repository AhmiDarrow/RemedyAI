"""Misc HTTP twins are Go-owned; misc keeps /dashboard for TestClient only.

Production ``POST /api/projects/scan`` and ``GET /api/app/command`` live in
``native/go/httpapi`` (``projects_scan.go``, ``app_command.go``). Leftover
``/api/openapi.*`` exports are gone with the FastAPI twin.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_misc_go_owned_http_routes_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/projects/scan",
        "/api/app/command",
        "/api/openapi.json",
        "/api/openapi.yaml",
    ):
        assert path not in paths
    assert "/dashboard" in paths
