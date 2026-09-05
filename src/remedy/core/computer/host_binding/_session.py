"""session C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from collections.abc import Mapping
from ctypes import (
    c_size_t,
    c_uint8,
    c_uint32,
    c_uint64,
)
from typing import Any

from ._core import (
    STATUS_OPERATION_FAILED,
    HostError,
    _BytePtr,
    _check,
    _lib,
    _take,
    _utf8,
)
from ._json import take_json
from ._policy import (
    DEFAULT_SPAWN_SCOPE,
    DEFAULT_SPAWN_SUBJECT,
    issue_process_spawn_token,
)

# ---- ABI 5 additive: HostSession -------------------------------------------


def host_session_wrap(*, host: str, command: str, sentinel: str) -> str:
    """Zig sentinel wrap — portable protocol helper."""
    payload = _utf8(
        json.dumps(
            {"host": host, "command": command, "sentinel": sentinel},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_wrap",
        library.remedy_core_host_session_wrap(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length)
    data = json.loads(raw.decode("utf-8"))
    return str(data.get("wrapped") or "")


def host_session_split(
    *,
    text: str,
    sentinel: str,
    command: str = "",
    conpty: bool = False,
) -> tuple[int, str]:
    """Zig sentinel split — portable protocol helper."""
    payload = _utf8(
        json.dumps(
            {
                "text": text,
                "sentinel": sentinel,
                "command": command,
                "conpty": bool(conpty),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_split",
        library.remedy_core_host_session_split(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length)
    data = json.loads(raw.decode("utf-8"))
    return int(data.get("exit_code", -1)), str(data.get("body") or "")


def host_session_argv(host: str | None = None) -> list[str]:
    """Argv Zig will authorize for :func:`host_session_open_authorized`."""
    host_name = str(host or ("cmd" if sys.platform == "win32" else "posix"))
    host_raw = _utf8(host_name)
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_argv",
        library.remedy_core_host_session_argv(
            host_raw, len(host_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    data = take_json(library, ptr, length)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise HostError("host_session_argv", STATUS_OPERATION_FAILED)
    return [str(x) for x in data]


def issue_host_session_token(
    host: str | None = None,
    *,
    owner_checkpoint: bool = False,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
) -> tuple[bytes, int]:
    """Return ``(token, now_ms)`` for an authorized HostSession open of *host*."""
    return issue_process_spawn_token(
        host_session_argv(host),
        owner_checkpoint=owner_checkpoint,
        subject=subject,
        scope=scope,
    )


def host_session_open(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    use_conpty: bool = False,
) -> int:
    """Open a Zig HostSession (Windows). Returns an opaque handle."""
    body: dict[str, Any] = {"use_conpty": bool(use_conpty)}
    if host:
        body["host"] = host
    if cwd:
        body["cwd"] = cwd
    if env is not None:
        body["env"] = dict(env)
    payload = _utf8(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    handle = c_uint64()
    _check(
        library,
        "host_session_open",
        library.remedy_core_host_session_open(
            payload, len(payload), ctypes.byref(handle)
        ),
    )
    return int(handle.value)


def host_session_open_authorized(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    use_conpty: bool = False,
    token: bytes | bytearray | memoryview,
    subject: str = "agent:remedy",
    scope: str = "workspace:local",
    owner_confirmed: bool = False,
    now_ms: int | None = None,
) -> int:
    """Authorize then open a Zig HostSession (Windows)."""
    body: dict[str, Any] = {"use_conpty": bool(use_conpty)}
    if host:
        body["host"] = host
    if cwd:
        body["cwd"] = cwd
    if env is not None:
        body["env"] = dict(env)
    payload = _utf8(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    handle = c_uint64()
    token_raw = bytes(token)
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "host_session_open_authorized",
        library.remedy_core_host_session_open_authorized(
            payload,
            len(payload),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(handle),
        ),
    )
    return int(handle.value)


def host_session_run(
    handle: int, command: str, *, timeout_ms: int = 60_000
) -> dict[str, Any]:
    """Run one command in a Zig HostSession; returns a SessionResult dict."""
    library = _lib()
    cmd = _utf8(command)
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_run",
        library.remedy_core_host_session_run(
            c_uint64(handle),
            cmd,
            len(cmd),
            c_uint32(max(1, int(timeout_ms))),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    raw = _take(library, ptr, length)
    return dict(json.loads(raw.decode("utf-8")))


def host_session_cwd(handle: int) -> str:
    """Probe cwd for a Zig HostSession."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_cwd",
        library.remedy_core_host_session_cwd(
            c_uint64(handle), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def host_session_close(handle: int) -> None:
    """Close a Zig HostSession."""
    if not handle:
        return
    library = _lib()
    _check(
        library,
        "host_session_close",
        library.remedy_core_host_session_close(c_uint64(handle)),
    )
