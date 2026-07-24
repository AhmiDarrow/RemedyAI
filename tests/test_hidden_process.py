"""Tests for Windows console-hide subprocess helpers."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from remedy.execution.process import (
    CREATE_NO_WINDOW,
    create_hidden_subprocess_exec,
    hidden_creationflags,
    hidden_subprocess_kwargs,
    run_hidden,
    win_shell_prefix,
)


def test_hidden_creationflags_windows_only() -> None:
    flags = hidden_creationflags()
    if sys.platform == "win32":
        assert flags == CREATE_NO_WINDOW
        assert flags == 0x08000000
        assert hidden_subprocess_kwargs() == {"creationflags": CREATE_NO_WINDOW}
    else:
        assert flags == 0
        assert hidden_subprocess_kwargs() == {}


def test_win_shell_prefix_has_hidden_style_on_windows() -> None:
    prefix = win_shell_prefix()
    assert len(prefix) >= 2
    if sys.platform == "win32":
        joined = " ".join(prefix).lower()
        # PowerShell path includes -WindowStyle Hidden; cmd uses /c under CREATE_NO_WINDOW.
        assert "windowstyle" in joined or prefix[-1].lower() in ("/c", "-command")
    else:
        assert prefix[-1] == "-c"


def test_run_hidden_python_echo() -> None:
    result = run_hidden(
        [sys.executable, "-c", "print('hidden-ok')"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "hidden-ok" in (result.stdout or "")


@pytest.mark.asyncio
async def test_create_hidden_subprocess_exec() -> None:
    proc = await create_hidden_subprocess_exec(
        sys.executable,
        "-c",
        "print('async-hidden')",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
    assert proc.returncode == 0
    assert b"async-hidden" in (out or b"")


def test_run_hidden_accepts_creationflags_merge() -> None:
    """Ensure kwargs still work and CREATE_NO_WINDOW is applied when set."""
    kw = hidden_subprocess_kwargs()
    if sys.platform == "win32":
        # Mimic what create_subprocess receives
        assert kw.get("creationflags") == CREATE_NO_WINDOW
        # subprocess.Popen accepts the flag without error
        p = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kw,
        )
        assert p.wait(timeout=15) == 0
