"""TestClient-only: keep /dashboard HTML; Go owns the rest of misc.

Production ``:7400`` serves the SPA via Go ``httpapi/webui.go``.
``/api/app/command`` and ``/api/projects/scan`` are Go-owned.
Leftover Python-only ``/api/openapi.json`` / ``.yaml`` exports are gone.
"""
from __future__ import annotations

from fastapi import FastAPI, Response

from remedy import __version__ as _remedy_version


def register_misc_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register TestClient-only dashboard."""
    _ = (runtime, gateway, memory)

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        from remedy.interfaces.api import DASHBOARD_HTML

        html = DASHBOARD_HTML.replace("{{version}}", _remedy_version)
        return Response(content=html, media_type="text/html")
