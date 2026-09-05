"""Child processes via ``remedy_core`` authorized spawn — no soft pipe fallback.

Production paths:

* :func:`spawn_hidden` — policy + capability token + write-jail inside a job /
  process group (KILL_ON_JOB_CLOSE).
* :func:`run_hidden` — no-pipe → :func:`spawn_hidden`; capture → Zig
  exec-capture. Interactive pipes raise ``HostError`` (unsupported).
* :func:`kill_tree` / :func:`kill_process_tree` — Zig toolhelp / process-group
  walk, deepest first (win32 + linux).

``popen_hidden`` / :func:`create_hidden_subprocess_exec` exist so tests can
patch them; on every platform they fail closed (no CREATE_NO_WINDOW happy
path). Interactive pipes raise ``HostError`` — Zig has no general 3-pipe
authorized spawn for Python workers (HostSession ``spawnPiped`` is
Windows-only and merges stderr).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn


def _process_host_required() -> bool:
    return sys.platform in ("win32", "linux")


def require_process_host() -> None:
    """Fail closed when the Zig process host cannot be loaded on win32/linux."""
    if not _process_host_required():
        return
    from remedy.core.computer import host_binding

    host_binding._lib()


def _refuse_soft_pipe_spawn(helper: str) -> NoReturn:
    """Zig has no interactive pipe-spawn; never soft-fallback anywhere."""
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

    raise HostError(helper, STATUS_UNSUPPORTED)


def _stdio_is_pipe_request(
    *,
    capture_output: bool = False,
    input: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    stdin: Any = None,
    extra: Mapping[str, Any] | None = None,
) -> bool:
    if capture_output or input is not None:
        return True
    pipe_markers = (subprocess.PIPE, asyncio.subprocess.PIPE)
    for value in (stdout, stderr, stdin):
        if value in pipe_markers:
            return True
    extra = extra or {}
    return any(extra.get(key) in pipe_markers for key in ("stdout", "stderr", "stdin"))


def _can_exec_capture(
    *,
    capture_output: bool,
    input: Any,
    extra: Mapping[str, Any],
) -> bool:
    if not capture_output or input is not None:
        return False
    pipe_markers = (subprocess.PIPE, asyncio.subprocess.PIPE)
    return not any(extra.get(key) in pipe_markers for key in ("stdout", "stderr", "stdin"))


DEFAULT_RUN_TIMEOUT_S = 120.0

# Retain Zig job/process-group handles so KILL_ON_JOB_CLOSE does not reap
# fire-and-forget launches the moment spawn returns.
_DETACHED_CHILDREN: list[Any] = []


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
    """Run *args* via Zig authorized spawn or exec-capture (no soft pipes)."""
    require_process_host()
    if not _stdio_is_pipe_request(
        capture_output=capture_output, input=input, extra=extra
    ):
        child = spawn_hidden(args, cwd=cwd, env=env)
        try:
            code = wait(child, timeout)
            if code is None:
                child.kill_tree()
                child.close()
                raise subprocess.TimeoutExpired(cmd=list(args), timeout=timeout)
            empty = "" if text else b""
            result = subprocess.CompletedProcess(
                list(args), int(code), empty, empty
            )
            if check and result.returncode:
                raise subprocess.CalledProcessError(
                    result.returncode, list(args), result.stdout, result.stderr
                )
            return result
        finally:
            with suppress(Exception):
                child.close()
    if _can_exec_capture(capture_output=capture_output, input=input, extra=extra):
        return _run_hidden_exec_capture(
            args,
            text=text,
            timeout=timeout,
            cwd=cwd,
            env=env,
            check=check,
        )
    _refuse_soft_pipe_spawn("run_hidden")


def _run_hidden_exec_capture(
    args: Sequence[str],
    *,
    text: bool,
    timeout: float | None,
    cwd: str | Path | None,
    env: Mapping[str, str] | None,
    check: bool,
) -> subprocess.CompletedProcess[Any]:
    from remedy.core.computer import host_binding

    resolved = resolve_argv0(args)
    token, now_ms = host_binding.issue_process_spawn_token(resolved)
    timeout_ms = 0 if timeout is None else int(max(0.0, float(timeout)) * 1000)
    captured = host_binding.process_exec_capture_authorized(
        resolved,
        str(cwd) if cwd else None,
        env,
        token=token,
        now_ms=now_ms,
        timeout_ms=timeout_ms,
    )
    if captured.timed_out:
        raise subprocess.TimeoutExpired(
            cmd=list(args),
            timeout=timeout,
            output=captured.stdout if not text else captured.stdout.decode("utf-8", "replace"),
            stderr=captured.stderr if not text else captured.stderr.decode("utf-8", "replace"),
        )
    stdout: Any = captured.stdout
    stderr: Any = captured.stderr
    if text:
        stdout = captured.stdout.decode("utf-8", "replace")
        stderr = captured.stderr.decode("utf-8", "replace")
    result = subprocess.CompletedProcess(list(args), int(captured.exit_code), stdout, stderr)
    if check and result.returncode:
        raise subprocess.CalledProcessError(
            result.returncode, list(args), result.stdout, result.stderr
        )
    return result


async def run_hidden_async(
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
    """Async wrapper around :func:`run_hidden`."""
    return await asyncio.to_thread(
        run_hidden,
        args,
        capture_output=capture_output,
        text=text,
        timeout=timeout,
        cwd=cwd,
        env=env,
        check=check,
        input=input,
        **extra,
    )


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
    """Fail closed — no Zig pipe-spawn. Prefer :func:`spawn_hidden` / :func:`run_hidden`."""
    _ = (args, cwd, env, stdout, stderr, stdin, extra)
    require_process_host()
    _refuse_soft_pipe_spawn("popen_hidden")


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
    """Fail closed — no Zig pipe-spawn. Prefer :func:`run_hidden_async`."""
    _ = (program, args, stdout, stderr, stdin, cwd, env, extra)
    require_process_host()
    _refuse_soft_pipe_spawn("create_hidden_subprocess_exec")


class HiddenProcess:
    """Process from :func:`spawn_hidden`; :meth:`close` ends the job tree."""

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
        return wait(self, timeout)

    def poll(self) -> int | None:
        return wait(self, 0)

    def kill_tree(self) -> None:
        kill_tree(self.pid)

    def terminate(self) -> None:
        """Job/group kill — Zig has no graceful SIGTERM on the spawn handle."""
        self.kill_tree()

    def kill(self) -> None:
        self.kill_tree()

    def close(self) -> None:
        from remedy.core.computer import host_binding

        handle, self._handle = self._handle, 0
        if handle:
            host_binding.process_close(handle)

    @property
    def handle(self) -> int:
        return self._handle


def retain_detached(child: HiddenProcess) -> HiddenProcess:
    """Keep *child*'s job handle alive so KILL_ON_JOB_CLOSE does not reap it."""
    _DETACHED_CHILDREN.append(child)
    return child


