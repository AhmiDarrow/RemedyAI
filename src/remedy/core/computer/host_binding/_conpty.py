"""conpty C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
import sys
from collections.abc import Mapping, Sequence
from ctypes import (
    c_size_t,
    c_uint8,
    c_uint32,
    c_uint64,
)

from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

from ._core import (
    STATUS_UNSUPPORTED,
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
