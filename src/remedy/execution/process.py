"""Child processes that never flash a console window and never outlive us.

Desktop users must never see a brief cmd/powershell window when the agent
runs tools, and nothing Remedy starts may be left running after she stops
it. Every Remedy-spawned child goes through this module:

* :func:`run_hidden` / :func:`popen_hidden` /
  :func:`create_hidden_subprocess_exec` wrap :mod:`subprocess` and
  :mod:`asyncio` for callers that need pipes, and add the hidden creation
  flags on Windows.
* :func:`spawn_hidden` starts a process through ``remedy_core`` authorized
  spawn (policy + capability token) inside a Windows job object, so the
  whole tree (``uv.exe`` and the python it launches, ``cmd`` and its
  children) dies when the handle closes.
* :func:`kill_tree` / :func:`kill_process_tree` terminate a process and every
  descendant through ``remedy_core`` (toolhelp walk, deepest first) instead
  of a shell helper.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any


def hidden_creationflags() -> int:
    """Windows creation flags that suppress a console window (0 elsewhere).

    Uses ``getattr`` so a win32-platform mock on a POSIX interpreter (CI under
    WSL, unit tests) cannot AttributeError on ``CREATE_NO_WINDOW``.
    """
    if sys.platform != "win32":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def hidden_startupinfo() -> Any | None:
    """STARTUPINFO with SW_HIDE, belt and braces alongside CREATE_NO_WINDOW."""
    if sys.platform != "win32":
        return None
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is None:
        return None
    startup = startupinfo_cls()
    startup.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startup.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return startup


def hidden_subprocess_kwargs() -> dict[str, Any]:
    """Kwargs mergeable into subprocess.run / Popen / create_subprocess_exec.

    On Windows: CREATE_NO_WINDOW + SW_HIDE so *this* process has no console.
    The packaged desktop sidecar is a GUI process (no console). A console
    child (git, python, uv, cmd) without this flag opens a visible CMD.

    CREATE_NO_WINDOW is not inherited: ``cmd /c uv run pytest`` hides cmd but
    uv's python child still pops a window. Prefer exec'ing python/git
    directly (see ``deflate_uv_run``), or :func:`spawn_hidden` when no pipes
    are needed.
    """
    if sys.platform != "win32":
        return {}
    out: dict[str, Any] = {"creationflags": hidden_creationflags()}
    startup = hidden_startupinfo()
    if startup is not None:
        out["startupinfo"] = startup
    return out


def _merge_hidden(extra: dict[str, Any]) -> dict[str, Any]:
    """Hidden kwargs plus *extra*; caller creation flags are OR-ed in."""
    kwargs = hidden_subprocess_kwargs()
    flags = extra.pop("creationflags", 0)
    if flags:
        kwargs["creationflags"] = int(kwargs.get("creationflags", 0)) | int(flags)
    kwargs.update(extra)
    return kwargs


#: Default wall for a hidden child process. Every caller today passes its own,
#: so this changes nothing now; it is here so the next one that forgets does
#: not get an unbounded wait. Pass ``timeout=None`` for a deliberately
#: unbounded run (an interactive dev server, a long build).
DEFAULT_RUN_TIMEOUT_S = 120.0


def run_hidden(
    args: Sequence[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = DEFAULT_RUN_TIMEOUT_S,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    input: str | bytes | None = None,
    **extra: Any,
) -> subprocess.CompletedProcess[Any]:
    """subprocess.run with CREATE_NO_WINDOW on Windows."""
    kwargs: dict[str, Any] = {
        **_merge_hidden(extra),
        "capture_output": capture_output,
        "text": text,
        "timeout": timeout,
        "cwd": cwd,
        "env": env,
        "check": check,
    }
    if input is not None:
        kwargs["input"] = input
    return subprocess.run(list(args), **kwargs)


def popen_hidden(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    **extra: Any,
) -> subprocess.Popen[Any]:
    """subprocess.Popen with CREATE_NO_WINDOW on Windows.

    ``creationflags`` in *extra* are added to the hidden flags, so a caller
    can still ask for CREATE_NEW_PROCESS_GROUP.
    """
    return subprocess.Popen(
        list(args),
        cwd=cwd,
        env=env,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        **_merge_hidden(extra),
    )


async def create_hidden_subprocess_exec(
    program: str,
    *args: str,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    cwd: str | Path | None = None,
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
        **_merge_hidden(extra),
    )


# --- remedy_core process control -------------------------------------------


class HiddenProcess:
    """A process started by :func:`spawn_hidden`.

    The process runs inside a job object with ``KILL_ON_JOB_CLOSE``: calling
    :meth:`close` (or leaving a ``with`` block) ends the process and every
    descendant that is still running. Use :meth:`wait` first when the process
    is meant to finish on its own.
    """

    def __init__(self, pid: int, handle: int) -> None:
        self.pid = pid
        self._handle = handle
        self.returncode: int | None = None

    def __repr__(self) -> str:
        state = "open" if self._handle else "closed"
        return f"HiddenProcess(pid={self.pid}, returncode={self.returncode}, {state})"

    def __enter__(self) -> HiddenProcess:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def wait(self, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
        """Exit code once the process ends, None if *timeout* seconds elapse."""
        return wait(self, timeout)

    def poll(self) -> int | None:
        return wait(self, 0)

    def kill_tree(self) -> None:
        """Terminate the process and all descendants now."""
        kill_tree(self.pid)

    def close(self) -> None:
        """Release the handles; a still-running tree is terminated."""
        from remedy.core.computer import host_binding

        handle, self._handle = self._handle, 0
        if handle:
            host_binding.process_close(handle)

    @property
    def handle(self) -> int:
        return self._handle


def _resolve_argv0(argv: Sequence[str]) -> list[str]:
    """Resolve argv[0] to an absolute path (required by authorized spawn)."""
    import shutil

    args = [str(a) for a in argv]
    if not args:
        raise ValueError("argv must not be empty")
    exe = args[0]
    path = Path(exe)
    if path.is_absolute():
        return args
    found = shutil.which(exe)
    if found is None:
        raise FileNotFoundError(exe)
    args[0] = str(Path(found).resolve())
    return args


def spawn_hidden(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> HiddenProcess:
    """Start *argv* hidden, inside a job that dies with its handle.

    No pipes are attached; use :func:`popen_hidden` when output is needed.
    Goes through ``remedy_core`` authorized spawn (policy + capability token).
    There is no soft fallback to the unsigned spawn export.
    Raises :class:`remedy.core.computer.host_binding.HostError` (unsupported)
    on platforms where ``remedy_core`` has no process host yet.
    """
    from remedy.core.computer import host_binding

    resolved = _resolve_argv0(argv)
    token, now_ms = host_binding.issue_process_spawn_token(resolved)
    pid, handle = host_binding.process_spawn_authorized(
        resolved,
        str(cwd) if cwd else None,
        env,
        token=token,
        now_ms=now_ms,
    )
    return HiddenProcess(pid, handle)


def wait(process: HiddenProcess, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
    """Wait for a :class:`HiddenProcess`; None when *timeout* elapses first.

    A closed handle always raises ``ValueError`` — read ``returncode`` on the
    object after a prior successful wait instead of waiting again.
    """
    from remedy.core.computer import host_binding

    if not process.handle:
        raise ValueError("HiddenProcess is closed")
    if process.returncode is not None:
        return process.returncode
    timeout_ms = host_binding.WAIT_FOREVER if timeout is None else int(max(0.0, timeout) * 1000)
    code = host_binding.process_wait(process.handle, timeout_ms)
    if code is not None:
        process.returncode = code
    return code


def kill_tree(pid: int) -> None:
    """Terminate *pid* and every descendant (deepest first) through ``remedy_core``."""
    from remedy.core.computer import host_binding

    host_binding.process_kill_tree(int(pid))


def kill_process_tree(proc: Any) -> None:
    """Kill *proc* and, on Windows, its whole child tree.

    Accepts ``asyncio.subprocess.Process``, ``subprocess.Popen`` or
    :class:`HiddenProcess`. Safe to call if the process already exited.

    On Windows the kill-tree walks through ``remedy_core``. A missing or
    mismatched core is a hard error — orphans must not be left behind by a
    silent fallthrough to ``proc.kill()``.
    """
    if proc is None:
        return
    if isinstance(proc, HiddenProcess):
        try:
            proc.kill_tree()
        except ProcessLookupError:
            return
        return
    pid = getattr(proc, "pid", None)
    if sys.platform == "win32" and pid:
        # The job/toolhelp walk reaches grandchildren (pwsh under cmd, python
        # under uv) that a plain proc.kill() would orphan.
        from remedy.core.computer.host_binding import HostError
        from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

        try:
            kill_tree(int(pid))
            return
        except (HostError, NativeRuntimeUnavailableError):
            raise
        except ProcessLookupError:
            return
        except OSError:
            # Process already gone; fall through only to confirm.
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
        with suppress(Exception):
            proc.terminate()


def win_shell_prefix() -> list[str]:
    """Argv prefix for running a shell command string on Windows without a window.

    Prefer **cmd.exe** for agent bash_exec: local models emit bash/cmd-style
    commands (``mkdir -p``, ``&&``, ``cd path && …``). PowerShell treats
    ``mkdir -p a b`` as unknown parameters and fails partner builds
    (RemedyPDF 2026-08-08: ``mkdir: A positional parameter cannot be found…``).

    PowerShell payloads never go through this prefix — Host Bridge writes a
    temp ``.ps1`` and runs ``pwsh -File``. See ``execution.host``.
    """
    import shutil

    if sys.platform != "win32":
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-c"]

    cmd = shutil.which("cmd") or "cmd.exe"
    return [cmd, "/c"]
