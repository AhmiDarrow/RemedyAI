"""Windows ConPTY spawn — thin async duck-type over Zig ``remedy_core`` (ABI 5).

Used by the terminal route when ConPTY is available. Spawn/IO failures raise
(no soft Python ConPTY twin and no unsigned-spawn fallback).
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from typing import Any

from remedy.core.computer import host_binding
from remedy.core.computer.host_binding import HostError
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError


def spawn_conpty_supported() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(host_binding.conpty_available())
    except (HostError, NativeRuntimeUnavailableError, OSError):
        return False


async def spawn_conpty(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Return a duck-typed process with async stdin/stdout (ConPTY attached)."""
    override = getattr(spawn_conpty, "_override", None)
    if override is not None:
        return await override(argv, cwd=cwd, env=env)
    if sys.platform != "win32":
        raise RuntimeError("ConPTY is Windows-only")
    return await asyncio.to_thread(_spawn_conpty_sync, argv, cwd, env)


def _resolve_argv0(argv: list[str]) -> list[str]:
    import shutil
    from pathlib import Path

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


def _spawn_conpty_sync(
    argv: list[str],
    cwd: str | None,
    env: dict[str, str] | None,
) -> _ConPTYProcess:
    try:
        resolved = _resolve_argv0(argv)
        token, now_ms = host_binding.issue_process_spawn_token(resolved)
        pid, handle = host_binding.conpty_spawn_authorized(
            resolved,
            cwd=cwd,
            env=env,
            token=token,
            now_ms=now_ms,
        )
    except HostError as exc:
        detail = str(exc)
        if exc.os_error:
            detail = f"{detail} winerr={exc.os_error}"
        raise OSError(detail) from exc
    except NativeRuntimeUnavailableError as exc:
        raise OSError(f"ConPTY unavailable: {exc}") from exc
    return _ConPTYProcess(pid=pid, handle=handle)


class _HandleStream:
    def __init__(self, session: int, *, write: bool) -> None:
        self._session = session
        self._write = write
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed or not self._write or not self._session:
            return
        with suppress(HostError, NativeRuntimeUnavailableError, OSError):
            host_binding.conpty_write(self._session, data)

    async def drain(self) -> None:
        return None

    async def read(self, n: int = 4096) -> bytes:
        if self._closed or self._write or not self._session:
            return b""
        return await asyncio.to_thread(self._read_sync, max(1, int(n)))

    def _read_sync(self, n: int) -> bytes:
        try:
            return host_binding.conpty_read(self._session, n)
        except (HostError, NativeRuntimeUnavailableError, OSError):
            return b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._session:
            return
        which = (
            host_binding.CONPTY_PIPE_STDIN
            if self._write
            else host_binding.CONPTY_PIPE_STDOUT
        )
        with suppress(HostError, NativeRuntimeUnavailableError, OSError):
            host_binding.conpty_close_pipe(self._session, which)


class _ConPTYProcess:
    """Duck-type asyncio.subprocess.Process for terminal / HostSession callers."""

    def __init__(self, *, pid: int, handle: int) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._handle = int(handle)
        self.stdin = _HandleStream(self._handle, write=True)
        self.stdout = _HandleStream(self._handle, write=False)
        self.stderr = None

    def poll(self) -> int | None:
        """``None`` while the child is running; otherwise the exit code."""
        if self.returncode is not None:
            return self.returncode
        if not self._handle:
            return self.returncode
        try:
            code = host_binding.conpty_poll(self._handle)
        except (HostError, NativeRuntimeUnavailableError, OSError):
            return None
        if code is None:
            return None
        self.returncode = int(code)
        return self.returncode

    def kill(self) -> None:
        self._terminate()

    def terminate(self) -> None:
        self._terminate()

    def _terminate(self) -> None:
        if self._handle:
            with suppress(HostError, NativeRuntimeUnavailableError, OSError):
                host_binding.conpty_kill(self._handle)
        self._close()
        self.returncode = 1

    def _close(self) -> None:
        with suppress(Exception):
            self.stdin.close()
        with suppress(Exception):
            self.stdout.close()
        handle = self._handle
        self._handle = 0
        self.stdin._session = 0
        self.stdout._session = 0
        if handle:
            with suppress(HostError, NativeRuntimeUnavailableError, OSError):
                host_binding.conpty_close(handle)
