"""Persistent host session — cwd/env survive; interactive prompts get killed.

Default I/O is pipes (hidden, UTF-8). When a command needs a real console and
ConPTY APIs are present, we attach a pseudo-console so progress/TUI programs
do not hang on a pipe.
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

# Every escape sequence a pseudoconsole can emit: OSC (title etc.), CSI
# (cursor moves, colours, private modes such as ?9001h / ?25l), two-byte
# ESC sequences, and the stray C0 controls (BEL, BS) that are not text.
_VT_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL | ST
    r"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1b[ -/]*[@-~]"  # ESC + intermediate* + final
    r"|[\x07\x08]"
)

_PROMPT_MARKERS = (
    b"password:",
    b"[y/n]",
    b"(y/n)",
    b"are you sure",
    b"press any key",
    b"enter passphrase",
)


def _child_exit_code(proc: Any) -> int | None:
    """The child's exit code if it has already ended, else None.

    asyncio.subprocess.Process exposes ``returncode`` but not ``poll``.
    ``_ConPTYProcess`` has both — ``poll()`` is GetExitCodeProcess.
    """
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
    """One long-lived cmd/pwsh process. Not the default for bash_exec."""

    host: str = "cmd"
    cwd: str | None = None
    env: dict[str, str] | None = None
    use_conpty: bool = False
    _proc: Any = field(default=None, init=False, repr=False)
    _buf: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    _stdout_pending: asyncio.Task[bytes] | None = field(
        default=None, init=False, repr=False
    )
    started: bool = field(default=False, init=False)

    async def start(self) -> None:
        if self.started and self._alive():
            return
        self._lock = asyncio.Lock()
        argv = _session_argv(self.host)
        env = dict(self.env or os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        if os.name == "nt":
            env.setdefault("CHCP", "65001")
        from remedy.execution.process import create_hidden_subprocess_exec

        used_conpty = False
        if self.use_conpty and os.name == "nt":
            proc = await _try_conpty_exec(argv, cwd=self.cwd, env=env)
            used_conpty = proc is not None
        else:
            proc = None
        if proc is None:
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
        self.started = True
        self._used_conpty = used_conpty
        # Quiet the banner / set UTF-8
        boot = _boot_commands(self.host)
        if boot:
            await self._send_raw(boot + "\n")
            await asyncio.sleep(0.05)

    async def run(self, command: str, *, timeout: float = 60.0) -> SessionResult:
        if not command or not str(command).strip():
            return SessionResult(exit_code=-1, stdout="", stderr="empty command", host=self.host)
        await self.start()
        assert self._lock is not None
        with suppress(Exception):
            from remedy.core.turn_context import register_turn_process

            register_turn_process(self._proc)
        token = uuid.uuid4().hex[:10]
        sentinel = f"{_SENTINEL_PREFIX}{token}"
        wrapped = _wrap_with_sentinel(self.host, command.strip(), sentinel)
        try:
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
            conpty=bool(getattr(self, "_used_conpty", False)),
        )
        cwd = ""
        if shell_exit is not None:
            # The child died while we waited for the sentinel (`exit`,
            # `exit /b N`, the shell crashing). Returning a timeout here used
            # to kill a process that was already gone and leave the next
            # `run()` writing to a closed pipe.
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
                used_conpty=bool(getattr(self, "_used_conpty", False)),
            )
        if timed_out:
            # Do not leave the timed-out command running in the shared shell.
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
            used_conpty=bool(getattr(self, "_used_conpty", False)),
        )

    async def current_cwd(self) -> str:
        if not self._alive():
            return ""
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
            # Same as run(): a wedged cwd probe must not leave ReadFile pending.
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
            conpty=bool(getattr(self, "_used_conpty", False)),
        )
        del _code
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        self.started = False
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
        self._proc = None
        self.started = False
        self._stdout_pending = None

    def _alive(self) -> bool:
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
        if getattr(self, "_used_conpty", False):
            # A pseudoconsole is a keyboard, not a pipe: cooked line input is
            # submitted by Enter (CR). A bare LF is just typed into the line,
            # so the shell never ran anything and every run() timed out.
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r")
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
            # One outstanding read — never wait_for-cancel a blocking ConPTY ReadFile.
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
                # The fake console ReadFile is non-blocking: empty while the
                # child is still alive just means "nothing yet". A real
                # blocking ReadFile returning empty is EOF, and poll() will
                # have seen the death above. Sleep a beat so we do not spin.
                await asyncio.sleep(min(0.05, remaining))
                continue
            buf.extend(chunk)
            # The sentinel means the command COMPLETED — check it first so a
            # command that merely printed a prompt-like word (cat a file with
            # "password:", git output with "are you sure") is never mistaken
            # for a live prompt.
            if _sentinel_done(bytes(buf), marker):
                return bytes(buf), False, interactive, None
            # A real interactive prompt is the CURRENT unterminated line: the
            # process is blocked on stdin, so the marker sits AFTER the last
            # newline with nothing following it. Output that contains the marker
            # followed by a newline is just text and must not kill the shared
            # session (that was destroying legitimate commands + losing cwd/env).
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
    if host == "pwsh":
        return (
            f"{command}\n"
            f'Write-Output "{sentinel}:$LASTEXITCODE"\n'
        )
    if host == "cmd" or os.name == "nt":
        return f"{command}\necho {sentinel}:%ERRORLEVEL%\n"
    return f"{command}\necho {sentinel}:$?\n"


def _sentinel_pattern(sentinel: bytes | str) -> re.Pattern[Any]:
    """Match the sentinel only in its EXPANDED form: ``SENTINEL:<digits>`` at
    the end of a line.

    A pseudoconsole echoes what we type, so ``echo SENTINEL:%ERRORLEVEL%``
    (or ``"SENTINEL:$LASTEXITCODE"``) appears in the stream before the command
    has run. Requiring the character after the colon to be a digit or a line
    end (``
``, ``
``, or the ESC that ConPTY uses instead of a newline)
    rejects the echo, whose next character is ``%`` or ``$``. The digits may
    be empty: PowerShell's ``$LASTEXITCODE`` is null until a native command
    has run.
    """
    if isinstance(sentinel, bytes):
        return re.compile(re.escape(sentinel) + rb":(-?\d*)(?=[\r\n\x1b])")
    return re.compile(re.escape(sentinel) + r":(-?\d*)(?=[\r\n\x1b])")


def _sentinel_done(buf: bytes, sentinel: bytes) -> bool:
    return _sentinel_pattern(sentinel).search(buf) is not None


def strip_vt(text: str) -> str:
    """Remove every terminal escape sequence a pseudoconsole may have added."""
    return _VT_RE.sub("", text)


def _find_ignoring_whitespace(haystack: str, needle: str, start: int = 0) -> tuple[int, int]:
    """``(start, end)`` of *needle* in *haystack*, ignoring whitespace in both.

    ConPTY may re-wrap an echoed command at the screen width or replace its
    newlines with cursor moves, so the echo is the typed text modulo
    whitespace. Returns ``(-1, -1)`` when absent.
    """
    want = "".join(needle.split())
    if not want:
        return -1, -1
    chars: list[str] = []
    index: list[int] = []
    for i in range(start, len(haystack)):
        ch = haystack[i]
        if not ch.isspace():
            chars.append(ch)
            index.append(i)
    pos = "".join(chars).find(want)
    if pos < 0:
        return -1, -1
    return index[pos], index[pos + len(want) - 1] + 1


def _strip_echo(body: str, command: str) -> str:
    """Drop the console's echo of what we typed, keeping only what it printed.

    *command* is the full wrapped text we sent: the owner's command followed
    by the sentinel line. The echo of the sentinel line comes AFTER the
    command's output, so everything from it on is cut; the echo of the command
    itself (and anything before it: the boot commands, a prompt) is cut too.
    """
    lines = [ln for ln in command.splitlines() if ln.strip()]
    if not lines:
        return body
    typed = "\n".join(lines[:-1]) if len(lines) > 1 else ""
    sentinel_line = lines[-1]
    s, _e = _find_ignoring_whitespace(body, sentinel_line)
    if s >= 0:
        body = body[:s]
    if typed:
        _s, e = _find_ignoring_whitespace(body, typed)
        if e >= 0:
            body = body[e:]
    return body


def _split_sentinel(
    text: str,
    sentinel: str,
    *,
    host: str = "",
    command: str = "",
    conpty: bool = False,
) -> tuple[int, str]:
    del host  # the sentinel grammar is the same for every host
    m = _sentinel_pattern(sentinel).search(text)
    if m is None:
        return -1, strip_vt(text).strip() if conpty else text
    num = m.group(1)
    try:
        code = int(num) if num else 0
    except ValueError:
        code = 0
    body = text[: m.start()]
    if conpty:
        body = _strip_echo(strip_vt(body), command)
    return code, body.strip()


# -- ConPTY (best-effort; pipe session is the supported default) ---------------


def conpty_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return hasattr(k32, "CreatePseudoConsole")
    except Exception:
        return False


async def _try_conpty_exec(
    argv: list[str],
    *,
    cwd: str | None,
    env: dict[str, str],
) -> Any | None:
    """Attempt a ConPTY-backed process. None → caller uses pipes."""
    if not conpty_available():
        return None
    try:
        from remedy.execution.host.conpty import spawn_conpty

        return await spawn_conpty(argv, cwd=cwd, env=env)
    except Exception:
        return None


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
