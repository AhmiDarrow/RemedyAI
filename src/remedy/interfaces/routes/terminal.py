"""SSE terminal for Grove Connect phone (and web rails).

The desktop app shells out to Tauri ``pty_open`` / ``pty_write`` / ``pty_close``;
the phone has no Tauri bridge, so Grove Connect needs the same capability as
plain HTTP. This module exposes a minimal ConPTY/subprocess-backed terminal:

    POST   /api/terminal              → open a shell, returns {terminal_id}
    GET    /api/terminal/{id}/stream  → SSE: event: output / exit (+ keepalive)
    POST   /api/terminal/{id}/input   → {"data": "..."} writes to stdin
    POST   /api/terminal/{id}/resize  → {"cols": n, "rows": n}
    DELETE /api/terminal/{id}         → close the shell

Trust model: only reachable through the Connect pipe (Noise-authenticated
pairing + the loopback shim token). The desktop terminal has the same blast
radius — this is the phone getting the identical capability over HTTP.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from remedy.interfaces.api_support import sse_headers

logger = logging.getLogger(__name__)

_MAX_TERMINALS = 16
_IDLE_KEEPALIVE_S = 10.0
_IDLE_GRACE_S = 60.0
_READ_CHUNK = 8192
_MAX_INPUT_BYTES = 65536  # one input POST (security: no unbounded writes)
_MAX_QUEUE_BYTES = 512 * 1024  # output flood cap: drop + notice beyond this
_MAX_SSE_BATCH = 16 * 1024  # coalesce output into one SSE event (speed)

# Windows: force the console to UTF-8 so ConPTY output arrives as UTF-8 bytes
# regardless of the OEM codepage (cp437/cp850). Without this, PowerShell 5.1
# writes box-drawing/unicode as OEM bytes and the phone renders mojibake.
_WIN_UTF8_INIT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
    " [Console]::InputEncoding=[System.Text.Encoding]::UTF8;"
    " chcp 65001 > $null"
)

# ANSI/VT escape sequences (CSI, OSC, charset-select). The phone shows plain
# text, so strip them server-side instead of shipping raw control bytes.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI: colors, cursor, erase
    r"|\x1b\][^\x07]*(?:\x07|\x1b\\)"  # OSC: title/kitty sequences
    r"|\x1b[()][0-9A-Za-z]"  # charset select
    r"|\x1b[=>]"  # keypad/alt modes
)


def _strip_ansi(text: str) -> str:
    """Remove terminal escape sequences; keep \n and \t for plain rendering."""
    text = _ANSI_RE.sub("", text)
    out = []
    for ch in text:
        if ch in "\n\t":
            out.append(ch)
        elif ch == "\r" or ord(ch) >= 32:
            out.append(ch)
    return "".join(out)


def _pick_shell() -> tuple[str, list[str]]:
    """Mirror desktop/src-tauri/src/pty_host.rs shell selection."""
    if os.name == "nt":
        pf = str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")).resolve())
        pf86 = str(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")).resolve())
        sysroot = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")).resolve())
        args = ["-NoLogo", "-NoProfile", "-NoExit", "-Command", _WIN_UTF8_INIT]
        for c in (
            str(Path(pf) / "PowerShell" / "7" / "pwsh.exe"),
            str(Path(pf86) / "PowerShell" / "7" / "pwsh.exe"),
            str(Path(sysroot) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ):
            if Path(c).is_file():
                return c, list(args)
        return "powershell.exe", list(args)
    shell = (os.environ.get("SHELL") or "").strip() or "/bin/sh"
    low = shell.lower()
    win_interop = low.endswith(".exe") or "/mnt/" in low or "\\" in low
    if not win_interop and Path(shell).is_file():
        return shell, ["-i"]
    return "/bin/sh", []


def _default_cwd() -> str | None:
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config()
        raw = (cfg.get("project_path") or "").strip()
        if raw and Path(raw).is_dir():
            return str(Path(raw).resolve())
    except Exception:
        pass
    return None


class _TerminalSession:
    """One shell: async stdin/stdout (ConPTY or pipes) + SSE output queue.

    The output buffer is a thread-safe ``queue.Queue`` (not asyncio.Queue):
    the pump task and the SSE generator can run on *different* event loops
    (httpx TestClient spins a fresh loop per request; uvicorn uses one loop).
    Consumers poll it via ``asyncio.to_thread``.
    """

    def __init__(self, proc: Any, *, cwd: str | None) -> None:
        self.id = f"term-{uuid.uuid4().hex[:12]}"
        self.proc = proc
        self.cwd = cwd
        self.q: queue.Queue[bytes | None] = queue.Queue()
        self.closed = False
        self.returncode: int | None = None
        self.created = time.time()
        self._dropped = 0  # output bytes dropped by the flood cap
        self._thread: threading.Thread | None = None
        self._reader: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None

    async def start(self) -> None:
        # ONE dedicated reader thread per shell (ConPTY): a blocked ReadFile
        # must never occupy the asyncio thread pool. Thread-per-chunk read
        # starves the whole sidecar when a shell is idle — the "server dies
        # when the phone drops" symptom. Pipe fallback (StreamReader) stays
        # async because its read() is a coroutine bound to this loop.
        if getattr(self.proc.stdout, "_read_sync", None) is not None:
            self._thread = threading.Thread(
                target=self._pump_sync,
                name=f"term-read-{self.id}",
                daemon=True,
            )
            self._thread.start()
        else:
            self._reader = asyncio.create_task(self._pump_async())

    def _push(self, data: bytes) -> bool:
        """Bound the output queue; return False when the session is closed."""
        if self.closed:
            return False
        if self.q.qsize() * _READ_CHUNK >= _MAX_QUEUE_BYTES:
            self._dropped += len(data)  # flood cap: drop, keep reading
            return True
        self.q.put(data)
        return True

    def _pump_sync(self) -> None:
        """ConPTY reader: blocking ReadFile in one dedicated thread."""
        try:
            read = self.proc.stdout._read_sync
            while True:
                data = read(_READ_CHUNK)
                if not data:
                    break
                if not self._push(data):
                    break
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                self.returncode = self.proc.poll()
            self.q.put(None)

    async def _pump_async(self) -> None:
        """Pipe fallback: StreamReader coroutine on this event loop."""
        try:
            while True:
                data = await self.proc.stdout.read(_READ_CHUNK)
                if not data:
                    break
                if not self._push(data):
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                self.returncode = self.proc.poll()
            self.q.put(None)

    async def write(self, data: str) -> None:
        if self.closed:
            raise HTTPException(410, "Terminal closed")
        if len(data) > _MAX_INPUT_BYTES:
            raise HTTPException(
                413, f"Input too large (max {_MAX_INPUT_BYTES} bytes)"
            )
        raw = data.encode("utf-8", errors="replace")
        if not raw:
            return
        writer = getattr(self.proc.stdin, "write", None)
        if writer is None:
            return
        # Both real stdins have a SYNCHRONOUS write: the ConPTY _HandleStream
        # (blocking WriteFile) and the asyncio StreamWriter (transport.write,
        # returns None). Awaiting either raised `TypeError: NoneType can't be
        # awaited`, which BaseHTTPMiddleware surfaced as a 404 "Request
        # aborted" — so typing into the phone terminal did nothing. Only await
        # a genuinely async write (test doubles / future transports).
        if asyncio.iscoroutinefunction(writer):
            await writer(raw)
        else:
            writer(raw)
        drain = getattr(self.proc.stdin, "drain", None)
        if drain is not None:
            with contextlib.suppress(Exception):
                await drain()

    async def resize(self, cols: int, rows: int) -> None:
        # ConPTY-backed sessions support live resize; pipe fallback ignores.
        resize = getattr(self.proc, "resize", None)
        if callable(resize):
            with contextlib.suppress(Exception):
                await resize(cols, rows)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._reader is not None:
            self._reader.cancel()
            with contextlib.suppress(BaseException):
                await self._reader
        # Kill the process to unblock the dedicated reader thread's ReadFile.
        with contextlib.suppress(Exception):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.q.put(None)

    def schedule_idle_close(self, delay_s: float = _IDLE_GRACE_S) -> None:
        """Close after a grace period unless a new stream re-attaches.

        A phone dropping its SSE (rotation, brief network blip) must not kill
        the shell; but a terminal whose owner walked away should not leak.
        """

        async def _do() -> None:
            try:
                await asyncio.sleep(delay_s)
            except asyncio.CancelledError:
                return
            if not self.closed:
                await self.close()

        if self._idle_task is not None:
            self._idle_task.cancel()
        # Loop may be closing (test teardown / app shutdown): never let a
        # create_task raise inside an SSE generator's finally.
        with contextlib.suppress(RuntimeError):
            self._idle_task = asyncio.create_task(_do())

    def cancel_idle_close(self) -> None:
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None


# Module-level registry survives route re-registration (tests recreate apps).
_TERMINALS: dict[str, _TerminalSession] = {}

# Test hook: async fn(cwd, cols, rows) -> proc-like or None. When set, it
# wins over the real ConPTY/pipe spawn so unit tests never launch a shell.
_SPAWN_OVERRIDE: Any | None = None


async def _spawn_terminal(
    *,
    cwd: str | None,
    cols: int,
    rows: int,
    shell_argv: list[str] | None = None,
) -> _TerminalSession:
    """Spawn a shell. Prefers ConPTY on Windows; falls back to pipe subprocess."""
    if len(_TERMINALS) >= _MAX_TERMINALS:
        # Evict the oldest idle terminal to keep the cap honest.
        oldest = min(_TERMINALS.values(), key=lambda t: t.created)
        with contextlib.suppress(Exception):
            await oldest.close()
        _TERMINALS.pop(oldest.id, None)

    if _SPAWN_OVERRIDE is not None:
        proc = await _SPAWN_OVERRIDE(cwd=cwd, cols=cols, rows=rows)
        if proc is not None:
            sess = _TerminalSession(proc, cwd=cwd)
            await sess.start()
            return sess

    if shell_argv:
        argv = list(shell_argv)
    else:
        shell, args = _pick_shell()
        argv = [shell, *args]
    if os.name == "nt":
        try:
            from remedy.execution.host.conpty import spawn_conpty

            proc = await spawn_conpty(argv, cwd=cwd, env=os.environ.copy())
            if proc is not None:
                sess = _TerminalSession(proc, cwd=cwd)
                await sess.start()
                return sess
        except Exception as exc:
            logger.info("conpty spawn failed (%s); falling back to pipes", exc)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
        creationflags=(
            getattr(asyncio.subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt"
            else 0
        ),
    )
    sess = _TerminalSession(proc, cwd=cwd)
    await sess.start()
    return sess


class TerminalOpenRequest(BaseModel):
    cwd: str | None = None
    cols: int = Field(default=100, ge=20, le=400)
    rows: int = Field(default=28, ge=5, le=120)


class TerminalInputRequest(BaseModel):
    data: str = Field(default="", description="Text to write to stdin")


class TerminalResizeRequest(BaseModel):
    cols: int = Field(default=100, ge=20, le=400)
    rows: int = Field(default=28, ge=5, le=120)


def register_terminal_routes(app: FastAPI) -> None:
    """Attach /api/terminal* routes."""

    @app.post("/api/terminal")
    async def terminal_open(req: TerminalOpenRequest):
        cwd = (req.cwd or "").strip() or _default_cwd()
        if cwd and not Path(cwd).is_dir():
            cwd = _default_cwd()
        try:
            sess = await _spawn_terminal(cwd=cwd, cols=req.cols, rows=req.rows)
        except Exception as exc:
            logger.exception("terminal open failed")
            raise HTTPException(500, f"Could not start terminal: {exc}") from exc
        _TERMINALS[sess.id] = sess
        return {
            "terminal_id": sess.id,
            "cwd": sess.cwd,
            "shell": os.path.basename(_pick_shell()[0]),
        }

    @app.get("/api/terminal/{terminal_id}/stream")
    async def terminal_stream(terminal_id: str):
        sess = _TERMINALS.get(terminal_id)
        if sess is None:
            raise HTTPException(404, "Terminal not found")

        async def gen():
            sess.cancel_idle_close()  # a stream is attached again
            last_touch = time.time()
            dropped = sess._dropped
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        chunk = sess.q.get_nowait()
                    except queue.Empty:
                        if time.time() - last_touch >= _IDLE_KEEPALIVE_S:
                            yield ": keepalive\n\n"
                            last_touch = time.time()
                        await asyncio.sleep(0.05)
                        continue
                    last_touch = time.time()
                    if chunk is None:
                        break
                    # Coalesce pending output into ONE SSE event: fast shells
                    # emit many tiny chunks, and one event per chunk floods
                    # the tunnel with micro-frames (the "terminal is very
                    # slow" symptom). Batch up to the cap, then flush.
                    parts = [chunk]
                    total = len(chunk)
                    while total < _MAX_SSE_BATCH:
                        try:
                            nxt = sess.q.get_nowait()
                        except queue.Empty:
                            break
                        if nxt is None:
                            # Shell exited mid-batch: flush what we already
                            # collected (the last lines before exit) and end
                            # on the next loop turn instead of dropping them.
                            with contextlib.suppress(Exception):
                                sess.q.put_nowait(None)
                            break
                        parts.append(nxt)
                        total += len(nxt)
                    if sess._dropped != dropped:
                        parts.insert(
                            0,
                            (
                                "\n[output truncated: "
                                f"{sess._dropped - dropped} bytes dropped]\n"
                            ).encode("utf-8", errors="replace"),
                        )
                        dropped = sess._dropped
                    text = b"".join(parts).decode("utf-8", errors="replace")
                    if not text:
                        continue
                    text = _strip_ansi(text)
                    if not text:
                        continue
                    payload = json.dumps({"type": "output", "text": text})
                    yield f"event: output\ndata: {payload}\n\n"
                code = sess.returncode
                if code is None:
                    with contextlib.suppress(Exception):
                        code = sess.proc.poll()
                payload = json.dumps({"type": "exit", "code": code})
                yield f"event: exit\ndata: {payload}\n\n"
            finally:
                # Phone dropped the SSE (rotation / network blip): do NOT kill
                # the shell instantly — give the app time to re-attach. The
                # idle grace close also runs when the stream ends normally.
                if not sess.closed:
                    sess.schedule_idle_close()
                else:
                    # Stream ended because the shell closed; drop the registry
                    # entry so a stale id can't linger (caps stay honest).
                    with contextlib.suppress(Exception):
                        _TERMINALS.pop(sess.id, None)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers=sse_headers(),
        )

    @app.post("/api/terminal/{terminal_id}/input")
    async def terminal_input(terminal_id: str, req: TerminalInputRequest):
        sess = _TERMINALS.get(terminal_id)
        if sess is None:
            raise HTTPException(404, "Terminal not found")
        await sess.write(req.data)
        return {"ok": True}

    @app.post("/api/terminal/{terminal_id}/resize")
    async def terminal_resize(terminal_id: str, req: TerminalResizeRequest):
        sess = _TERMINALS.get(terminal_id)
        if sess is None:
            raise HTTPException(404, "Terminal not found")
        await sess.resize(req.cols, req.rows)
        return {"ok": True}

    @app.delete("/api/terminal/{terminal_id}")
    async def terminal_close(terminal_id: str):
        sess = _TERMINALS.pop(terminal_id, None)
        if sess is None:
            raise HTTPException(404, "Terminal not found")
        await sess.close()
        return {"ok": True}


__all__ = ["register_terminal_routes"]
