"""TestClient-only: OpenAPI export and simple HTML dashboard.

Go ``remedy-runtime`` owns production ``:7400``. App command, updates check,
and project scan live on Go httpapi — not registered here.
"""
from __future__ import annotations

import json
import logging

import yaml
from fastapi import FastAPI, Response

from remedy import __version__ as _remedy_version

logger = logging.getLogger(__name__)


def register_misc_routes(app: FastAPI, *, runtime=None, gateway=None, memory=None) -> None:
    """Register TestClient-only helpers (closes over unused runtime deps)."""
    _ = (runtime, gateway, memory)

    def _yaml_schema() -> str:
        import io

        data = app.openapi()
        buf = io.StringIO()
        yaml.safe_dump(data, buf, sort_keys=False)
        return buf.getvalue()

    # -- OpenAPI schema export -----------------------------------------------
    # Hidden when packaged/frozen or REMEDY_DISABLE_API_DOCS=1 (S-AUTH-05).
    if not getattr(getattr(app, "state", None), "disable_api_docs", False):

        @app.get("/api/openapi.yaml", include_in_schema=False)
        async def export_openapi_yaml():
            return Response(
                content=_yaml_schema(),
                media_type="application/yaml",
            )

        @app.get("/api/openapi.json", include_in_schema=False)
        async def export_openapi_json():
            return Response(
                content=json.dumps(app.openapi(), indent=2),
                media_type="application/json",
            )

    # -- dashboard (simple HTML) ---------------------------------------------
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        from remedy.interfaces.api import DASHBOARD_HTML

        html = DASHBOARD_HTML.replace("{{version}}", _remedy_version)
        return Response(content=html, media_type="text/html")
