"""Catalog helpers; HTTP /api/models|/api/commands|/api/agents is Go-owned.

Production lives in ``native/go/httpapi`` (``providers.go``, ``catalog_static.go``,
``session_command.go``). FastAPI twins are gone.
"""

from __future__ import annotations

from remedy.interfaces.config import PROVIDER_CATALOG


def test_demo_curated_catalog_excludes_image_and_video_junk() -> None:
    """Pinned without the deleted FastAPI helper - Go filters live discovery."""
    catalog = PROVIDER_CATALOG["demo"]["models"]
    ids = {str(m.get("id") or "") for m in catalog}
    assert "codestral-latest" in ids
    assert "deepseek-v4-flash" not in ids
    assert "flux-kontext-max" not in ids
    assert "kling-v3.0-pro" not in ids
