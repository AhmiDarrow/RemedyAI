"""Production :7400 is owned by remedy-runtime — Python serve must not uvicorn."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.interfaces.cli.cmd_runtime import (
    build_runtime_serve_argv,
    resolve_remedy_runtime_command,
)
from remedy.interfaces.cli.parser import build_parser


def test_serve_parser_hides_retired_python_flags() -> None:
    """Uvicorn-era serve flags stay accepted for argv compat but are not advertised."""
    from remedy.interfaces.cli.parser import build_parser

    parser = build_parser()
    serve = None
    for action in parser._actions:  # noqa: SLF001
        if getattr(action, "choices", None) and "serve" in action.choices:
            serve = action.choices["serve"]
            break
    assert serve is not None
    help_text = serve.format_help()
    assert "remedy-runtime" in help_text
    assert "--computer-host" not in help_text
    assert "--force-setup" not in help_text
    assert "--skip-setup" not in help_text
    # Still parse so Desktop/scripts argv keep working.
    ns = parser.parse_args(["serve", "--skip-setup", "--computer-host"])
    assert ns.command == "serve"
    assert ns.skip_setup is True
    assert ns.computer_host is True


def test_parser_still_exposes_serve() -> None:
    ns = build_parser().parse_args(["serve", "--host", "127.0.0.1", "--port", "7410"])
    assert ns.command == "serve"
    assert ns.host == "127.0.0.1"
    assert ns.port == 7410


def test_build_runtime_serve_argv_default_is_bare_serve() -> None:
    assert build_runtime_serve_argv(["remedy-runtime"]) == [
        "remedy-runtime",
        "--serve",
    ]


def test_build_runtime_serve_argv_non_default_uses_listen() -> None:
    assert build_runtime_serve_argv(
        ["/bin/remedy-runtime"], host="127.0.0.1", port=7410
    ) == ["/bin/remedy-runtime", "--serve", "--listen", "127.0.0.1:7410"]


def test_cmd_serve_dispatches_to_runtime(monkeypatch, tmp_path) -> None:
    import remedy.interfaces.cli.cmd_runtime as CR

    called: dict[str, object] = {}

    def fake_call(cmd, *args, **kwargs):
        called["cmd"] = list(cmd)
        called["cwd"] = kwargs.get("cwd")
        return 0

    monkeypatch.setenv(
        "REMEDY_NATIVE_RUNTIME_BIN", str(tmp_path / "remedy-runtime.exe")
    )
    monkeypatch.setattr(CR.subprocess, "call", fake_call)

    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="127.0.0.1",
                port=7400,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 0
    assert called["cmd"] == [
        str(tmp_path / "remedy-runtime.exe"),
        "--serve",
    ]


def test_cmd_serve_passes_listen_for_custom_port(monkeypatch, tmp_path) -> None:
    import remedy.interfaces.cli.cmd_runtime as CR

    called: dict[str, object] = {}

    def fake_call(cmd, *args, **kwargs):
        called["cmd"] = list(cmd)
        return 0

    monkeypatch.setenv("REMEDY_RUNTIME", str(tmp_path / "remedy-runtime"))
    monkeypatch.setattr(CR.subprocess, "call", fake_call)

    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="127.0.0.1",
                port=7411,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 0
    assert called["cmd"] == [
        str(tmp_path / "remedy-runtime"),
        "--serve",
        "--listen",
        "127.0.0.1:7411",
    ]


def test_cmd_serve_hard_fails_without_runtime(monkeypatch, tmp_path, capsys) -> None:
    import remedy.interfaces.cli.cmd_runtime as CR

    monkeypatch.delenv("REMEDY_NATIVE_RUNTIME_BIN", raising=False)
    monkeypatch.delenv("REMEDY_RUNTIME", raising=False)
    monkeypatch.setattr(CR, "resolve_remedy_runtime_command", lambda: None)

    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="127.0.0.1",
                port=7400,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 2
    out = capsys.readouterr().out
    assert "remedy-runtime" in out.lower()
    assert "dual-serve" in out.lower() or "no longer" in out.lower()


def test_cmd_serve_refuses_non_loopback(monkeypatch, tmp_path, capsys) -> None:
    import remedy.interfaces.cli.cmd_runtime as CR

    monkeypatch.setenv("REMEDY_RUNTIME", str(tmp_path / "remedy-runtime"))
    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="0.0.0.0",
                port=7400,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 2
    assert "loopback" in capsys.readouterr().out.lower()


def test_cmd_serve_does_not_import_create_app(monkeypatch, tmp_path) -> None:
    """Regression: production serve must not mount FastAPI/uvicorn."""
    import remedy.interfaces.cli.cmd_runtime as CR

    monkeypatch.setenv("REMEDY_RUNTIME", str(tmp_path / "remedy-runtime"))
    monkeypatch.setattr(CR.subprocess, "call", lambda *a, **k: 0)

    real_import = __import__

    def guard(name, *args, **kwargs):
        if name == "remedy.interfaces.api" or name.endswith(".api"):
            raise AssertionError(f"serve must not import {name}")
        if "uvicorn" in name:
            raise AssertionError(f"serve must not import {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guard)
    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="127.0.0.1",
                port=7400,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 0


def test_fastapi_api_module_is_gone() -> None:
    """Phase 4 exit: FastAPI create_app / interfaces.api are deleted."""
    assert importlib.util.find_spec("remedy.interfaces.api") is None
    assert not Path("src/remedy/interfaces/api.py").exists()


def test_fastapi_module_is_not_production_serve_entry() -> None:
    """create_app must not be reachable from the production serve path."""
    import remedy.interfaces.cli.cmd_runtime as CR
    import remedy.interfaces.serve_forensics as SF

    src = Path(CR.__file__).read_text(encoding="utf-8")
    assert "create_app" not in src
    assert "run_uvicorn" not in src
    assert "import uvicorn" not in src
    assert "uvicorn.run" not in src
    assert "from remedy.interfaces.api" not in src
    assert not hasattr(SF, "run_uvicorn_logged")


def test_cmd_serve_argv_never_starts_uvicorn(monkeypatch, tmp_path) -> None:
    """Production ``remedy serve`` must exec remedy-runtime only — never uvicorn."""
    import remedy.interfaces.cli.cmd_runtime as CR

    seen: dict[str, object] = {}

    def fake_call(cmd, *args, **kwargs):
        seen["cmd"] = [str(x) for x in cmd]
        joined = " ".join(seen["cmd"]).lower()
        assert "uvicorn" not in joined
        assert "remedy.interfaces.api" not in joined
        assert any("remedy-runtime" in p for p in seen["cmd"])
        return 0

    monkeypatch.setenv("REMEDY_RUNTIME", str(tmp_path / "remedy-runtime"))
    monkeypatch.setattr(CR.subprocess, "call", fake_call)
    with pytest.raises(SystemExit) as ei:
        CR._cmd_serve(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                host="127.0.0.1",
                port=7400,
                skip_setup=True,
                force_setup=False,
                config_file=None,
                computer_host=False,
                no_computer_host=False,
            )
        )
    assert ei.value.code == 0
    assert seen["cmd"] == [str(tmp_path / "remedy-runtime"), "--serve"]


def test_python_fastapi_routes_package_is_gone() -> None:
    """Deleted FastAPI route twins must stay gone with the routes package."""
    assert importlib.util.find_spec("remedy.interfaces.routes") is None
    assert not Path("src/remedy/interfaces/routes").exists()
    assert importlib.util.find_spec("remedy.connect") is None
    assert importlib.util.find_spec("remedy.core.computer.host_conpty") is None


def test_rmdy_tool_worker_entry_still_present() -> None:
    spec = importlib.util.find_spec("remedy.runtime.rmdy_tool_worker")
    assert spec is not None


def test_resolve_prefers_env_override(monkeypatch, tmp_path) -> None:
    path = tmp_path / "custom-runtime.exe"
    path.write_bytes(b"")
    monkeypatch.setenv("REMEDY_NATIVE_RUNTIME_BIN", str(path))
    assert resolve_remedy_runtime_command() == [str(path)]


def test_resolve_never_returns_remedy_desktop(monkeypatch, tmp_path) -> None:
    """Even with a legacy sidecar on disk, serve must resolve remedy-runtime."""
    import remedy.interfaces.cli.cmd_runtime as CR

    desktop_bin = tmp_path / "desktop" / "bin"
    desktop_bin.mkdir(parents=True)
    (desktop_bin / "remedy-desktop.exe").write_bytes(b"MZ" + b"\0" * 64)
    runtime = desktop_bin / "remedy-runtime.exe"
    runtime.write_bytes(b"MZ" + b"\0" * 64)

    monkeypatch.delenv("REMEDY_NATIVE_RUNTIME_BIN", raising=False)
    monkeypatch.delenv("REMEDY_RUNTIME", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REMEDY_RUNTIME", str(runtime))
    cmd = CR.resolve_remedy_runtime_command()
    assert cmd is not None
    joined = " ".join(str(x) for x in cmd).lower()
    assert "remedy-runtime" in joined
    assert "remedy-desktop" not in joined
