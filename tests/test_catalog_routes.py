"""Catalog helpers; HTTP /api/models|/api/commands|/api/agents is Go-owned.

Production lives in ``native/go/httpapi`` (``providers.go``, ``catalog_static.go``,
``session_command.go``). FastAPI twins are gone.
"""

from __future__ import annotations

from remedy.interfaces.config import PROVIDER_CATALOG


def test_builtin_catalog_bakes_in_no_models() -> None:
    """Models come from each provider's endpoint; nothing is compiled in."""
    for pid, meta in PROVIDER_CATALOG.items():
        if meta.get("user_defined"):
            continue
        assert not meta.get("models"), pid


