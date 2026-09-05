"""Zig shell-chain abort flag must cancel without a Python communicate twin."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from remedy.execution.result import SubprocessSandbox


@pytest.mark.asyncio
async def test_abort_before_start_returns_aborted(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.turn_context.is_turn_aborted",
        lambda: True,
    )
    monkeypatch.setattr(
        "remedy.execution.result._install_write_roots",
        lambda roots: None,
    )
    monkeypatch.setattr(
        "remedy.execution.result._clear_write_roots",
        lambda: None,
    )
    res = await SubprocessSandbox().execute(["python", "-c", "print(1)"])
    assert res.exit_code == -1
    assert "Aborted before start" in res.stderr


@pytest.mark.asyncio
async def test_sandbox_clears_write_roots_after_execute(monkeypatch):
    """Installed jail roots must not leak into later Full-mode spawns."""
    calls: list[str] = []
    monkeypatch.setattr(
        "remedy.execution.result._install_write_roots",
        lambda roots: calls.append(f"install:{len(roots)}"),
    )
    monkeypatch.setattr(
        "remedy.execution.result._clear_write_roots",
        lambda: calls.append("clear"),
    )
    monkeypatch.setattr(
        "remedy.core.turn_context.is_turn_aborted",
        lambda: True,
    )
    res = await SubprocessSandbox(allowed_paths=[Path("/tmp/jail")]).execute(
        ["python", "-c", "print(1)"]
    )
    assert res.exit_code == -1
    assert calls == ["install:1", "clear"]


@pytest.mark.asyncio
async def test_shell_chain_abort_flag_is_set(monkeypatch):
    """Abort event flips the ctypes flag passed into Zig shell_chain_execute."""
    seen: dict[str, object] = {}

    def fake_execute(payload, *, abort_flag=None):
        seen["payload"] = payload
        seen["flag"] = abort_flag
        if abort_flag is not None:
            abort_flag.value = 1
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "Aborted (session stop)",
            "duration_ms": 1.0,
        }

    abort = asyncio.Event()
    abort.set()
    monkeypatch.setattr(
        "remedy.core.turn_context.is_turn_aborted",
        lambda: False,
    )
    monkeypatch.setattr(
        "remedy.core.turn_context.current_abort_event",
        lambda: abort,
    )
    monkeypatch.setattr(
        "remedy.execution.result._install_write_roots",
        lambda roots: None,
    )
    monkeypatch.setattr(
        "remedy.execution.result._clear_write_roots",
        lambda: None,
    )
    monkeypatch.setattr(
        "remedy.core.computer.host_binding.shell_chain_execute",
        fake_execute,
    )

    argv = ["cmd.exe", "/c", "echo a && echo b"]
    res = await SubprocessSandbox().execute(argv, timeout_seconds=5.0)
    assert seen.get("flag") is not None
    assert int(getattr(seen["flag"], "value", 0)) == 1
    assert res.exit_code == -1
    assert "Aborted" in res.stderr
