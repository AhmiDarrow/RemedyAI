"""FastAPI route package is gone; Go owns production :7400."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_fastapi_routes_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.interfaces.routes") is None
    assert not Path("src/remedy/interfaces/routes").exists()


def test_create_app_harness_is_gone() -> None:
    import remedy.interfaces.api as api

    assert not hasattr(api, "create_app")
    assert callable(api.should_warn_slow)
    assert callable(api.request_log_level)
