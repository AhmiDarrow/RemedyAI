"""Tests for Zig process binding and fail-closed soft pipe gates."""

from __future__ import annotations

import sys

import pytest

from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError
from remedy.execution import process as P
from remedy.execution.process import run_hidden, win_shell_prefix
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError


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
    try:
        from remedy.core.computer import host_binding as H

        H._lib()
    except Exception:
        pytest.skip("remedy_core not available for exec-capture")
    result = run_hidden(
        [sys.executable, "-c", "print('hidden-ok')"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0
    assert "hidden-ok" in (result.stdout or "")


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


@pytest.mark.asyncio
async def test_create_hidden_subprocess_exec_fail_closed_on_host_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interactive pipe spawn is unsupported on win32/linux (no Zig pipe-spawn)."""
    if sys.platform not in ("win32", "linux"):
        pytest.skip("soft pipe refuse is win32/linux")
    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        await P.create_hidden_subprocess_exec(
            sys.executable,
            "-c",
            "print('async-hidden')",
            stdout=__import__("asyncio").subprocess.PIPE,
            stderr=__import__("asyncio").subprocess.PIPE,
        )
    assert raised.value.status == STATUS_UNSUPPORTED
    assert raised.value.function == "create_hidden_subprocess_exec"


def test_popen_hidden_fail_closed_on_host_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if sys.platform not in ("win32", "linux"):
        pytest.skip("soft pipe refuse is win32/linux")
    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        P.popen_hidden([sys.executable, "-c", "pass"])
    assert raised.value.status == STATUS_UNSUPPORTED
    assert raised.value.function == "popen_hidden"
