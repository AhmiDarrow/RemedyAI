"""conpty C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``. Owns the authorized
spawn duck-type (:class:`_ConPTYProcess`) and async ``spawn_conpty`` facade.
Production ``/api/terminal`` lives in Go ``remedy-runtime``.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from ctypes import (
    c_size_t,
    c_uint8,
    c_uint32,
    c_uint64,
)
from typing import Any

from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

from ._core import (
    STATUS_UNSUPPORTED,
    HostError,
    _BytePtr,
    _check,
    _lib,
    _utf8,
)

# --- ConPTY (ABI 5) ----------------------------------------------------------

CONPTY_PIPE_STDIN = 0
CONPTY_PIPE_STDOUT = 1


def conpty_available() -> bool:
    """True when ``CreatePseudoConsole`` is exported on this Windows host."""
    if sys.platform != "win32":
        return False
    try:
        library = _lib()
    except NativeRuntimeUnavailableError:
        return False
    flag = c_uint8()
    status = library.remedy_core_conpty_available(ctypes.byref(flag))
    if status == STATUS_UNSUPPORTED:
        return False
    _check(library, "conpty_available", status)
    return bool(flag.value)


def conpty_spawn(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    cols: int = 120,
    rows: int = 40,
) -> tuple[int, int]:
    """``(pid, handle)`` for a ConPTY-attached child. Close with :func:`conpty_close`."""
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    pid, handle = c_uint32(), c_uint64()
    _check(
        library,
        "conpty_spawn",
        library.remedy_core_conpty_spawn(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            int(cols) & 0xFFFF,
            int(rows) & 0xFFFF,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return int(pid.value), int(handle.value)


def conpty_write(handle: int, data: bytes | bytearray | memoryview) -> int:
    """Write bytes to the ConPTY stdin pipe; return the count written."""
    library = _lib()
    raw = bytes(data)
    written = c_size_t(0)
    _check(
        library,
        "conpty_write",
        library.remedy_core_conpty_write(handle, raw, len(raw), ctypes.byref(written)),
    )
    return int(written.value)


def conpty_read(handle: int, max_len: int = 4096) -> bytes:
    """Read up to *max_len* bytes from the ConPTY stdout pipe (empty at EOF)."""
    library = _lib()
    size = max(0, int(max_len))
    if size == 0:
        return b""
    buf = (c_uint8 * size)()
    got = c_size_t(0)
    _check(
        library,
        "conpty_read",
        library.remedy_core_conpty_read(
            handle, ctypes.cast(buf, _BytePtr), size, ctypes.byref(got)
        ),
    )
    n = int(got.value)
    if n <= 0:
        return b""
    return bytes(buf[:n])


def conpty_poll(handle: int) -> int | None:
    """Exit code once the child has left STILL_ACTIVE; otherwise ``None``."""
    library = _lib()
    exited, code = c_uint8(), c_uint32()
    _check(
        library,
        "conpty_poll",
        library.remedy_core_conpty_poll(handle, ctypes.byref(exited), ctypes.byref(code)),
    )
    return int(code.value) if exited.value else None


def conpty_kill(handle: int) -> None:
    library = _lib()
    _check(library, "conpty_kill", library.remedy_core_conpty_kill(handle))


def conpty_close_pipe(handle: int, which: int) -> None:
    """Close stdin (0) or stdout (1) pipe end. Idempotent."""
    library = _lib()
    _check(
        library,
        "conpty_close_pipe",
        library.remedy_core_conpty_close_pipe(handle, int(which) & 0xFFFFFFFF),
    )


def conpty_close(handle: int) -> None:
    """Release pipes, pseudoconsole and process handle; invalidates *handle*."""
    library = _lib()
    _check(library, "conpty_close", library.remedy_core_conpty_close(handle))


# --- Duck-type over Zig ConPTY (terminal / HostSession callers) -------------
# Call through the ``host_binding`` package so test fakes (install_fake_conpty)
# patch the same symbols production uses.


def _api() -> Any:
    from remedy.core.computer import host_binding as api

    return api


class _HandleStream:
    def __init__(self, session: int, *, write: bool) -> None:
        self._session = session
        self._write = write
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed or not self._write or not self._session:
            return
        api = _api()
        with suppress(HostError, NativeRuntimeUnavailableError, OSError):
            api.conpty_write(self._session, data)

    async def drain(self) -> None:
        return None

    async def read(self, n: int = 4096) -> bytes:
        if self._closed or self._write or not self._session:
            return b""
        return await asyncio.to_thread(self._read_sync, max(1, int(n)))

    def _read_sync(self, n: int) -> bytes:
        try:
            return _api().conpty_read(self._session, n)
        except (HostError, NativeRuntimeUnavailableError, OSError):
            return b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._session:
            return
        api = _api()
        which = api.CONPTY_PIPE_STDIN if self._write else api.CONPTY_PIPE_STDOUT
        with suppress(HostError, NativeRuntimeUnavailableError, OSError):
            api.conpty_close_pipe(self._session, which)


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
            code = _api().conpty_poll(self._handle)
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
                _api().conpty_kill(self._handle)
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
                _api().conpty_close(handle)


def spawn_conpty_process(
    argv: list[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> _ConPTYProcess:
    """Authorized ConPTY spawn → duck-typed process. No unsigned fallback."""
    from remedy.execution.process_argv import resolve_argv0

    api = _api()
    resolved = resolve_argv0(argv)
    token, now_ms = api.issue_process_spawn_token(resolved)
    try:
        pid, handle = api.conpty_spawn_authorized(
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


# Compat alias — tests patch/call the sync spawn by this name.
_spawn_conpty_sync = spawn_conpty_process


def spawn_conpty_supported() -> bool:
    """True when ``remedy_core`` reports ConPTY (ABI 5) on this host."""
    if sys.platform != "win32":
        return False
    try:
        # Via package so fakes / monkeypatches on ``host_binding`` apply.
        return bool(_api().conpty_available())
    except (HostError, NativeRuntimeUnavailableError, OSError):
        return False


async def spawn_conpty(
    argv: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Return a duck-typed process with async stdin/stdout (ConPTY attached)."""
    api = _api()
    override = getattr(api.spawn_conpty, "_override", None)
    if override is None:
        override = getattr(spawn_conpty, "_override", None)
    if override is not None:
        return await override(argv, cwd=cwd, env=env)
    if sys.platform != "win32":
        raise RuntimeError("ConPTY is Windows-only")
    sync = getattr(api, "_spawn_conpty_sync", None) or _spawn_conpty_sync
    return await asyncio.to_thread(sync, argv, cwd, env)
