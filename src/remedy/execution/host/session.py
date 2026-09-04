"""Persistent host session — thin binding over Zig HostSession.

Windows open/run/cwd/close and the sentinel protocol (wrap/split/VT/echo)
live in ``remedy_core`` (ABI 5). Zig live open is Windows-only; Linux fails
closed (no soft pipe spawn). Darwin may still use hidden pipes. Protocol
helpers still go through the Zig ABI.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SENTINEL_PREFIX = "REMEDY_HOST_DONE_"
_SESSIONS_GUARD = asyncio.Lock()

# Expanded-sentinel check for the POSIX pipe reader only. Wrap/split ownership
# is Zig; this regex matches ``host_session.findSentinel`` (digit or empty
# code, ended by CR/LF/ESC) so we do not treat ConPTY-style unexpanded echoes
# as completion if a POSIX test ever feeds them.
_SENTINEL_DONE_RE = re.compile(rb":(-?\d*)(?=[\r\n\x1b])")

_PROMPT_MARKERS = (
    b"password:",
    b"[y/n]",
    b"(y/n)",
    b"are you sure",
    b"press any key",
    b"enter passphrase",
)


def _child_exit_code(proc: Any) -> int | None:
    """The child's exit code if it has already ended, else None."""
    poll = getattr(proc, "poll", None)
    if callable(poll):
        with suppress(Exception):
            rc = poll()
            if rc is not None:
                return int(rc)
    rc = getattr(proc, "returncode", None)
    return int(rc) if rc is not None else None


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
    """One long-lived cmd/pwsh/posix process. Not the default for bash_exec.

    Windows: Zig ``remedy_core`` HostSession owns spawn + sentinel I/O (no
    Python ConPTY/pipe twin). Linux: fail closed (no Zig live open / no soft
    pipes). Darwin: hidden pipes; wrap/split still Zig.
    """

    host: str = "cmd"
    cwd: str | None = None
    env: dict[str, str] | None = None
    use_conpty: bool = False
    _proc: Any = field(default=None, init=False, repr=False)
    _zig_handle: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _stdout_pending: asyncio.Task[bytes] | None = field(
        default=None, init=False, repr=False
    )
    started: bool = field(default=False, init=False)
    _used_conpty: bool = field(default=False, init=False, repr=False)

    async def start(self) -> None:
        if self.started and self._alive():
            return
        self._lock = asyncio.Lock()
        if os.name == "nt":
            await self._start_zig()
            return
        await self._start_posix_pipes()

    async def _start_zig(self) -> None:
        """Windows: Zig HostSession owns spawn + sentinel I/O (authorized open)."""
        from remedy.core.computer import host_binding

        if self.env is not None:
            env = dict(self.env)
        else:
            from remedy.execution.env import scrub_subprocess_env

            env = scrub_subprocess_env()
        # Token hashes Zig's own session argv (PATH / SystemRoot resolution).
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
        self._proc = None
        self.started = True
        self._used_conpty = bool(self.use_conpty)

    async def _start_posix_pipes(self) -> None:
        # Zig HostSession live open is Windows-only. Linux must not soft-pipe.
        if sys.platform in ("win32", "linux"):
            from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

            raise HostError("host_session_posix_pipes", STATUS_UNSUPPORTED)
        argv = _session_argv(self.host)
        if self.env is not None:
            env = dict(self.env)
        else:
            from remedy.execution.env import scrub_subprocess_env

            env = scrub_subprocess_env()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        env.setdefault("GCM_INTERACTIVE", "never")
        env.setdefault("GH_PROMPT_DISABLED", "1")
        for key in list(env):
            if key.upper() == "GIT_ASKPASS":
                env.pop(key, None)
        from remedy.execution.process import create_hidden_subprocess_exec

        proc = await create_hidden_subprocess_exec(
            argv[0],
            *argv[1:],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.cwd,
            env=env,
        )
        self._proc = proc
        self._zig_handle = 0
        self.started = True
        self._used_conpty = False
        boot = _boot_commands(self.host)
        if boot:
            await self._send_raw(boot + "\n")
            await asyncio.sleep(0.05)

    async def run(self, command: str, *, timeout: float = 60.0) -> SessionResult:
        if not command or not str(command).strip():
            return SessionResult(exit_code=-1, stdout="", stderr="empty command", host=self.host)
        await self.start()
        assert self._lock is not None
        if self._zig_handle:
            return await self._run_zig(command.strip(), timeout=timeout)
        return await self._run_posix(command.strip(), timeout=timeout)

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

    async def _run_posix(self, command: str, *, timeout: float) -> SessionResult:
        with suppress(Exception):
            from remedy.core.turn_context import register_turn_process

            register_turn_process(self._proc)
        token = uuid.uuid4().hex[:10]
        sentinel = f"{_SENTINEL_PREFIX}{token}"
        wrapped = _wrap_with_sentinel(self.host, command, sentinel)
        try:
            assert self._lock is not None
            async with self._lock:
                await self._send_raw(wrapped)
                raw, timed_out, interactive, shell_exit = await self._read_until(
                    sentinel.encode("ascii"), timeout=max(1.0, float(timeout))
                )
        finally:
            with suppress(Exception):
                from remedy.core.turn_context import unregister_turn_process

                unregister_turn_process(self._proc)
        text = raw.decode("utf-8", errors="replace")
        code, body = _split_sentinel(
            text,
            sentinel,
            host=self.host,
            command=wrapped,
            conpty=False,
        )
        cwd = ""
        if shell_exit is not None:
            self._abandon_proc()
            note = f"the shell exited (code {shell_exit}) while running the command"
            if note not in body:
                body = f"{body}\n{note}" if body else note
            return SessionResult(
                exit_code=int(shell_exit),
                stdout=body,
                timed_out=False,
                interactive=interactive,
                cwd="",
                host=self.host,
                used_conpty=False,
            )
        if timed_out:
            with suppress(Exception):
                from remedy.execution.process import kill_process_tree

                kill_process_tree(self._proc)
            self._abandon_proc()
        else:
            cwd = await self.current_cwd()
        return SessionResult(
            exit_code=code if not timed_out else -1,
            stdout=body,
            timed_out=timed_out,
            interactive=interactive,
            cwd=cwd,
            host=self.host,
            used_conpty=False,
        )

    async def current_cwd(self) -> str:
        if not self._alive():
            return ""
        if self._zig_handle:
            from remedy.core.computer import host_binding

            assert self._lock is not None
            async with self._lock:
                here = await asyncio.to_thread(
                    host_binding.host_session_cwd, self._zig_handle
                )
            if not here:
                self._abandon_proc()
            return here or ""
        token = uuid.uuid4().hex[:8]
        sentinel = f"{_SENTINEL_PREFIX}cwd_{token}"
        cmd = _cwd_command(self.host)
        wrapped = _wrap_with_sentinel(self.host, cmd, sentinel)
        assert self._lock is not None
        async with self._lock:
            await self._send_raw(wrapped)
            raw, timed_out, _, shell_exit = await self._read_until(
                sentinel.encode("ascii"), timeout=8.0
            )
        if timed_out or shell_exit is not None:
            if timed_out:
                with suppress(Exception):
                    from remedy.execution.process import kill_process_tree

                    kill_process_tree(self._proc)
            self._abandon_proc()
            return ""
        _code, body = _split_sentinel(
            raw.decode("utf-8", errors="replace"),
            sentinel,
            host=self.host,
            command=wrapped,
            conpty=False,
        )
        del _code
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    async def close(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        proc = self._proc
        self._proc = None
        self.started = False
        if handle:
            from remedy.core.computer import host_binding

            await asyncio.to_thread(host_binding.host_session_close, handle)
            return
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.write(b"exit\n")
                await proc.stdin.drain()
        except Exception:
            pass
        from remedy.execution.process import kill_process_tree

        kill_process_tree(proc)

    def _abandon_proc(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self._proc = None
        self.started = False
        self._stdout_pending = None
        if handle:
            with suppress(Exception):
                from remedy.core.computer import host_binding

                host_binding.host_session_close(handle)

    def _alive(self) -> bool:
        if self._zig_handle:
            return self.started
        proc = self._proc
        if proc is None:
            return False
        if _child_exit_code(proc) is not None:
            return False
        return getattr(proc, "returncode", None) is None

    async def _send_raw(self, text: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("host session is not running")
        data = text.encode("utf-8", errors="replace")
        if not data.endswith(b"\n"):
            data += b"\n"
        proc.stdin.write(data)
        await proc.stdin.drain()

    async def _read_until(
        self, marker: bytes, *, timeout: float
    ) -> tuple[bytes, bool, bool, int | None]:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return b"", True, False, None
        buf = bytearray()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        interactive = False
        while True:
            with suppress(Exception):
                from remedy.core.turn_context import is_turn_aborted

                if is_turn_aborted():
                    from remedy.execution.process import kill_process_tree

                    kill_process_tree(proc)
                    return bytes(buf), True, False, None
            remaining = deadline - loop.time()
            if remaining <= 0:
                return bytes(buf), True, interactive, None
            if self._stdout_pending is None or self._stdout_pending.done():
                self._stdout_pending = asyncio.ensure_future(proc.stdout.read(4096))
            done, _pending = await asyncio.wait(
                {self._stdout_pending},
                timeout=min(0.4, remaining),
            )
            if self._stdout_pending not in done:
                continue
            try:
                chunk = self._stdout_pending.result()
            except Exception:
                self._stdout_pending = None
                exited = _child_exit_code(proc)
                if exited is not None:
                    return bytes(buf), False, interactive, exited
                return bytes(buf), True, interactive, None
            self._stdout_pending = None
            if not chunk:
                exited = _child_exit_code(proc)
                if exited is not None:
                    return bytes(buf), False, interactive, exited
                await asyncio.sleep(min(0.05, remaining))
                continue
            buf.extend(chunk)
            if _sentinel_done(bytes(buf), marker):
                return bytes(buf), False, interactive, None
            tail = bytes(buf).lower().rsplit(b"\n", 1)[-1]
            if any(m in tail for m in _PROMPT_MARKERS):
                interactive = True
                from remedy.execution.process import kill_process_tree

                kill_process_tree(proc)
                self._abandon_proc()
                return bytes(buf), True, True, None


def _cwd_command(host: str) -> str:
    """Print working directory for the session host dialect."""
    if host == "cmd":
        return "cd"
    if host == "pwsh":
        return "(Get-Location).Path"
    return "pwd"


def _session_argv(host: str) -> list[str]:
    if host == "pwsh":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
        return [exe, "-NoLogo", "-NoProfile"]
    if os.name == "nt":
        exe = shutil.which("cmd") or "cmd.exe"
        return [exe, "/Q", "/K"]
    sh = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    return [sh]


def _boot_commands(host: str) -> str:
    if host == "cmd" and os.name == "nt":
        return "chcp 65001 >NUL & @echo off"
    if host == "pwsh":
        return "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()"
    return ""


def _wrap_with_sentinel(host: str, command: str, sentinel: str) -> str:
    """Zig sentinel wrap (portable protocol helper)."""
    from remedy.core.computer import host_binding

    return host_binding.host_session_wrap(host=host, command=command, sentinel=sentinel)


def _sentinel_done(buf: bytes, sentinel: bytes) -> bool:
    """True when *buf* contains an expanded ``SENTINEL:<digits>`` line end."""
    idx = 0
    while True:
        found = buf.find(sentinel, idx)
        if found < 0:
            return False
        rest = buf[found + len(sentinel) :]
        if _SENTINEL_DONE_RE.match(rest):
            return True
        idx = found + 1


def _split_sentinel(
    text: str,
    sentinel: str,
    *,
    host: str = "",
    command: str = "",
    conpty: bool = False,
) -> tuple[int, str]:
    """Zig sentinel split (portable protocol helper)."""
    del host
    from remedy.core.computer import host_binding

    return host_binding.host_session_split(
        text=text,
        sentinel=sentinel,
        command=command,
        conpty=conpty,
    )


def conpty_available() -> bool:
    """True when ``remedy_core`` reports ConPTY (ABI 5) on this host."""
    if sys.platform != "win32":
        return False
    try:
        from remedy.execution.host.conpty import spawn_conpty_supported

        return bool(spawn_conpty_supported())
    except Exception:
        return False


# Per-chat-session host shells. Do not reuse across session_id or start cwd.
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
