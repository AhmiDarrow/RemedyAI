"""Tests for the /api/terminal SSE route (Grove Connect phone terminal).

The SSE stream is exercised against a real uvicorn server in a background
thread: httpx TestClient does not pump long-lived SSE generators (it returns
an empty body once the generator awaits between yields), so testing the stream
through it produces false failures. JSON endpoints use TestClient.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import urllib.request
from contextlib import closing, suppress

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import remedy.interfaces.routes.terminal as terminal_mod
from remedy.interfaces.routes.terminal import (
    _TERMINALS,
    _pick_shell,
    register_terminal_routes,
)

PORT = 18741


class _FakeStdout:
    def __init__(self, fake: _FakeProc) -> None:
        self._fake = fake

    async def read(self, n: int) -> bytes:
        return await self._fake.stdout_read(n)


class _FakeStdin:
    def __init__(self, fake: _FakeProc) -> None:
        self._fake = fake

    async def write(self, data: bytes) -> None:
        await self._fake.stdin_write(data)

    async def drain(self) -> None:
        pass


class _FakeProc:
    """Duck-typed subprocess: writes READY, echoes input lines back."""

    def __init__(self) -> None:
        self._buf_in: list[bytes] = []
        self._out: list[bytes] = [b"READY\n"]
        self.returncode: int | None = None
        self.killed = False
        self.stdout = _FakeStdout(self)
        self.stdin = _FakeStdin(self)

    async def stdout_read(self, n: int) -> bytes:
        if self._out:
            return self._out.pop(0)
        # Wait patiently (20s) until input arrives — simulates a live shell.
        for _ in range(2000):
            if self._buf_in:
                line = self._buf_in.pop(0)
                self._out.append(b"ECHO:" + line)
                return self._out.pop(0)
            await asyncio.sleep(0.01)
        return b""

    async def stdin_write(self, data: bytes) -> None:
        for line in data.split(b"\n"):
            if line:
                self._buf_in.append(line)

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = 1


class _AnsiFakeProc(_FakeProc):
    """Fake shell that emits ANSI/VT escapes (colors, title, erase-line)."""

    def __init__(self) -> None:
        super().__init__()
        self._out = [
            b"\x1b[32mREADY\x1b[0m\n",
            b"\x1b]0;remedy\x07PWD: ",
            b"\x1b[K\x1b[1m>\x1b[0m ",
        ]


@pytest.fixture(autouse=True)
def _clean_terminals(monkeypatch):
    """Clear the registry and install a deterministic fake spawner."""
    _TERMINALS.clear()

    async def fake_spawn(*, cwd, cols, rows):
        return _FakeProc()

    monkeypatch.setattr(terminal_mod, "_SPAWN_OVERRIDE", fake_spawn)
    yield
    for sess in list(_TERMINALS.values()):
        with suppress(Exception):
            sess.proc.kill()
    _TERMINALS.clear()


def _app() -> TestClient:
    app = FastAPI()
    register_terminal_routes(app)
    return TestClient(app)


def _open_terminal(client: TestClient) -> str:
    r = client.post("/api/terminal", json={"cwd": None, "cols": 100, "rows": 28})
    assert r.status_code == 200, r.text
    tid = r.json().get("terminal_id")
    assert tid and tid.startswith("term-")
    return tid


def _http_post_json(path: str, payload: dict) -> dict:
    """POST JSON to the uvicorn server (urllib — shares uvicorn's event loop)."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode())


def _http_delete(path: str) -> int:
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}", method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _read_sse_until(tid: str, needle: bytes, timeout: float = 8.0) -> bytes:
    """Open the SSE stream over a raw socket and read until *needle* appears."""
    with closing(socket.create_connection(("127.0.0.1", PORT), timeout=timeout)) as s:
        s.settimeout(timeout)
        s.sendall(
            f"GET /api/terminal/{tid}/stream HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Accept: text/event-stream\r\n"
            "Connection: close\r\n\r\n".encode()
        )
        data = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in data:
                return data
            try:
                chunk = s.recv(4096)
            except TimeoutError:
                break
            if not chunk:
                break
            data += chunk
        return data


@pytest.fixture(scope="module")
def uvicorn_server():
    """Boot a real uvicorn on PORT once, with the fake-spawn override active."""
    import uvicorn

    terminal_mod._TERMINALS.clear()

    async def fake_spawn(*, cwd, cols, rows):
        return _FakeProc()

    terminal_mod._SPAWN_OVERRIDE = fake_spawn

    app = FastAPI()
    register_terminal_routes(app)
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield server
    server.should_exit = True
    thread.join(timeout=5)
    terminal_mod._SPAWN_OVERRIDE = None


def test_pick_shell_returns_list() -> None:
    shell, args = _pick_shell()
    assert isinstance(shell, str) and shell
    assert isinstance(args, list)


