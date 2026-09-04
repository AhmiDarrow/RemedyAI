"""Tests for Windows console-hide subprocess helpers and fail-closed host gate."""

from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from remedy.execution import process as P
from remedy.execution.process import (
    create_hidden_subprocess_exec,
    hidden_creationflags,
    hidden_subprocess_kwargs,
    run_hidden,
    win_shell_prefix,
)
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError


def test_hidden_creationflags_windows_only() -> None:
    flags = hidden_creationflags()
    kw = hidden_subprocess_kwargs()
    if sys.platform == "win32":
        assert flags == subprocess.CREATE_NO_WINDOW
        assert flags == 0x08000000
        assert kw.get("creationflags") == subprocess.CREATE_NO_WINDOW
        # STARTUPINFO SW_HIDE is set alongside the flag (extra anti-flash)
        assert kw["startupinfo"].wShowWindow == subprocess.SW_HIDE
    else:
        assert flags == 0
        assert kw == {}


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
        assert kw.get("creationflags") == subprocess.CREATE_NO_WINDOW
        # subprocess.Popen accepts the flag without error
        p = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **kw,
        )
        assert p.wait(timeout=15) == 0


def test_soft_helpers_fail_closed_without_process_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Piped soft helpers must not soft-fallback when remedy_core is missing."""
    if sys.platform not in ("win32", "linux"):
        pytest.skip("process host gate is win32/linux")

    def _boom() -> None:
        raise NativeRuntimeUnavailableError("remedy_core library not found: test double")

    monkeypatch.setattr(P, "require_process_host", _boom)
    with pytest.raises(NativeRuntimeUnavailableError):
        P.run_hidden(
            [sys.executable, "-c", "pass"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    with pytest.raises(NativeRuntimeUnavailableError):
        P.popen_hidden([sys.executable, "-c", "pass"])
