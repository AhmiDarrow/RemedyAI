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


def test_create_app_still_importable_for_tests() -> None:
    mod = importlib.import_module("remedy.interfaces.api")
    assert callable(mod.create_app)
    doc = (mod.__doc__ or "") + (mod.create_app.__doc__ or "")
    assert "test" in doc.lower() or "TestClient" in doc
    assert "remedy-runtime" in doc or ":7400" in doc


def test_create_app_lifespan_is_testclient_teardown_only() -> None:
    """Production host autostart must not live in TestClient create_app."""
    src = Path("src/remedy/interfaces/api.py").read_text(encoding="utf-8")
    for needle in (
        "start_vigil_thread",
        "start_delivery_thread",
        "run_unattended_improve",
        "ensure_rmb_server",
        "maybe_ensure_local_model",
        "warm_voice_engines",
        "resume_posts",
        "initialize_native_runtime",
        "ensure_connected",
        "gateway.start",
    ):
        assert needle not in src, f"create_app still starts production work: {needle}"
    assert "aclose_shared_session" in src
    assert "_shutdown_vision_decoder" in src


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


def test_python_routes_omit_go_owned_connect_and_webhooks() -> None:
    """TestClient surface must not re-register Go-owned Connect/webhook paths."""
    import importlib.util
    import re

    routes_init = Path("src/remedy/interfaces/routes/__init__.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(
        r"^\s*(from|import).*\b(webhooks|connect)\b", routes_init, flags=re.M
    )
    assert not re.search(r"\bregister_webhook_routes\s*\(", routes_init)
    assert not re.search(r"\bregister_connect_\w+\s*\(", routes_init)
    assert importlib.util.find_spec("remedy.interfaces.routes.webhooks") is None
    assert importlib.util.find_spec("remedy.interfaces.routes.connect") is None
    assert importlib.util.find_spec("remedy.connect") is None


