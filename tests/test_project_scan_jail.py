"""Misc HTTP twins are Go-owned; TestClient has no /dashboard stub.

Production ``POST /api/projects/scan`` and ``GET /api/app/command`` live in
``native/go/httpapi`` (``projects_scan.go``, ``app_command.go``). Leftover
``/api/openapi.*`` exports and the HTML ``/dashboard`` stub are gone.
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
        "/dashboard",
    ):
        assert path not in paths
