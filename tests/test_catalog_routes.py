"""Catalog HTTP is Go-owned; keep a TestClient boundary check.

Production ``/api/models``, ``/api/commands``, ``/api/agents``, and
``POST /api/sessions/{id}/command`` live in ``native/go/httpapi``
(``providers.go``, ``catalog_static.go``, ``session_command.go``). The
FastAPI twin — including leftover Python-only ``commands/custom`` /
``agents/custom`` — is gone. Demo allowlist coverage is Go
``demoModelAllowed``; curated catalog contents stay in provider catalog tests.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app
from remedy.interfaces.config import PROVIDER_CATALOG


def test_catalog_http_routes_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    for path in (
        "/api/models",
        "/api/commands",
        "/api/commands/custom",
        "/api/commands/custom/{name}",
        "/api/agents",
        "/api/agents/custom",
        "/api/agents/custom/{name}",
        "/api/sessions/{session_id}/command",
    ):
        assert path not in paths


def test_demo_curated_catalog_excludes_image_and_video_junk() -> None:
    """Pinned without the deleted FastAPI helper — Go filters live discovery."""
    catalog = PROVIDER_CATALOG["demo"]["models"]
    ids = {str(m.get("id") or "") for m in catalog}
    assert "codestral-latest" in ids
    assert "deepseek-v4-flash" not in ids
    assert "flux-kontext-max" not in ids
    assert "kling-v3.0-pro" not in ids