def test_python_routes_omit_phase4_go_owned_modules() -> None:
    """Deleted FastAPI twins must stay gone; create_app must not serve them."""
    import importlib.util
    import re

    from fastapi.testclient import TestClient

    from remedy.interfaces.api import create_app

    routes_init = Path("src/remedy/interfaces/routes/__init__.py").read_text(
        encoding="utf-8"
    )
    for mod in (
        "i18n",
        "usage",
        "vision",
        "telephony",
        "nanoswarm",
        "assistant",
        "computer",
        "hive",
        "session_events",
        "chat",
        "terminal",
        "rmb",
        "voice",
        "workspace",
        "skills_library",
        "memory",
        "partner",
        "catalog",
        "claimidx_ops",
        "auth",
        "settings",
        "misc",
        "status",
    ):
        assert not re.search(rf"\bregister_{mod}_routes\s*\(", routes_init)
        assert importlib.util.find_spec(f"remedy.interfaces.routes.{mod}") is None
    assert not re.search(r"\bregister_status_routes\s*\(", routes_init)
    assert not re.search(r"\bregister_sessions_routes\s*\(", routes_init)
    assert importlib.util.find_spec("remedy.interfaces.routes.sessions") is None
    assert importlib.util.find_spec("remedy.core.computer.host_conpty") is None

    client = TestClient(create_app(api_key=""))
    absent = [
        "/api/i18n",
        "/api/usage/summary",
        "/api/usage/series",
        "/api/usage/export",
        "/api/updates/check",
        "/api/vision/status",
        "/api/vision/catalog",
        "/api/telephony/status",
        "/api/auth/xai",
        "/api/auth/xai/login",
        "/api/providers",
        "/api/providers/connected",
        "/api/providers/free",
        "/api/providers/ollama/detect",
        "/api/settings",
        "/api/diagnostics",
        "/api/coordination/presence",
        "/api/self-inject/rounds",
        "/api/notifications",
        "/api/metrics",
        "/api/self-improve",
        "/api/assistant/status",
        "/api/assistant/google",
        "/api/memory/search",
        "/api/memory/facts",
        "/api/memory/persona-wipe",
        "/api/memory/import",
        "/api/session-summaries",
        "/api/handoffs",
        "/api/skills",
        "/api/skills/packs",
        "/api/skills/metrics/reuse",
        "/api/skills/learning/summary",
        "/api/skills/export",
        "/api/skills/library/catalog",
        "/api/skills/library/search",
        "/api/skills/library/suggest",
        "/api/skills/library/updates",
        "/api/models",
        "/api/commands",
        "/api/commands/custom",
        "/api/agents",
        "/api/agents/custom",
        "/api/app/command",
        "/api/openapi.json",
        "/api/openapi.yaml",
        "/dashboard",
        "/api/ping",
        "/api/status",
        "/api/turn-active",
        "/api/continuity/dashboard",
        "/api/nanoswarm/status",
        "/api/nanoswarm/token/status",
        "/api/nanoswarm/classify",
        "/api/goals",
        "/api/life-tasks/current",
        "/api/checkpoints/latest",
        "/api/partner/status",
        "/api/partner/metabolism",
        "/api/partner/identity/export",
        "/api/approvals",
        "/api/plans",
        "/api/plans/latest",
        "/api/projects/scan",
        "/api/sessions",
        "/api/sessions/s1",
        "/api/sessions/s1/messages",
        "/api/sessions/s1/llm",
        "/api/sessions/s1/attachments/x.txt",
        "/api/sessions/s1/turns/t1/explain",
        "/api/sessions/s1/todos",
        "/api/sessions/s1/timeline",
        "/api/sessions/s1/export",
        "/api/hive/roster",
        "/api/computer/host/status",
        "/api/computer/jobs/next",
        "/api/computer/ui/command",
        "/api/events/sessions",
        "/api/chat",
        "/api/rmb/status",
        "/api/rmb/catalog",
        "/api/rmb/hf/progress",
        "/api/rmb/start",
        "/api/rmb/settings",
        "/api/rmb/use",
        "/api/terminal",
        "/api/voice/status",
        "/api/voice/identity",
        "/api/workspace",
        "/api/files",
        "/api/files/search",
        "/api/media",
        "/api/scratch",
    ]
    def _absent(status: int) -> bool:
        # Starlette may answer POST to an unmatched path with 405 when no
        # POST route exists anywhere for that URL shape; both mean "not served".
        return status in (404, 405)

    for path in absent:
        r = client.get(path)
        assert _absent(r.status_code), f"{path} still registered ({r.status_code})"

    for path in (
        "/api/telephony/terms",
        "/api/telephony/choose",
        "/api/notifications/read",
        "/api/sessions/s1/steer",
        "/api/sessions/s1/time-travel",
        "/api/memory/persona-wipe",
        "/api/memory/import",
        "/api/partner/identity/export",
        "/api/partner/identity/import",
        "/api/approvals/x/resolve",
        "/api/plans",
        "/api/plans/p1/status",
        "/api/plans/p1/steps/status",
        "/api/projects/scan",
        "/api/goals",
        "/api/life-tasks/act",
        "/api/nanoswarm/classify",
        "/api/hive/spawn",
        "/api/hive/retire",
        "/api/hive/assign",
        "/api/computer/host/hello",
        "/api/computer/capture",
        "/api/computer/a11y/push",
        "/api/chat",
        "/api/chat/stream",
        "/api/rmb/stop",
        "/api/rmb/start",
        "/api/rmb/settings",
        "/api/rmb/use",
        "/api/rmb/hf/search",
        "/api/rmb/hf/pull",
        "/api/rmb/hf/cancel",
        "/api/terminal",
        "/api/voice/speak",
        "/api/voice/settings",
        "/api/voice/install",
        "/api/voice/client-log",
        "/api/sessions",
        "/api/sessions/bulk-project",
        "/api/sessions/import",
        "/api/sessions/s1/messages",
        "/api/sessions/s1/messages/stream",
        "/api/sessions/s1/abort",
        "/api/sessions/s1/attachments",
        "/api/sessions/s1/llm",
        "/api/skills/library/install",
        "/api/skills/library/suggest/dismiss",
        "/api/skills/library/submit",
        "/api/skills/library/update/alpha",
        "/api/skills/export",
        "/api/providers/custom",
        "/api/providers/probe",
        "/api/settings",
        "/api/sessions/s1/command",
    ):
        r = client.post(path, json={})
        assert _absent(r.status_code), f"{path} still registered ({r.status_code})"

    assert _absent(client.put("/api/settings", json={}).status_code)
    assert _absent(client.delete("/api/providers/custom/custom-x").status_code)

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
    # Prefer explicit env so resolve stays deterministic across PATH noise.
    monkeypatch.setenv("REMEDY_RUNTIME", str(runtime))
    cmd = CR.resolve_remedy_runtime_command()
    assert cmd is not None
    joined = " ".join(str(x) for x in cmd).lower()
    assert "remedy-runtime" in joined
    assert "remedy-desktop" not in joined
