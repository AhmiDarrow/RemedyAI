"""Production :7400 is owned by remedy-runtime — Python serve must not uvicorn."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from remedy.interfaces.cli.launcher import resolve_remedy_runtime_command


def test_resolve_prefers_env_override(monkeypatch, tmp_path) -> None:
    path = tmp_path / "custom-runtime.exe"
    path.write_bytes(b"")
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(path))
    assert resolve_remedy_runtime_command() == [str(path)]


def test_fastapi_api_module_is_gone() -> None:
    """Phase 4 exit: FastAPI create_app / interfaces.api are deleted."""
    assert importlib.util.find_spec("remedy.interfaces.api") is None
    assert not Path("src/remedy/interfaces/api.py").exists()


def test_python_fastapi_routes_package_is_gone() -> None:
    """Deleted FastAPI route twins must stay gone with the routes package."""
    assert importlib.util.find_spec("remedy.interfaces.routes") is None
    assert not Path("src/remedy/interfaces/routes").exists()
    assert importlib.util.find_spec("remedy.connect") is None
    assert importlib.util.find_spec("remedy.core.computer.host_conpty") is None
