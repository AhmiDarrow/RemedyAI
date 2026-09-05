"""Persistent host session — thin binding over Zig HostSession.

Windows open/run/cwd/close and the sentinel protocol live in ``remedy_core``
(ABI 5). Live open is Windows-only; Linux and Darwin fail closed (no soft
pipe spawn). Protocol helpers still go through the Zig ABI.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

_SESSIONS_GUARD = asyncio.Lock()


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
            from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

            raise HostError("host_session_open", STATUS_UNSUPPORTED)
        await self._start_zig()

    async def _start_zig(self) -> None:
        """Windows: Zig HostSession owns spawn + sentinel I/O (authorized open)."""
        from remedy.core.computer import host_binding

        if self.env is not None:
            env = dict(self.env)
        else:
            from remedy.execution.env import scrub_subprocess_env

            env = scrub_subprocess_env()
        token, now_ms = host_binding.issue_host_session_token(self.host)
        handle = await asyncio.to_thread(
            host_binding.host_session_open_authorized,
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
            return SessionResult(exit_code=-1, stdout="", stderr="empty command", host=self.host)
        await self.start()
        assert self._lock is not None
        if not self._zig_handle:
            from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

            raise HostError("host_session_run", STATUS_UNSUPPORTED)
        return await self._run_zig(command.strip(), timeout=timeout)

    async def _run_zig(self, command: str, *, timeout: float) -> SessionResult:
        from remedy.core.computer import host_binding

        assert self._lock is not None
        async with self._lock:
            data = await asyncio.to_thread(
                host_binding.host_session_run,
                self._zig_handle,
                command,
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
        from remedy.core.computer import host_binding

        assert self._lock is not None
        async with self._lock:
            here = await asyncio.to_thread(
                host_binding.host_session_cwd, self._zig_handle
            )
        if not here:
            self._abandon_proc()
        return here or ""

    async def close(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            from remedy.core.computer import host_binding

            await asyncio.to_thread(host_binding.host_session_close, handle)

    def _abandon_proc(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            with suppress(Exception):
                from remedy.core.computer import host_binding

                host_binding.host_session_close(handle)

    def _alive(self) -> bool:
        return bool(self._zig_handle) and self.started


def conpty_available() -> bool:
    """True when ``remedy_core`` reports ConPTY (ABI 5) on this host."""
    if sys.platform != "win32":
        return False
    try:
        from remedy.core.computer import host_binding

        return bool(host_binding.conpty_available())
    except Exception:
        return False


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
