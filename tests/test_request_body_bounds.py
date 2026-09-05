"""FastAPI route body-cap leftovers are gone; Go owns production webhooks."""

from __future__ import annotations

from pathlib import Path


def test_fastapi_routes_package_is_gone():
    """No FastAPI route modules remain to buffer unbounded bodies."""
    assert not Path("src/remedy/interfaces/routes").exists()
