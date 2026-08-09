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


def hidden_startupinfo() -> Any | None:
    """STARTUPINFO with SW_HIDE — belt-and-suspenders with CREATE_NO_WINDOW."""
    if sys.platform != "win32":
        return None
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE
        return si
    except Exception:
        return None


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Kwargs mergeable into subprocess.run / Popen / create_subprocess_exec.

    On Windows: CREATE_NO_WINDOW + SW_HIDE so tool/spread children do not flash
    a cmd/console window. Does not prevent a *third-party* tool that itself
    forces a new console (rare); Remedy-spawned shells and rg should stay quiet.
    """
    if sys.platform != "win32":
        return {}
    out: dict[str, Any] = {"creationflags": CREATE_NO_WINDOW}
    si = hidden_startupinfo()
    if si is not None:
        out["startupinfo"] = si
    return out


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


def kill_process_tree(proc: Any) -> None:
    """Kill *proc* and, on Windows, attempt to kill its child tree.

    Accepts ``asyncio.subprocess.Process`` or ``subprocess.Popen``.
    Safe to call if the process already exited.
    """
    if proc is None:
        return
    pid = getattr(proc, "pid", None)
    # Prefer tree kill on Windows so pwsh/cmd children die with the shell.
    if sys.platform == "win32" and pid:
        try:
            # /T = tree, /F = force; CREATE_NO_WINDOW so no flash
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=5,
                **hidden_subprocess_kwargs(),
            )
            return
        except Exception:
            pass
    try:
        if getattr(proc, "returncode", None) is not None:
            return  # already exited (asyncio Process)
    except Exception:
        pass
    try:
        if hasattr(proc, "poll") and proc.poll() is not None:
            return
    except Exception:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        return
    except Exception:
        from contextlib import suppress

        with suppress(Exception):
            proc.terminate()


def win_shell_prefix() -> list[str]:
    """Argv prefix for running a shell command string on Windows without a window.

    Prefer **cmd.exe** for agent bash_exec: local models emit bash/cmd-style
    commands (``mkdir -p``, ``&&``, ``cd path && …``). PowerShell treats
    ``mkdir -p a b`` as unknown parameters and fails partner builds
    (RemedyPDF 2026-08-08: ``mkdir: A positional parameter cannot be found…``).
    """
    import shutil

    if sys.platform != "win32":
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-c"]

    cmd = shutil.which("cmd") or "cmd.exe"
    return [cmd, "/c"]
