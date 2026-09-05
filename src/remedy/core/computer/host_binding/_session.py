"""session C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``. Owns asyncio
``HostSession`` orchestration over the Zig ABI (open/run/cwd/close) and the
shared-session registry. Call Zig symbols via the ``host_binding`` package so
test fakes / monkeypatches apply.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import sys
import time
from collections.abc import Mapping
from contextlib import suppress
from ctypes import (
    c_size_t,
    c_uint8,
    c_uint32,
    c_uint64,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._core import (
    STATUS_OPERATION_FAILED,
    STATUS_UNSUPPORTED,
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


def _api() -> Any:
    from remedy.core.computer import host_binding as api

    return api

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


# ---- Asyncio HostSession orchestration (Zig ABI 5) --------------------------


@dataclass
class SessionResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    cwd: str = ""
    timed_out: bool = False
    interactive: bool = False
    host: str = "cmd"
    used_conpty: bool = False


@dataclass
class HostSession:
    """One long-lived cmd/pwsh process. Not the default for bash_exec.

    Windows: Zig ``remedy_core`` HostSession owns spawn + sentinel I/O.
    Non-Windows: fail closed (no soft pipe twin).
    """

    host: str = "cmd"
    cwd: str | None = None
    env: dict[str, str] | None = None
    use_conpty: bool = False
    _zig_handle: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    started: bool = field(default=False, init=False)
    _used_conpty: bool = field(default=False, init=False, repr=False)

    async def start(self) -> None:
        if self.started and self._alive():
            return
        self._lock = asyncio.Lock()
        if os.name != "nt":
            raise HostError("host_session_open", STATUS_UNSUPPORTED)
        api = _api()
        if self.env is not None:
            env = dict(self.env)
        else:
            from remedy.execution.env import scrub_subprocess_env

            env = scrub_subprocess_env()
        token, now_ms = api.issue_host_session_token(self.host)
        handle = await asyncio.to_thread(
            api.host_session_open_authorized,
            host=self.host,
            cwd=self.cwd,
            env=env,
            use_conpty=bool(self.use_conpty),
            token=token,
            now_ms=now_ms,
        )
        self._zig_handle = int(handle)
        self.started = True
        self._used_conpty = bool(self.use_conpty)

    async def run(self, command: str, *, timeout: float = 60.0) -> SessionResult:
        if not command or not str(command).strip():
            return SessionResult(
                exit_code=-1, stdout="", stderr="empty command", host=self.host
            )
        await self.start()
        assert self._lock is not None
        if not self._zig_handle:
            raise HostError("host_session_run", STATUS_UNSUPPORTED)
        api = _api()
        async with self._lock:
            data = await asyncio.to_thread(
                api.host_session_run,
                self._zig_handle,
                command.strip(),
                timeout_ms=int(max(1.0, float(timeout)) * 1000),
            )
        timed_out = bool(data.get("timed_out"))
        interactive = bool(data.get("interactive"))
        if timed_out or interactive:
            self._abandon_proc()
        return SessionResult(
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            cwd=str(data.get("cwd") or ""),
            timed_out=timed_out,
            interactive=interactive,
            host=str(data.get("host") or self.host),
            used_conpty=bool(data.get("used_conpty", self._used_conpty)),
        )

    async def current_cwd(self) -> str:
        if not self._alive() or not self._zig_handle:
            return ""
        api = _api()
        assert self._lock is not None
        async with self._lock:
            here = await asyncio.to_thread(api.host_session_cwd, self._zig_handle)
        if not here:
            self._abandon_proc()
        return here or ""

    async def close(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            await asyncio.to_thread(_api().host_session_close, handle)

    def _abandon_proc(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            with suppress(Exception):
                _api().host_session_close(handle)

    def _alive(self) -> bool:
        return bool(self._zig_handle) and self.started


_SESSIONS_GUARD = asyncio.Lock()
_SESSIONS: dict[str, HostSession] = {}


def _session_key(session_id: str | None) -> str:
    sid = (session_id or "").strip()
    return sid if sid else "_GLOBAL"


def _norm_cwd_key(cwd: str | None) -> str:
    if not cwd:
        return ""
    try:
        return (
            str(Path(cwd).expanduser().resolve(strict=False))
            .replace("\\", "/")
            .rstrip("/")
            .lower()
        )
    except OSError:
        return cwd.replace("\\", "/").rstrip("/").lower()


async def get_shared_session(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    use_conpty: bool = False,
    session_id: str | None = None,
) -> HostSession:
    want = host or ("cmd" if os.name == "nt" else "posix")
    key = _session_key(session_id)
    to_close: HostSession | None = None
    async with _SESSIONS_GUARD:
        existing = _SESSIONS.get(key)
        if existing is not None and existing.host == want and existing._alive():
            if cwd and existing.cwd and _norm_cwd_key(cwd) != _norm_cwd_key(existing.cwd):
                to_close = _SESSIONS.pop(key, None)
            else:
                return existing
        elif existing is not None:
            to_close = _SESSIONS.pop(key, None)
    if to_close is not None:
        await to_close.close()
    sess = HostSession(host=want, cwd=cwd, env=env, use_conpty=use_conpty)
    await sess.start()
    extra_close: HostSession | None = None
    async with _SESSIONS_GUARD:
        other = _SESSIONS.get(key)
        if other is not None and other.host == want and other._alive():
            extra_close = sess
            sess = other
        else:
            _SESSIONS[key] = sess
    if extra_close is not None:
        await extra_close.close()
    return sess


async def close_shared_session(session_id: str | None = None) -> None:
    """Close the keyed session. None/empty closes only the default ``_GLOBAL`` session."""
    async with _SESSIONS_GUARD:
        sess = _SESSIONS.pop(_session_key(session_id), None)
    if sess is not None:
        await sess.close()


async def close_all_shared_sessions() -> None:
    async with _SESSIONS_GUARD:
        items = list(_SESSIONS.items())
        _SESSIONS.clear()
    for _key, sess in items:
        if sess is not None:
            await sess.close()
