"""Subprocess helpers that never flash a console window on Windows.

Desktop users must never see a brief cmd/powershell window when the agent
runs tools. All Remedy-spawned child processes should go through this module.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any

# CREATE_NO_WINDOW — hide console windows for GUI / desktop tool runs.
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def hidden_creationflags() -> int:
    """Return Windows creation flags that suppress a console window."""
    if sys.platform == "win32":
        return CREATE_NO_WINDOW
    return 0


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Kwargs mergeable into subprocess.run / Popen / create_subprocess_exec."""
    flags = hidden_creationflags()
    if flags:
        return {"creationflags": flags}
    return {}


def run_hidden(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input: str | bytes | None = None,
    **extra: Any,
) -> subprocess.CompletedProcess[Any]:
    """subprocess.run with CREATE_NO_WINDOW on Windows."""
    kwargs: dict[str, Any] = {
        **hidden_subprocess_kwargs(),
        "capture_output": capture_output,
        "text": text,
        "timeout": timeout,
        "cwd": cwd,
        "env": env,
        "check": check,
        **extra,
    }
    if input is not None:
        kwargs["input"] = input
    return subprocess.run(list(args), **kwargs)


def popen_hidden(
    args: Sequence[str],
    *,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    **extra: Any,
) -> subprocess.Popen[Any]:
    """subprocess.Popen with CREATE_NO_WINDOW on Windows."""
    return subprocess.Popen(
        list(args),
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        **hidden_subprocess_kwargs(),
        **extra,
    )


async def create_hidden_subprocess_exec(
    program: str,
    *args: str,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    **extra: Any,
) -> asyncio.subprocess.Process:
    """asyncio.create_subprocess_exec that never shows a Windows console."""
    return await asyncio.create_subprocess_exec(
        program,
        *args,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        cwd=cwd,
        env=env,
        **hidden_subprocess_kwargs(),
        **extra,
    )


def win_shell_prefix() -> list[str]:
    """Argv prefix for running a shell command string on Windows without a window.

    Prefers PowerShell with -WindowStyle Hidden; falls back to cmd.exe
    (still under CREATE_NO_WINDOW when spawned via this module).
    """
    import shutil

    if sys.platform != "win32":
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-c"]

    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    if pwsh:
        return [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-WindowStyle",
            "Hidden",
            "-Command",
        ]
    cmd = shutil.which("cmd") or "cmd.exe"
    return [cmd, "/c"]
