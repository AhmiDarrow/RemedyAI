"""FastAPI route twins are gone; body caps live in Go httpapi only."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_no_fastapi_route_or_api_harness_remains() -> None:
    assert importlib.util.find_spec("remedy.interfaces.routes") is None
    assert importlib.util.find_spec("remedy.interfaces.api") is None
    assert not Path("src/remedy/interfaces/routes").exists()
    assert not Path("src/remedy/interfaces/api.py").exists()
