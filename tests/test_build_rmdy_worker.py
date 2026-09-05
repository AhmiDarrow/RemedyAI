"""RMDY worker packaging — zipapp / onedir without an HTTP server."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_rmdy_worker.py"
WORKER_MOD = "remedy.runtime.rmdy_tool_worker"


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_rmdy_worker", BUILD_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bw():
    return _load_build_script()


def test_build_script_exists() -> None:
    assert BUILD_SCRIPT.is_file()


def test_worker_entry_module_importable() -> None:
    spec = importlib.util.find_spec(WORKER_MOD)
    assert spec is not None
    mod = importlib.import_module(WORKER_MOD)
    assert callable(getattr(mod, "main", None))


def test_banned_server_modules_listed(bw) -> None:
    banned = bw.BANNED_SERVER_MODULE_NAMES
    for name in ("fastapi", "uvicorn", "starlette"):
        assert name in banned


def test_stage_and_assert_no_http_server(bw, tmp_path) -> None:
    package = bw.stage_worker_tree(tmp_path)
    assert (package / "runtime" / "rmdy_tool_worker.py").is_file()
    assert (tmp_path / "__main__.py").is_file()
    bw.assert_no_http_server_bundle(tmp_path)
    bw.assert_worker_allowlist(tmp_path)
    for name in bw.BANNED_SERVER_MODULE_NAMES:
        assert not (package / name).exists()
        assert not (tmp_path / name).exists()


def test_stage_excludes_agent_tool_registration(bw, tmp_path) -> None:
    package = bw.stage_worker_tree(tmp_path)
    # Absolute worker allowlist: no agent_*_tools registration forest.
    leftovers = sorted(
        p.relative_to(package).as_posix()
        for p in package.rglob("agent_*_tools.py")
    )
    assert leftovers == []
    assert not (package / "core" / "agent_mcp_bridge.py").exists()
    assert (package / "core" / "web_helpers.py").is_file()


def test_write_pyinstaller_spec_excludes_server(bw, tmp_path) -> None:
    spec_path = tmp_path / "rmdy_tool_worker.spec"
    bw.write_pyinstaller_spec(spec_path)
    text = spec_path.read_text(encoding="utf-8")
    assert "rmdy_tool_worker" in text
    assert "exclude_binaries=True" in text  # onedir COLLECT pattern
    for name in ("fastapi", "uvicorn", "starlette"):
        assert repr(name) in text
    assert "Analysis(" in text
    assert "COLLECT(" in text


def test_build_zipapp_smoke(bw, tmp_path) -> None:
    out = tmp_path / "rmdy_tool_worker.pyz"
    stage = tmp_path / "stage"
    built = bw.build_zipapp(out, stage_parent=stage)
    assert built.is_file()
    assert built.stat().st_size > 0
    # Zipapp is a zip file; ensure worker module path is inside.
    import zipfile

    with zipfile.ZipFile(built) as zf:
        names = zf.namelist()
    assert any(n.replace("\\", "/").endswith("runtime/rmdy_tool_worker.py") for n in names)
    assert not any(
        any(banned in n.replace("\\", "/").split("/") for banned in bw.BANNED_SERVER_MODULE_NAMES)
        for n in names
    )
