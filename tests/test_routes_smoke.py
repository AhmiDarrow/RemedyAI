"""FastAPI routes package and create_app / interfaces.api are deleted."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_fastapi_routes_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.interfaces.routes") is None
    assert not Path("src/remedy/interfaces/routes").exists()


def test_fastapi_api_module_is_gone() -> None:
    assert importlib.util.find_spec("remedy.interfaces.api") is None
    assert not Path("src/remedy/interfaces/api.py").exists()
