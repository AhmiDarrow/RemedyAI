"""Child processes via ``remedy_core`` authorized spawn — no soft pipe fallback.

Zig-only production paths:

* :func:`spawn_hidden` / :func:`spawn_piped` — authorized spawn
* :func:`kill_tree` / :func:`kill_process_tree` — toolhelp / process-group
* exec-capture via :func:`run_hidden` (:mod:`process_run`)

Handle types: :mod:`process_child`. Argv helpers: :mod:`process_argv`.
``popen_hidden`` pipes → :func:`spawn_piped`; else fail closed.
:func:`create_hidden_subprocess_exec` stays fail-closed.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn

from remedy.execution.process_argv import resolve_argv0, win_shell_prefix
from remedy.execution.process_child import (
    DEFAULT_RUN_TIMEOUT_S,
    HiddenProcess,
    PipedProcess,
    retain_detached,
    wait,
    wait_piped,
    wrap_os_pipe_handle,
)
from remedy.execution.process_run import (
    create_hidden_subprocess_exec,
    popen_hidden,
    run_hidden,
    run_hidden_async,
)

__all__ = [
    "DEFAULT_RUN_TIMEOUT_S",
    "HiddenProcess",
    "PipedProcess",
    "create_hidden_subprocess_exec",
    "kill_process_tree",
    "kill_tree",
    "popen_hidden",
    "require_process_host",
    "resolve_argv0",
    "retain_detached",
    "run_hidden",
    "run_hidden_async",
    "spawn_hidden",
    "spawn_piped",
    "wait",
    "wait_piped",
    "win_shell_prefix",
]


def _process_host_required() -> bool:
    return sys.platform in ("win32", "linux")


def require_process_host() -> None:
    """Fail closed when the Zig process host cannot be loaded on win32/linux."""
    if not _process_host_required():
        return
    from remedy.core.computer import host_binding

    host_binding._lib()


def _refuse_soft_pipe_spawn(helper: str) -> NoReturn:
    """No soft unsigned pipe-spawn; use :func:`spawn_piped` / authorized ABI."""
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

    raise HostError(helper, STATUS_UNSUPPORTED)


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


def spawn_piped(
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    write_roots: Sequence[str | Path] | None = None,
    text: bool = True,
) -> PipedProcess:
    """Start *argv* with interactive stdin/stdout/stderr via authorized spawn."""
    from remedy.core.computer import host_binding

    require_process_host()
    resolved = resolve_argv0(argv)
    token, now_ms = host_binding.issue_process_spawn_token(resolved)
    roots = None if write_roots is None else [str(r) for r in write_roots]
    spawned = host_binding.process_spawn_piped_authorized(
        resolved,
        str(cwd) if cwd else None,
        env,
        token=token,
        now_ms=now_ms,
        write_roots=roots,
    )
    stdin = wrap_os_pipe_handle(spawned.stdin_write, writable=True, text=text)
    stdout = wrap_os_pipe_handle(spawned.stdout_read, writable=False, text=text)
    stderr = wrap_os_pipe_handle(spawned.stderr_read, writable=False, text=text)
    return PipedProcess(spawned.pid, spawned.handle, stdin, stdout, stderr)


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
    if isinstance(proc, PipedProcess):
        with suppress(ProcessLookupError):
            proc.kill()
        return
    pid = getattr(proc, "pid", None)
    if _process_host_required():
        if not pid:
            return
        from remedy.core.computer.host_binding import HostError
        from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

        try:
            kill_tree(int(pid))
        except (HostError, NativeRuntimeUnavailableError):
            raise
        except ProcessLookupError:
            return
        return
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