def resolve_argv0(argv: Sequence[str]) -> list[str]:
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
    write_roots: Sequence[str | Path] | None = None,
) -> HiddenProcess:
    """Start *argv* hidden via authorized spawn (no unsigned soft fallback)."""
    from remedy.core.computer import host_binding

    resolved = resolve_argv0(argv)
    token, now_ms = host_binding.issue_process_spawn_token(resolved)
    roots = None if write_roots is None else [str(r) for r in write_roots]
    pid, handle = host_binding.process_spawn_authorized(
        resolved,
        str(cwd) if cwd else None,
        env,
        token=token,
        now_ms=now_ms,
        write_roots=roots,
    )
    return HiddenProcess(pid, handle)


def wait(process: HiddenProcess, timeout: float | None = DEFAULT_RUN_TIMEOUT_S) -> int | None:
    """Wait for a :class:`HiddenProcess`; None when *timeout* elapses first."""
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
    """Terminate *pid* and every descendant through ``remedy_core``."""
    from remedy.core.computer import host_binding

    host_binding.process_kill_tree(int(pid))


def kill_process_tree(proc: Any) -> None:
    """Kill *proc* (and its child tree) through ``remedy_core`` on host platforms."""
    if proc is None:
        return
    if isinstance(proc, HiddenProcess):
        with suppress(ProcessLookupError):
            proc.kill_tree()
        return
    pid = getattr(proc, "pid", None)
    if _process_host_required() and pid:
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
            pass
    try:
        if getattr(proc, "returncode", None) is not None:
            return
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
    """Argv prefix for a shell command string (cmd /c on Windows, sh -c elsewhere)."""
    import shutil

    if sys.platform != "win32":
        sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [sh, "-c"]
    cmd = shutil.which("cmd") or "cmd.exe"
    return [cmd, "/c"]
