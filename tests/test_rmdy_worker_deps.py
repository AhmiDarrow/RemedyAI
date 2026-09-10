"""The packaged RMDY worker finds its third-party closure beside the zipapp.

A packaged install runs a managed CPython with only the standard library, so
``pydantic``/``PyYAML`` ship in a staged ``rmdy-deps`` directory that the Go
launcher passes as ``REMEDY_RMDY_DEPS``. These tests cover the pins, the
sys.path bootstrap, the missing-dependency exit code, and — when the directory
has actually been built — a real stdlib-only import of the worker.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from remedy.runtime import rmdy_tool_worker as worker

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "scripts" / "build_rmdy_worker.py"


def _load_build_script():
    spec = importlib.util.spec_from_file_location("build_rmdy_worker_deps", BUILD_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bw():
    return _load_build_script()


def _staged_pair() -> tuple[Path, Path] | None:
    """A built ``(zipapp, deps)`` pair, or None when packaging has not run."""
    for parent in (ROOT / "dist", ROOT / "desktop" / "bin"):
        pyz = parent / "rmdy_tool_worker.pyz"
        deps = parent / "rmdy-deps"
        if pyz.is_file() and deps.is_dir():
            return pyz, deps
    return None


def test_dependency_versions_come_from_uv_lock(bw) -> None:
    series = bw.managed_python_series()
    requirements = bw.deps_requirements(series)
    assert requirements, "worker dependency closure must not be empty"
    locked = bw.locked_versions(bw.deps_distributions(series))
    for requirement in requirements:
        name, _, version = requirement.partition("==")
        assert version, f"{requirement} must pin an exact version"
        assert locked[name] == version


def test_managed_python_series_decides_whether_tomli_ships(bw) -> None:
    """3.11+ has tomllib; only an older worker interpreter needs tomli."""
    series = bw.managed_python_series()
    major, minor = (int(part) for part in series.split(".", 1))
    ships_tomli = bw.TOMLI_DISTRIBUTION in bw.deps_distributions(series)
    assert ships_tomli == ((major, minor) < (3, 11))
    assert bw.TOMLI_DISTRIBUTION in bw.deps_distributions("3.10")
    assert bw.TOMLI_DISTRIBUTION not in bw.deps_distributions("3.12")


def test_worker_and_builder_agree_on_the_import_closure(bw) -> None:
    assert set(worker._REQUIRED_THIRD_PARTY) <= set(bw.DEPS_IMPORT_NAMES)


def test_exit_code_matches_the_go_supervisor() -> None:
    go_source = (ROOT / "native" / "go" / "workers" / "rmdy_worker.go").read_text("utf-8")
    assert f"ExitMissingDependencies = {worker.EXIT_MISSING_DEPENDENCIES}" in go_source
    assert 'envRMDYDeps     = "REMEDY_RMDY_DEPS"' in go_source


def test_bootstrap_appends_the_deps_directory_last(tmp_path, monkeypatch) -> None:
    """A developer venv must keep winning: the directory is additive only."""
    deps = tmp_path / "rmdy-deps"
    deps.mkdir()
    monkeypatch.setattr(sys, "path", ["/first", "/second"])
    resolved = worker.bootstrap_dependency_path({"REMEDY_RMDY_DEPS": str(deps)})
    assert resolved == deps
    assert sys.path[-1] == str(deps)
    # Idempotent: a second call does not stack duplicates.
    worker.bootstrap_dependency_path({"REMEDY_RMDY_DEPS": str(deps)})
    assert sys.path.count(str(deps)) == 1


def test_bootstrap_ignores_unset_and_missing_directories(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "path", ["/first"])
    assert worker.bootstrap_dependency_path({}) is None
    assert worker.bootstrap_dependency_path({"REMEDY_RMDY_DEPS": "   "}) is None
    missing = tmp_path / "not-here"
    assert worker.bootstrap_dependency_path({"REMEDY_RMDY_DEPS": str(missing)}) is None
    assert sys.path == ["/first"]


def test_missing_dependency_exits_with_the_distinct_code(monkeypatch) -> None:
    import importlib

    def _boom(name: str):
        raise ModuleNotFoundError(f"No module named {name!r}")

    monkeypatch.setattr(importlib, "import_module", _boom)
    with pytest.raises(SystemExit) as excinfo:
        worker.verify_dependencies()
    assert excinfo.value.code == worker.EXIT_MISSING_DEPENDENCIES


def test_verify_dependencies_passes_in_this_environment() -> None:
    worker.verify_dependencies()


def test_staged_deps_manifest_is_complete(bw) -> None:
    staged = _staged_pair()
    if staged is None:
        pytest.skip("run scripts/build_rmdy_worker.py to stage the deps directory")
    _, deps = staged
    bw.assert_deps_complete(deps)
    manifest = json.loads((deps / bw.DEPS_MANIFEST_NAME).read_text("utf-8"))
    assert manifest["python_version"] == bw.managed_python_series()
    assert manifest["requirements"] == bw.deps_requirements(manifest["python_version"])


def test_worker_imports_with_only_the_staged_deps_on_sys_path(bw, tmp_path) -> None:
    """The packaged path: no site-packages, zipapp + deps directory only."""
    staged = _staged_pair()
    if staged is None:
        pytest.skip("run scripts/build_rmdy_worker.py to stage the deps directory")
    pyz, deps = staged
    series = f"{sys.version_info.major}.{sys.version_info.minor}"
    if series != bw.managed_python_series():
        pytest.skip("staged wheels target the managed CPython series, not this one")
    suffix = ".pyd" if sys.platform == "win32" else ".so"
    native = list((deps / "pydantic_core").glob("_pydantic_core*"))
    if not any(path.name.endswith(suffix) for path in native):
        pytest.skip("staged rmdy-deps wheels are for another OS (Windows checkout under WSL)")
    program = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from remedy.runtime.rmdy_tool_worker import bootstrap_dependency_path, verify_dependencies\n"
        "assert bootstrap_dependency_path() is not None\n"
        "verify_dependencies()\n"
        "from remedy.runtime.rmdy_tool_worker import _HANDLERS\n"
        "out = _HANDLERS[('prompt.assemble', 1)]({\n"
        "    'message': 'attach probe', 'session_id': 'deps-probe',\n"
        "    'chat_mode': True, 'home_dir': sys.argv[2], '_go_bound': True})\n"
        "assert out.get('system'), out\n"
        "print('ok')\n"
    )
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ)
    env["REMEDY_HOME"] = str(home)
    env["REMEDY_RMDY_DEPS"] = str(deps)
    # -S drops site-packages and -E drops PYTHONPATH: what the managed
    # interpreter sees on a packaged machine.
    proc = subprocess.run(
        [sys.executable, "-S", "-E", "-c", program, str(pyz), str(home)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ok" in proc.stdout
