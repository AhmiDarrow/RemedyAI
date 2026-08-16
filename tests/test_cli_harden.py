"""CLI bind, --home jail, exec exit codes, and config redaction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.interfaces.cli.util import (
    UnsafeHomeError,
    evaluate_serve_bind,
    redact_cli_mapping,
    resolve_cli_home,
)


def test_evaluate_serve_bind_loopback_ok():
    for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        assert evaluate_serve_bind(host, has_auth=False, insecure_ok=False) == "ok"
        assert evaluate_serve_bind(host, has_auth=True, insecure_ok=False) == "ok"


def test_evaluate_serve_bind_refuses_open_lan():
    for host in ("0.0.0.0", "::", "*", "192.168.1.10"):
        assert evaluate_serve_bind(host, has_auth=False, insecure_ok=False) == "refuse"


def test_evaluate_serve_bind_warns_when_authed():
    assert evaluate_serve_bind("0.0.0.0", has_auth=True, insecure_ok=False) == "warn"
    assert evaluate_serve_bind("10.0.0.5", has_auth=True, insecure_ok=False) == "warn"


def test_evaluate_serve_bind_insecure_flag_allows():
    assert evaluate_serve_bind("0.0.0.0", has_auth=False, insecure_ok=True) == "ok"


def test_resolve_cli_home_accepts_tmp(tmp_path: Path):
    home = resolve_cli_home(tmp_path / ".remedy")
    assert home.is_dir()
    assert home.name == ".remedy"


def test_resolve_cli_home_refuses_drive_root():
    with pytest.raises(UnsafeHomeError):
        resolve_cli_home("C:\\", mkdir=False)


def test_resolve_cli_home_refuses_windows_tree():
    with pytest.raises(UnsafeHomeError):
        resolve_cli_home("C:\\Windows\\Temp\\remedy-home", mkdir=False)


def test_resolve_cli_home_refuses_file(tmp_path: Path):
    f = tmp_path / "not-a-dir"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(UnsafeHomeError):
        resolve_cli_home(f, mkdir=False)


def test_redact_cli_mapping_strips_api_keys():
    dumped = redact_cli_mapping(
        {
            "name": "Remedy",
            "llm_api_key": "sk-secret-value",
            "api_key": "tok-123",
            "provider_keys": {"xai": "xai-secret"},
            "nested": {"telegram_token": "123:ABC", "model": "grok"},
        }
    )
    assert dumped["name"] == "Remedy"
    assert dumped["llm_api_key"] == "[redacted]"
    assert dumped["api_key"] == "[redacted]"
    assert dumped["nested"]["telegram_token"] == "[redacted]"
    assert dumped["nested"]["model"] == "grok"
    assert dumped["provider_keys"] == "[redacted]"


def test_main_no_command_exits_2():
    from remedy.interfaces.cli.main import main

    with pytest.raises(SystemExit) as ei:
        main([])
    assert ei.value.code == 2


def test_cmd_exec_blocks_dangerous_with_exit_2():
    from remedy.interfaces.cli.cmd_skills import _cmd_exec

    args = SimpleNamespace(cmdline=["format", "C:"], timeout=5.0, workdir=None, shell=None)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(_cmd_exec(args))
    assert ei.value.code == 2


def test_cmd_exec_missing_command_exits_2():
    from remedy.interfaces.cli.cmd_skills import _cmd_exec

    args = SimpleNamespace(cmdline=[], timeout=5.0, workdir=None, shell=None)
    with pytest.raises(SystemExit) as ei:
        asyncio.run(_cmd_exec(args))
    assert ei.value.code == 2


def test_cmd_exec_strips_double_dash_and_propagates_exit(tmp_path: Path):
    from remedy.interfaces.cli.cmd_skills import _cmd_exec

    args = SimpleNamespace(
        cmdline=["--", "python", "-c", "raise SystemExit(7)"],
        timeout=15.0,
        workdir=str(tmp_path),
        shell=None,
    )
    with pytest.raises(SystemExit) as ei:
        asyncio.run(_cmd_exec(args))
    assert ei.value.code == 7


def test_cmd_exec_success_echo():
    from remedy.interfaces.cli.cmd_skills import _cmd_exec

    args = SimpleNamespace(
        cmdline=["python", "-c", "print('ok')"],
        timeout=15.0,
        workdir=None,
        shell=None,
    )
    asyncio.run(_cmd_exec(args))


def test_unsafe_home_via_main(tmp_path: Path):
    from remedy.interfaces.cli.main import main

    with pytest.raises(SystemExit) as ei:
        main(["--home", "C:\\Windows", "config", "path"])
    assert ei.value.code == 2


def test_auth_unknown_provider_exits_2(tmp_path: Path):
    from remedy.interfaces.cli.cmd_settings import _cmd_auth

    args = SimpleNamespace(home=str(tmp_path), provider="openai", auth_cmd="login")
    with pytest.raises(SystemExit) as ei:
        _cmd_auth(args)
    assert ei.value.code == 2


def test_learn_invalid_steps_json_exits_2(tmp_path: Path):
    from remedy.interfaces.cli.cmd_skills import _cmd_learn

    args = SimpleNamespace(
        learn_cmd="reflect",
        steps_json="not-json",
        task_title="t",
    )
    with pytest.raises(SystemExit) as ei:
        asyncio.run(_cmd_learn(args, tmp_path / "memory.db"))
    assert ei.value.code == 2


def test_handoff_show_missing_exits_1(tmp_path: Path):
    from remedy.interfaces.cli.cmd_store import _cmd_handoff

    args = SimpleNamespace(handoff_cmd="show", id="missing-id")
    with pytest.raises(SystemExit) as ei:
        asyncio.run(_cmd_handoff(args, tmp_path / "memory.db"))
    assert ei.value.code == 1


def test_skill_script_jail_rejects_absolute_and_dotdot(tmp_path: Path):
    from remedy.skills.script_path import (
        SkillScriptJailError,
        resolve_jailed_skill_script,
    )

    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts" / "ok.py").write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(SkillScriptJailError):
        resolve_jailed_skill_script(skill, r"C:\evil.py")
    with pytest.raises(SkillScriptJailError):
        resolve_jailed_skill_script(skill, "../evil.py")
    with pytest.raises(SkillScriptJailError):
        resolve_jailed_skill_script(skill, "scripts/../../evil.py")
    got = resolve_jailed_skill_script(skill, "scripts/ok.py")
    assert got == (skill / "scripts" / "ok.py").resolve()
