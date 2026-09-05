"""`remedy tool` talks to Go Tool ABI HTTP — no BasicRuntime."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from remedy.interfaces.cli import cmd_skills


def args(**kw):
    return argparse.Namespace(**kw)


@pytest.mark.asyncio
async def test_tool_list_uses_runtime_http(monkeypatch, tmp_path: Path, capsys):
    home = tmp_path / ".remedy"
    home.mkdir()
    calls: list[tuple[str, str]] = []

    def fake_req(method, path, *, home, body=None, timeout=30.0):
        calls.append((method, path))
        return 200, {
            "count": 1,
            "tools": [
                {
                    "id": "runtime.probe",
                    "runtime": "go",
                    "risk": "read_only",
                    "description": "Emit the native runtime probe record",
                }
            ],
        }

    monkeypatch.setattr(cmd_skills, "_tool_runtime_request", fake_req)
    await cmd_skills._cmd_tool(args(tool_cmd="list", home=str(home), timeout=5.0))
    assert calls == [("GET", "/api/tools")]
    assert "runtime.probe" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_tool_run_invokes_tool_abi(monkeypatch, tmp_path: Path, capsys):
    home = tmp_path / ".remedy"
    home.mkdir()
    seen: dict = {}

    def fake_req(method, path, *, home, body=None, timeout=30.0):
        seen["method"] = method
        seen["path"] = path
        seen["body"] = body
        return 200, {"ok": True, "id": "runtime.probe", "output": {"status": "ready"}}

    monkeypatch.setattr(cmd_skills, "_tool_runtime_request", fake_req)
    await cmd_skills._cmd_tool(
        args(
            tool_cmd="run",
            name="runtime.probe",
            tool_args="{}",
            home=str(home),
            timeout=5.0,
        )
    )
    assert seen["method"] == "POST"
    assert seen["path"] == "/api/tools/invoke"
    assert seen["body"] == {"id": "runtime.probe", "input": {}}
    out = capsys.readouterr().out
    assert "Success" in out
    assert "ready" in out


@pytest.mark.asyncio
async def test_tool_run_uses_go_http_not_basic_runtime(monkeypatch, tmp_path: Path):
    """Tool CLI talks to Go Tool ABI over HTTP — never builds BasicRuntime."""
    home = tmp_path / ".remedy"
    home.mkdir()
    assert not hasattr(cmd_skills, "BasicRuntime")

    monkeypatch.setattr(
        cmd_skills,
        "_tool_runtime_request",
        lambda *a, **k: (503, {"detail": "down"}),
    )
    with pytest.raises(SystemExit) as exc:
        await cmd_skills._cmd_tool(
            args(
                tool_cmd="run",
                name="runtime.probe",
                tool_args="{}",
                home=str(home),
                timeout=5.0,
            )
        )
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_tool_unreachable_runtime_exits_2(monkeypatch, tmp_path: Path, capsys):
    home = tmp_path / ".remedy"
    home.mkdir()
    monkeypatch.setattr(cmd_skills, "_tool_runtime_base", lambda: "http://127.0.0.1:9")
    monkeypatch.setenv("REMEDY_API_AUTH", "0")

    import urllib.error
    import urllib.request

    def fail_urlopen(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    with pytest.raises(SystemExit) as exc:
        await cmd_skills._cmd_tool(args(tool_cmd="list", home=str(home), timeout=1.0))
    assert exc.value.code == 2
    assert "remedy serve" in capsys.readouterr().out