def test_strip_ansi_removes_escapes_keeps_text() -> None:
    from remedy.interfaces.routes.terminal import _strip_ansi

    assert _strip_ansi("\x1b[32mOK\x1b[0m\n") == "OK\n"
    assert _strip_ansi("\x1b]0;Remedy\x07Title") == "Title"
    assert _strip_ansi("a\x1b[Kb") == "ab"
    assert _strip_ansi("plain\ttext\n") == "plain\ttext\n"
    assert _strip_ansi("\x00\x01\x02junk") == "junk"


def test_stream_strips_ansi_end_to_end(uvicorn_server) -> None:
    import json as _json

    async def fake_spawn(*, cwd, cols, rows):
        return _AnsiFakeProc()

    terminal_mod._SPAWN_OVERRIDE = fake_spawn
    try:
        payload = _http_post_json("/api/terminal", {"cwd": None, "cols": 100, "rows": 28})
        tid = payload["terminal_id"]
        data = _read_sse_until(tid, b"> ", timeout=10.0)
        text = ""
        for line in data.decode("utf-8", errors="replace").splitlines():
            if line.startswith("data: "):
                with suppress(Exception):
                    text += _json.loads(line[6:]).get("text", "")
        assert "\x1b" not in text, text
        assert "READY" in text
        assert "PWD" in text
    finally:
        terminal_mod._SPAWN_OVERRIDE = None


def test_terminal_open_returns_id() -> None:
    client = _app()
    tid = _open_terminal(client)
    assert tid in _TERMINALS


def test_terminal_delete_closes() -> None:
    client = _app()
    tid = _open_terminal(client)
    r = client.delete(f"/api/terminal/{tid}")
    assert r.status_code == 200
    assert r.json().get("ok") is True
    assert tid not in _TERMINALS


def test_terminal_input_on_closed_returns_410() -> None:
    client = _app()
    tid = _open_terminal(client)
    client.delete(f"/api/terminal/{tid}")
    r = client.post(f"/api/terminal/{tid}/input", json={"data": "x"})
    assert r.status_code in (404, 410)


def test_terminal_stream_delivers_ready(uvicorn_server):
    """The SSE stream over real HTTP carries the shell's first output."""
    tid = _http_post_json("/api/terminal", {})["terminal_id"]
    data = _read_sse_until(tid, b"READY")
    assert b"event: output" in data
    assert b"READY" in data


def test_terminal_stream_sse_framing(uvicorn_server):
    """Stream frames are proper SSE: event: output with JSON data."""
    tid = _http_post_json("/api/terminal", {})["terminal_id"]
    data = _read_sse_until(tid, b"READY")
    text = data.decode("utf-8", errors="replace")
    assert "event: output" in text
    data_line = next(
        (ln for ln in text.splitlines() if ln.startswith("data: ")), ""
    )
    assert data_line
    payload = json.loads(data_line[len("data: ") :])
    assert payload.get("type") == "output"
    assert "text" in payload


def test_terminal_input_echoes_over_stream(uvicorn_server):
    """POST /input reaches the shell and its echo arrives on the SSE stream."""
    tid = _http_post_json("/api/terminal", {})["terminal_id"]
    # Drain the ready line first.
    _read_sse_until(tid, b"READY")
    _http_post_json(f"/api/terminal/{tid}/input", {"data": "hello\n"})
    data = _read_sse_until(tid, b"ECHO:hello")
    assert b"ECHO:hello" in data


def test_terminal_reconnect_survives_after_drop(uvicorn_server):
    """A dropped SSE must NOT kill the shell — re-attach gets output."""
    tid = _http_post_json("/api/terminal", {})["terminal_id"]
    _read_sse_until(tid, b"READY")  # opens + closes the stream
    # Shell should still be alive: input works and echo streams on a new SSE.
    _http_post_json(f"/api/terminal/{tid}/input", {"data": "again\n"})
    data = _read_sse_until(tid, b"ECHO:again")
    assert b"ECHO:again" in data


def test_terminal_delete_via_http(uvicorn_server):
    tid = _http_post_json("/api/terminal", {})["terminal_id"]
    code = _http_delete(f"/api/terminal/{tid}")
    assert code == 200
    assert tid not in _TERMINALS


class _SyncStdin:
    """Stdin whose write() is SYNCHRONOUS — the real shape of both the ConPTY
    _HandleStream and the asyncio StreamWriter. Awaiting it (the old bug) would
    raise `TypeError: NoneType can't be awaited`."""

    def __init__(self) -> None:
        self.received = bytearray()

    def write(self, data: bytes) -> None:  # noqa: D401 - sync on purpose
        self.received.extend(data)

    async def drain(self) -> None:
        return None


class _SyncStdinProc:
    def __init__(self) -> None:
        self.stdin = _SyncStdin()
        self.stdout = _FakeStdout(_FakeProc())
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.returncode = 1


def test_write_does_not_await_a_synchronous_stdin() -> None:
    """Regression: typing into the phone terminal 404'd because write() awaited
    the shell's synchronous stdin.write. It must deliver the bytes instead."""
    from remedy.interfaces.routes.terminal import _TerminalSession

    proc = _SyncStdinProc()
    sess = _TerminalSession(proc, cwd=None)
    asyncio.run(sess.write("echo hi\n"))
    assert bytes(proc.stdin.received) == b"echo hi\n"
