"""The ``remedy`` launcher hands ported commands to Go remedy-runtime."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from remedy.interfaces.cli import launcher


def test_command_word_skips_home():
    assert launcher.command_word(["--home", "/x", "tool", "list"]) == "tool"
    assert launcher.command_word(["--home=/x", "exec", "--", "ls"]) == "exec"
    assert launcher.command_word([]) == ""


def test_only_ported_commands_go_to_go():
    assert launcher.wants_go(["tool", "list"])
    assert launcher.wants_go(["--home", "/h", "serve"])
    assert not launcher.wants_go([])
    assert not launcher.wants_go(["--version"])


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
def test_old_runtime_without_cli_falls_back_to_python(tmp_path, monkeypatch):
    rt = _fake_runtime(tmp_path, {"status": "ready", "protocol": 1, "tool_abi": 1})
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(rt))
    seen: list[list[str]] = []
    monkeypatch.setattr(launcher, "_python_main", lambda a: seen.append(a))
    launcher.main(["tool", "list"])
    assert seen == [["tool", "list"]]


def test_unported_command_stays_in_python(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(launcher, "_python_main", lambda a: seen.append(a))
    monkeypatch.setattr(launcher, "_runtime_command", lambda: pytest.fail("looked for the runtime"))
    launcher.main(["config", "zzz-not-ported"])
    assert seen == [["config", "zzz-not-ported"]]
