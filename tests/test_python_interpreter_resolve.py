"""``resolve_python_interpreter`` — the frozen Desktop sidecar is not Python.

``sys.executable`` is ``remedy-desktop.exe`` in the packaged build, so
``run_python_file`` / ``host_script(lang=python)`` used to run
``remedy-desktop.exe script.py`` → ``usage: remedy …`` exit 2.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.workspace_tools import shell as shell_mod
from remedy.core.workspace_tools.shell import (
    _is_python_binary,
    resolve_python_interpreter,
)


@pytest.fixture
def no_host_python(monkeypatch, tmp_path: Path):
    """Frozen sidecar, nothing on PATH, no Windows install dirs, no override."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "remedy-desktop.exe"))
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr("glob.glob", lambda *_a, **_k: [])
    monkeypatch.delenv("REMEDY_PYTHON", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nope"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "nope"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "nope"))
    return tmp_path


def test_is_python_binary_names() -> None:
    assert _is_python_binary(r"C:\Python312\python.exe")
    assert _is_python_binary(r"C:\Users\x\AppData\Local\Programs\Python\Python313\python.EXE")
    assert _is_python_binary("/usr/bin/python3.12")
    assert _is_python_binary("pythonw.exe")
    assert not _is_python_binary(r"C:\Program Files\Remedy Desktop\remedy-desktop.exe")
    assert not _is_python_binary("remedy.exe")
    assert not _is_python_binary("python_notes.txt")
    assert not _is_python_binary("")


def test_unfrozen_real_python_is_sys_executable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python3.12"))
    monkeypatch.delenv("REMEDY_PYTHON", raising=False)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    assert resolve_python_interpreter() == [str(tmp_path / "python3.12")]


def test_frozen_prefers_python_on_path(no_host_python: Path, monkeypatch) -> None:
    py = no_host_python / "python.exe"
    py.write_bytes(b"")
    monkeypatch.setattr(
        shutil, "which", lambda name, *_a, **_k: str(py) if name == "python" else None
    )
    assert resolve_python_interpreter() == [str(py)]


def test_frozen_falls_back_to_py_launcher(no_host_python: Path, monkeypatch) -> None:
    launcher = no_host_python / "py.exe"
    monkeypatch.setattr(
        shutil, "which", lambda name, *_a, **_k: str(launcher) if name == "py" else None
    )
    assert resolve_python_interpreter() == [str(launcher), "-3"]


def test_frozen_falls_back_to_python3(no_host_python: Path, monkeypatch) -> None:
    py3 = no_host_python / "python3"
    monkeypatch.setattr(
        shutil, "which", lambda name, *_a, **_k: str(py3) if name == "python3" else None
    )
    assert resolve_python_interpreter() == [str(py3)]


def test_frozen_skips_store_alias(no_host_python: Path, monkeypatch) -> None:
    alias = no_host_python / "WindowsApps" / "python.exe"
    launcher = no_host_python / "py.exe"

    def _which(name, *_a, **_k):
        return {"python": str(alias), "py": str(launcher)}.get(name)

    monkeypatch.setattr(shutil, "which", _which)
    assert resolve_python_interpreter() == [str(launcher), "-3"]


@pytest.mark.skipif(os.name != "nt", reason="Windows install-dir probe")
def test_frozen_scans_windows_install_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "remedy-desktop.exe"))
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    monkeypatch.delenv("REMEDY_PYTHON", raising=False)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "nope"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "nope"))
    local = tmp_path / "LocalAppData"
    old = local / "Programs" / "Python" / "Python39"
    new = local / "Programs" / "Python" / "Python313"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "python.exe").write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    found = resolve_python_interpreter()
    # C:\Python3*\python.exe on the host would also qualify; the newest
    # LOCALAPPDATA install must win over the older one.
    assert found is not None
    assert found != [str(old / "python.exe")]
    if found[0].lower().startswith(str(local).lower()):
        assert found == [str(new / "python.exe")]


def test_remedy_python_override_wins(no_host_python: Path, monkeypatch) -> None:
    custom = no_host_python / "custom" / "python.exe"
    custom.parent.mkdir()
    custom.write_bytes(b"")
    monkeypatch.setenv("REMEDY_PYTHON", str(custom))
    assert resolve_python_interpreter() == [str(custom)]


def test_frozen_without_python_returns_none(no_host_python: Path) -> None:
    assert resolve_python_interpreter() is None


def test_never_returns_sidecar(no_host_python: Path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert resolve_python_interpreter() is None


# ------------------------------------------------------------ tool behaviour


def _register_shell_runtime(tmp_path: Path, proj: Path, monkeypatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry
    from tests.test_project_write_jail import _make_runtime

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    APPROVALS.set_mode("auto")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "py-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)
    return rt, reg


@pytest.mark.asyncio
async def test_run_python_file_errors_clearly_without_python(
    tmp_path: Path, monkeypatch
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    helper = proj / "hello.py"
    helper.write_text("print('hi')\n", encoding="utf-8")
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    monkeypatch.setattr(shell_mod, "resolve_python_interpreter", lambda: None)
    out = await reg.execute("run_python_file", path=str(helper))
    assert "no Python interpreter found on this host" in out
    assert "REMEDY_PYTHON" in out
    assert "usage: remedy" not in out


@pytest.mark.asyncio
async def test_run_python_file_uses_resolved_interpreter(
    tmp_path: Path, monkeypatch
) -> None:
    """A frozen sidecar must still run the script with a real interpreter."""
    proj = tmp_path / "proj"
    proj.mkdir()
    helper = proj / "hello.py"
    helper.write_text("print('hi from real python')\n", encoding="utf-8")
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    real = sys.executable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "remedy-desktop.exe"))
    monkeypatch.setenv("REMEDY_PYTHON", real)
    out = await reg.execute("run_python_file", path=str(helper))
    assert "exit_code=0" in out, out
    assert "hi from real python" in out
    assert f"python={real}" in out


@pytest.mark.asyncio
async def test_host_script_python_readonly_body_not_jailed(
    tmp_path: Path, monkeypatch
) -> None:
    """A Python body that only reads (regex with ``y:\\s*``) must run."""
    from remedy.execution.host.session import close_all_shared_sessions

    proj = tmp_path / "proj"
    proj.mkdir()
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    try:
        body = (
            "import re, sys\n"
            "m = re.search(r'y:\\s*(\\d+)', 'y: 42')\n"
            "print('C:\\n', m.group(1), sys.version_info[0])\n"
        )
        out = await reg.execute("host_script", lang="python", body=body)
        assert "WRITE_JAIL" not in out, out
        assert "exit_code=0" in out, out
        assert "42" in out
    finally:
        await close_all_shared_sessions()
