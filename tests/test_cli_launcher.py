"""The ``remedy`` launcher hands every command to Go remedy-runtime."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from remedy.interfaces.cli import launcher


def _fake_runtime(tmp_path: Path, record: dict) -> Path:
    script = tmp_path / "remedy-runtime"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "if sys.argv[1:] == ['--probe']:\n"
        f"    print(json.dumps({json.dumps(record)!s}))\n"
        "    raise SystemExit(0)\n"
        "out = os.environ['FAKE_OUT']\n"
        "open(out, 'w').write(json.dumps({'argv': sys.argv[1:], 'ver': os.environ.get('REMEDY_CLI_VERSION', '')}))\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.mark.skipif(os.name != "posix", reason="fake runtime is a shebang script")
def test_runtime_with_cli_gets_argv_and_version(tmp_path, monkeypatch):
    rt = _fake_runtime(tmp_path, {"status": "ready", "protocol": 1, "tool_abi": 1, "cli": 1})
    out = tmp_path / "out.json"
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(rt))
    monkeypatch.setenv("FAKE_OUT", str(out))
    # execve would replace pytest; run the subprocess branch instead.
    monkeypatch.setattr(launcher.os, "name", "nt")
    with pytest.raises(SystemExit) as ei:
        launcher.main(["--home", str(tmp_path / "h"), "tool", "list"])
    assert ei.value.code == 7
    got = json.loads(out.read_text(encoding="utf-8"))
    assert got["argv"] == ["--home", str(tmp_path / "h"), "tool", "list"]
    assert got["ver"]


@pytest.mark.skipif(os.name != "posix", reason="fake runtime is a shebang script")
def test_old_runtime_without_cli_is_reported(tmp_path, monkeypatch, capsys):
    rt = _fake_runtime(tmp_path, {"status": "ready", "protocol": 1, "tool_abi": 1})
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(rt))
    with pytest.raises(SystemExit) as ei:
        launcher.main(["tool", "list"])
    assert ei.value.code == 1
    assert "too old" in capsys.readouterr().err


def test_missing_runtime_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(launcher, "_runtime_command", lambda: (None, None))
    with pytest.raises(SystemExit) as ei:
        launcher.main(["tool", "list"])
    assert ei.value.code == 1
    assert "remedy-runtime was not found" in capsys.readouterr().err


def test_no_arguments_asks_the_runtime_for_help(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(launcher, "_runtime_command", lambda: (["rt"], None))
    monkeypatch.setattr(launcher, "runtime_has_cli", lambda *a: True)
    monkeypatch.setattr(launcher.os, "name", "nt")
    monkeypatch.setattr(launcher.subprocess, "call", lambda full, **kw: seen.append(full) or 0)
    with pytest.raises(SystemExit) as ei:
        launcher.main([])
    assert ei.value.code == 0
    assert seen == [["rt", "--help"]]


def test_child_env_names_this_python_for_pip(monkeypatch):
    monkeypatch.delenv("REMEDY_CLI_PYTHON", raising=False)
    monkeypatch.delenv("REMEDY_CLI_PACKAGE_DIR", raising=False)
    env = launcher.child_env(None)
    assert env["REMEDY_CLI_PYTHON"] == os.path.abspath(sys.executable)
    assert (Path(env["REMEDY_CLI_PACKAGE_DIR"]) / "__init__.py").is_file()


def test_frozen_app_leaves_pip_unset(monkeypatch):
    monkeypatch.delenv("REMEDY_CLI_PYTHON", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "REMEDY_CLI_PYTHON" not in launcher.child_env(None)
