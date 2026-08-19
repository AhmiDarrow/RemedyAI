"""`remedy desktop …` — dependency install, dev server, build, launch, status.

Nothing here actually runs npm or starts the app: every subprocess call is
intercepted. What is being checked is that each subcommand reaches the right
command line, and that a failure surfaces as a non-zero exit instead of a
cheerful green message over a build that did not happen.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

from remedy.interfaces.cli import cmd_runtime as CR


class FakeCompleted:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


@pytest.fixture()
def npm(monkeypatch):
    """Pretend npm exists, and record what we would have run."""
    calls: list[dict] = []
    monkeypatch.setattr(CR, "_find_npm", lambda: "npm")

    def fake_run(cmd, **kw):
        calls.append({"cmd": list(cmd), "cwd": kw.get("cwd")})
        return FakeCompleted(fake_run.rc)

    fake_run.rc = 0
    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls, fake_run


def _args(**kw):
    return argparse.Namespace(desktop_cmd=kw.pop("cmd", None), **kw)


# --- subcommand dispatch ----------------------------------------------------


def test_install_runs_npm_install(npm):
    calls, _ = npm
    CR._cmd_desktop(_args(cmd="install"))
    assert calls[0]["cmd"] == ["npm", "install"]
    assert calls[0]["cwd"].endswith("desktop")


def test_install_is_the_default_subcommand(npm):
    calls, _ = npm
    CR._cmd_desktop(_args(cmd=None))
    assert calls[0]["cmd"] == ["npm", "install"]


def test_a_failed_install_exits_nonzero(npm):
    calls, run = npm
    run.rc = 7
    with pytest.raises(SystemExit) as exc:
        CR._cmd_desktop(_args(cmd="install"))
    assert exc.value.code == 7


def test_dev_starts_the_vite_server(npm):
    calls, _ = npm
    CR._cmd_desktop(_args(cmd="dev", open=False))
    assert calls[0]["cmd"] == ["npm", "run", "dev"]


def test_dev_can_open_a_browser(npm):
    calls, _ = npm
    CR._cmd_desktop(_args(cmd="dev", open=True))
    assert calls[0]["cmd"] == ["npm", "run", "dev", "--", "--open"]


def test_a_failed_dev_server_exits_nonzero(npm):
    _, run = npm
    run.rc = 1
    with pytest.raises(SystemExit):
        CR._cmd_desktop(_args(cmd="dev", open=False))


def test_build_runs_the_production_build(npm):
    calls, _ = npm
    CR._cmd_desktop(_args(cmd="build"))
    assert calls[0]["cmd"] == ["npm", "run", "build"]


def test_a_failed_build_does_not_report_success(npm, capsys):
    _, run = npm
    run.rc = 2
    with pytest.raises(SystemExit) as exc:
        CR._cmd_desktop(_args(cmd="build"))
    assert exc.value.code == 2
    assert "Desktop built to" not in capsys.readouterr().out


def test_an_unknown_subcommand_lists_the_real_ones(npm, capsys):
    CR._cmd_desktop(_args(cmd="frobnicate"))
    out = capsys.readouterr().out
    assert "install" in out and "launch" in out and "status" in out


def test_launch_and_status_are_dispatched(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(CR, "_desktop_launch", lambda: seen.append("launch"))
    monkeypatch.setattr(CR, "_desktop_status", lambda: seen.append("status"))
    CR._cmd_desktop(_args(cmd="launch"))
    CR._cmd_desktop(_args(cmd="status"))
    assert seen == ["launch", "status"]


# --- finding a package manager ----------------------------------------------


def test_npm_is_preferred(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/bin/npm" if n == "npm" else None)
    assert CR._find_npm() == "/bin/npm"


def test_pnpm_and_yarn_are_accepted_fallbacks(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda n: "/bin/yarn" if n == "yarn" else None)
    assert CR._find_npm() == "/bin/yarn"


def test_no_package_manager_says_where_to_get_one(monkeypatch, capsys):
    monkeypatch.setattr("shutil.which", lambda n: None)
    with pytest.raises(SystemExit) as exc:
        CR._find_npm()
    assert exc.value.code == 1
    assert "nodejs.org" in capsys.readouterr().out


# --- launching the installed app --------------------------------------------


def test_launch_starts_the_installed_binary(monkeypatch, tmp_path):
    exe = (
        tmp_path / "AppData" / "Local" / "Programs" / "Remedy Desktop"
        / "Remedy Desktop.exe"
    )
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    started: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    CR._desktop_launch()
    assert started and started[0][0].endswith("Remedy Desktop.exe")


def test_launch_on_a_non_windows_host_points_at_the_installer(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as exc:
        CR._desktop_launch()
    assert exc.value.code == 1
    assert "releases" in capsys.readouterr().out


def test_launch_without_an_install_says_where_to_download(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("no app"))
    with pytest.raises(SystemExit):
        CR._desktop_launch()
    assert "releases" in capsys.readouterr().out


# --- server status ----------------------------------------------------------


def test_status_reports_online_when_the_port_answers(monkeypatch, capsys):
    class Sock:
        def settimeout(self, _t): pass
        def connect_ex(self, _addr): return 0
        def close(self): pass

    monkeypatch.setattr("socket.socket", lambda *a, **kw: Sock())
    CR._desktop_status()
    assert "Online" in capsys.readouterr().out


def test_status_reports_offline_when_nothing_is_listening(monkeypatch, capsys):
    class Sock:
        def settimeout(self, _t): pass
        def connect_ex(self, _addr): return 1
        def close(self): pass

    monkeypatch.setattr("socket.socket", lambda *a, **kw: Sock())
    CR._desktop_status()
    assert "Offline" in capsys.readouterr().out


def test_status_survives_a_broken_socket_stack(monkeypatch, capsys):
    def boom(*a, **kw):
        raise OSError("no network stack")

    monkeypatch.setattr("socket.socket", boom)
    CR._desktop_status()
    assert "Error" in capsys.readouterr().out
