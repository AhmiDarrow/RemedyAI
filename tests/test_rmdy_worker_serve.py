"""Worker robustness, concurrency, cancel, Go-bound gating and tool semantics.

Every step here is self-bounding: this file has no external watchdog, so a
blocking read, wait or serve loop must fail the test rather than wedge the
suite, and every worker subprocess is killed (tree and all) on every path.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from remedy.runtime import rmdy_tool_worker as worker

REPO = Path(__file__).resolve().parent.parent

# Generous enough for a loaded CI box, short enough that a wedged step is a
# red test in seconds rather than a hung job.
_STEP_TIMEOUT = 60.0
_EXIT_TIMEOUT = 30.0


def _bounded[T](step: str, call: Callable[[], T], timeout: float = _STEP_TIMEOUT) -> T:
    """Run *call* on a helper thread and fail the test if it does not finish.

    Windows named-pipe reads and ``proc.wait`` cannot always be bounded from
    Python, so the join timeout is the watchdog. The thread is a daemon: once
    the test fails, teardown closes the pipe / kills the worker and it unwinds.
    """
    done: list[T] = []
    failed: list[BaseException] = []

    def _run() -> None:
        try:
            done.append(call())
        except BaseException as exc:  # noqa: BLE001 — re-raised on the test thread
            failed.append(exc)

    thread = threading.Thread(target=_run, name=f"bounded-{step}", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        pytest.fail(f"{step} did not finish within {timeout:.0f}s")
    if failed:
        raise failed[0]
    return done[0]


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill the worker and any child it spawned; never leave one behind."""
    if proc.poll() is None:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                check=False,
                capture_output=True,
            )
        else:
            proc.kill()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(OSError, ValueError):
                stream.close()
    _bounded("worker teardown", lambda: proc.wait(timeout=_EXIT_TIMEOUT), _EXIT_TIMEOUT + 5)


@contextlib.contextmanager
def _worker_process(
    args: list[str], env: dict[str, str], **popen: Any
) -> Iterator[subprocess.Popen[bytes]]:
    """Spawn the worker and guarantee it is gone when the block exits."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "remedy.runtime.rmdy_tool_worker", *args],
        env=env,
        **popen,
    )
    try:
        yield proc
    finally:
        _kill_tree(proc)
        assert proc.poll() is not None, "worker subprocess outlived the test"


def _frame(kind: int, payload: bytes, corr: bytes) -> bytes:
    return b"RMDY" + struct.pack("<HHII", 1, kind, 0, len(payload)) + corr + payload


def _corr(n: int) -> bytes:
    return bytes([n]) + b"\x00" * 15


def _tool_frame(corr: bytes, tool_id: str, inp: dict[str, Any], **envelope: Any) -> bytes:
    body = {"tool_id": tool_id, "version": 1, "input": inp, **envelope}
    return _frame(worker._KIND_TOOL_REQUEST, json.dumps(body).encode("utf-8"), corr)


def _parse_frames(raw: bytes) -> list[tuple[int, bytes, dict[str, Any] | bytes]]:
    out: list[tuple[int, bytes, dict[str, Any] | bytes]] = []
    pos = 0
    while pos + 32 <= len(raw):
        assert raw[pos : pos + 4] == b"RMDY"
        _ver, kind, flags, plen = struct.unpack_from("<HHII", raw, pos + 4)
        corr = raw[pos + 16 : pos + 32]
        payload = raw[pos + 32 : pos + 32 + plen]
        pos += 32 + plen
        if flags & 1:
            out.append((kind, corr, payload))
        else:
            out.append((kind, corr, json.loads(payload.decode("utf-8"))))
    return out


class _Duplex:
    """In-memory reader over *inbound* frames; collects written bytes."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._lock = threading.Lock()
        self.out = bytearray()

    def read(self, n: int) -> bytes:
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def write(self, data: bytes) -> int:
        with self._lock:
            self.out.extend(data)
        return len(data)

    def flush(self) -> None:
        return None


def _serve(inbound: bytes) -> list[tuple[int, bytes, Any]]:
    buf = _Duplex(inbound)
    # serve() ends at EOF; bound it anyway so a stuck tool fails the test.
    _bounded("serve", lambda: worker.serve(buf, buf))  # type: ignore[arg-type]
    return _parse_frames(bytes(buf.out))


@pytest.fixture()
def test_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(worker._ENV_TEST_TOOLS, "1")


# --- robustness ---------------------------------------------------------------


def test_system_exit_in_tool_yields_error_frame_and_worker_keeps_serving(test_tools: None) -> None:
    frames = _serve(
        _tool_frame(_corr(1), "debug.exit", {"code": 7})
        + _tool_frame(_corr(2), "text.slugify", {"text": "Still Alive"})
    )
    by_corr = {c: body for _k, c, body in frames}
    assert by_corr[_corr(1)]["ok"] is False
    assert "SystemExit" in by_corr[_corr(1)]["error"]
    assert by_corr[_corr(2)] == {"ok": True, "output": {"slug": "still-alive"}}


def test_result_over_frame_limit_becomes_error_not_crash(test_tools: None) -> None:
    frames = _serve(
        _tool_frame(_corr(1), "debug.big", {"chars": worker._MAX_PAYLOAD + 1024})
        + _tool_frame(_corr(2), "text.word_count", {"text": "one two"})
    )
    by_corr = {c: body for _k, c, body in frames}
    big = by_corr[_corr(1)]
    assert big["ok"] is False
    assert "16 MiB" in big["error"]
    assert big["size"] > worker._MAX_PAYLOAD
    assert by_corr[_corr(2)]["output"] == {"words": 2}


def test_unserializable_output_uses_default_str(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(worker._HANDLERS, ("debug.path", 1), lambda inp: {"p": Path("x/y")})
    frames = _serve(_tool_frame(_corr(1), "debug.path", {}))
    assert frames[0][2]["ok"] is True
    assert frames[0][2]["output"]["p"].replace("\\", "/") == "x/y"


def test_non_integer_version_is_an_error_frame() -> None:
    body = {"tool_id": "text.slugify", "version": "one", "input": {"text": "x"}}
    raw = _frame(worker._KIND_TOOL_REQUEST, json.dumps(body).encode(), _corr(1))
    frames = _serve(raw)
    assert frames[0][2]["ok"] is False
    assert "version" in frames[0][2]["error"]


def test_health_and_unknown_kind_still_answered() -> None:
    frames = _serve(_frame(worker._KIND_HEALTH, b"", _corr(1)) + _frame(5, b"", _corr(2)))
    kinds = {c: (k, body) for k, c, body in frames}
    assert kinds[_corr(1)][0] == worker._KIND_HEALTH
    assert kinds[_corr(1)][1]["ready"] is True
    assert kinds[_corr(2)][0] == worker._KIND_TOOL_RESULT
    assert b"unsupported" in kinds[_corr(2)][1]


def test_cancel_suppresses_late_response(test_tools: None) -> None:
    frames = _serve(
        _tool_frame(_corr(1), "debug.sleep", {"seconds": 0.6})
        + _frame(worker._KIND_CANCEL, b"", _corr(1))
        + _tool_frame(_corr(2), "text.slugify", {"text": "After Cancel"})
    )
    corrs = [c for _k, c, _b in frames]
    assert _corr(1) not in corrs
    assert _corr(2) in corrs


def test_serial_tools_hold_the_shared_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def _probe(inp: Any) -> dict[str, bool]:
        seen.append(worker._serial_lock.locked())
        return {"ok": True}

    monkeypatch.setitem(worker._HANDLERS, ("prompt.probe", 1), _probe)
    monkeypatch.setitem(worker._HANDLERS, ("text.probe", 1), _probe)
    # One frame per serve: dispatching both together lets the pool overlap them,
    # and a handler sampling a process-wide lock would then see the other
    # handler's state rather than its own. Each call asserts its own invariant.
    _serve(_tool_frame(_corr(1), "prompt.probe", {}))
    assert seen == [True], "a prompt.* handler must hold the shared serial lock"
    seen.clear()
    _serve(_tool_frame(_corr(2), "text.probe", {}))
    assert seen == [False], "an unrelated tool must not take the serial lock"


def test_logging_writes_rotating_file_under_remedy_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import logging

    home = tmp_path / "home"
    monkeypatch.setenv("REMEDY_HOME", str(home))
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = worker.configure_logging()
        assert path == home / "logs" / "rmdy_worker.log"
        handler = next(h for h in root.handlers if getattr(h, "_rmdy_file", None) == str(path))
        assert handler.maxBytes == 2 * 1024 * 1024  # type: ignore[attr-defined]
        worker.logger.error("probe line")
        handler.flush()
        assert "probe line" in path.read_text(encoding="utf-8")
    finally:
        for h in root.handlers:
            if h not in before:
                root.removeHandler(h)
                h.close()


# --- Go-bound gating ------------------------------------------------------------


def test_workspace_root_ignored_unless_go_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    default_ws = tmp_path / "default"
    other = tmp_path / "other"
    default_ws.mkdir()
    other.mkdir()
    (default_ws / "a.txt").write_text("default\n", encoding="utf-8")
    (other / "a.txt").write_text("other\n", encoding="utf-8")
    monkeypatch.setenv("REMEDY_WORKSPACE", str(default_ws))

    model_supplied = _serve(
        _tool_frame(_corr(1), "workspace.read", {"path": "a.txt", "workspace_root": str(other)})
    )
    assert model_supplied[0][2]["output"]["content"] == "default\n"

    envelope_bound = _serve(
        _tool_frame(
            _corr(2),
            "workspace.read",
            {"path": "a.txt", "workspace_root": str(other)},
            _go_bound=True,
        )
    )
    assert envelope_bound[0][2]["output"]["content"] == "other\n"

    input_bound = _serve(
        _tool_frame(
            _corr(3),
            "workspace.read",
            {"path": "a.txt", "workspace_root": str(other), worker.GO_BOUND_FIELD: True},
        )
    )
    assert input_bound[0][2]["output"]["content"] == "other\n"


def test_sanitize_input_strips_marker_and_home_dir() -> None:
    cleaned = worker._sanitize_input(
        {}, {"query": "q", "home_dir": "/x", "project_path": "/p", worker.GO_BOUND_FIELD: False}
    )
    assert cleaned == {"query": "q"}
    bound = worker._sanitize_input({worker.GO_BOUND_FIELD: True}, {"query": "q", "home_dir": "/x"})
    assert bound == {"query": "q", "home_dir": "/x"}


# --- tool semantics ---------------------------------------------------------------


def test_workspace_edit_preserves_crlf_and_bom(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    target = tmp_path / "win.txt"
    original = b"\xef\xbb\xbfline one\r\nreturn 1\r\nline three\r\n"
    target.write_bytes(original)
    out = worker._workspace_edit(
        {"path": "win.txt", "old_string": "return 1", "new_string": "return 2"}
    )
    assert out["changed"] is True
    assert target.read_bytes() == b"\xef\xbb\xbfline one\r\nreturn 2\r\nline three\r\n"

    # Multi-line replacement supplied with LF keeps the file CRLF.
    worker._workspace_edit(
        {
            "path": "win.txt",
            "old_string": "line one\nreturn 2",
            "new_string": "line one\nreturn 3\nextra",
        }
    )
    data = target.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert b"return 3\r\nextra\r\n" in data


def test_workspace_read_offset_is_one_based_with_paging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "n.txt").write_text("".join(f"l{i}\n" for i in range(1, 11)), encoding="utf-8")
    page = worker._workspace_read({"path": "n.txt", "offset": 3, "limit": 4})
    assert page["content"] == "l3\nl4\nl5\nl6\n"
    assert page["total_lines"] == 10
    assert page["line_start"] == 3
    assert page["line_end"] == 6
    assert page["next_offset"] == 7
    assert page["truncated"] is True
    tail = worker._workspace_read({"path": "n.txt", "offset": 7})
    assert tail["content"] == "l7\nl8\nl9\nl10\n"
    assert "next_offset" not in tail
    assert "truncated" not in tail
    numbered = worker._workspace_read({"path": "n.txt", "offset": 9, "line_numbers": True})
    assert numbered["content"] == " 9\tl9\n10\tl10\n"
    top = worker._workspace_read({"path": "n.txt", "offset": 0, "limit": 1})
    assert top["content"] == "l1\n"
    assert top["line_start"] == 1


def test_workspace_search_invalid_regex_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "a.txt").write_text("foo(\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid regex"):
        worker._workspace_search({"pattern": "foo("})


def test_workspace_search_context_and_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMEDY_WORKSPACE", str(tmp_path))
    (tmp_path / "a.txt").write_text(
        "before\nneedle one\nafter\n\nneedle two\nneedle three\n", encoding="utf-8"
    )
    out = worker._workspace_search({"pattern": "needle", "max_matches": 2, "context": 1})
    assert out["total"] == 2
    assert out["capped"] is True
    assert out["truncated"] is True
    first = out["matches"][0]
    assert first["line"] == 2
    assert "before" in first["text"] and "after" in first["text"]
    full = worker._workspace_search({"pattern": "needle", "max_matches": 10})
    assert full["total"] == 3
    assert full["capped"] is False
    assert full["truncated"] is False


def test_repo_search_rg_max_count_is_not_clamped_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A busy file must be allowed to contribute more than 100 matches."""
    from remedy.core import repo_search as rs

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 1
        stdout = ""

    def _fake_run_hidden(cmd, **_kw):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        return _Proc()

    import remedy.execution.process as proc_mod

    monkeypatch.setattr(proc_mod, "run_hidden", _fake_run_hidden)
    hits, ok, capped = rs._search_rg(
        "rg",
        tmp_path,
        tmp_path,
        "x",
        glob=None,
        max_matches=300,
        case_insensitive=False,
        context_before=0,
        context_after=0,
    )
    assert ok and not hits and not capped
    cmd = captured["cmd"]
    assert cmd[cmd.index("--max-count") + 1] == "301"


def test_repo_search_rg_groups_context_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from remedy.core import repo_search as rs

    class _Proc:
        returncode = 0
        stdout = "a.py-1-before\na.py:2:hit\na.py-3-after\n--\na.py:9:second\n"

    import remedy.execution.process as proc_mod

    monkeypatch.setattr(proc_mod, "run_hidden", lambda cmd, **kw: _Proc())
    hits, ok, capped = rs._search_rg(
        "rg",
        tmp_path,
        tmp_path,
        "hit",
        glob=None,
        max_matches=1,
        case_insensitive=False,
        context_before=1,
        context_after=1,
    )
    assert ok and capped
    assert len(hits) == 1
    assert hits[0].line == 2
    assert hits[0].text == "before\nhit\nafter"


# --- real endpoint, real subprocess ---------------------------------------------------


def _worker_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    env["REMEDY_HOME"] = str(tmp_path / "home")
    env["REMEDY_WORKSPACE"] = str(tmp_path)
    env[worker._ENV_TEST_TOOLS] = "1"
    return env


class _EndpointServer:
    """Listener side of the real IPC endpoint (what Go's ipc.Listen does)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.endpoint = ""
        self._sock: socket.socket | None = None
        self._conn: Any = None
        self._pipe: int | None = None

    def __enter__(self) -> _EndpointServer:
        if sys.platform == "win32":
            self.endpoint = rf"\\.\pipe\remedy-tools-test-{os.getpid()}-{time.time_ns()}"
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
            k32.CreateNamedPipeW.restype = ctypes.c_void_p
            pipe_access_duplex = 0x3
            pipe_type_byte = 0x0
            pipe_wait = 0x0
            handle = k32.CreateNamedPipeW(
                self.endpoint,
                pipe_access_duplex,
                pipe_type_byte | pipe_wait,
                1,
                65536,
                65536,
                0,
                None,
            )
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                raise OSError(f"CreateNamedPipeW failed: {ctypes.get_last_error()}")
            self._pipe = int(handle)
            self._k32 = k32
        else:
            self.endpoint = str(self.tmp_path / f"remedy-tools-{os.getpid()}.sock")
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(_STEP_TIMEOUT)
            self._sock.bind(self.endpoint)
            self._sock.listen(1)
        return self

    def accept(self) -> None:
        """Bounded on both platforms: socket timeout / join timeout."""
        if sys.platform == "win32":
            assert self._pipe is not None

            def _connect() -> None:
                error_pipe_connected = 535
                ok = self._k32.ConnectNamedPipe(ctypes.c_void_p(self._pipe), None)
                if not ok and ctypes.get_last_error() != error_pipe_connected:
                    raise OSError(f"ConnectNamedPipe failed: {ctypes.get_last_error()}")

            _bounded("endpoint accept", _connect)
        else:
            assert self._sock is not None
            self._conn, _ = self._sock.accept()
            self._conn.settimeout(_STEP_TIMEOUT)

    def send(self, data: bytes) -> None:
        if sys.platform == "win32":
            assert self._pipe is not None
            from ctypes import wintypes

            written = wintypes.DWORD(0)
            buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
            if not self._k32.WriteFile(
                ctypes.c_void_p(self._pipe), buf, len(data), ctypes.byref(written), None
            ):
                raise OSError(f"WriteFile failed: {ctypes.get_last_error()}")
        else:
            self._conn.sendall(data)

    def recv_exact(self, n: int) -> bytes:
        out = bytearray()
        while len(out) < n:
            if sys.platform == "win32":
                assert self._pipe is not None
                from ctypes import wintypes

                buf = (ctypes.c_char * (n - len(out)))()
                got = wintypes.DWORD(0)
                if not self._k32.ReadFile(
                    ctypes.c_void_p(self._pipe), buf, n - len(out), ctypes.byref(got), None
                ):
                    raise OSError(f"ReadFile failed: {ctypes.get_last_error()}")
                chunk = buf.raw[: got.value]
            else:
                chunk = self._conn.recv(n - len(out))
            if not chunk:
                raise EOFError
            out.extend(chunk)
        return bytes(out)

    def recv_frame(self, what: str = "frame") -> tuple[bytes, dict[str, Any]]:
        """Read one frame, bounded — a silent worker fails the test."""

        def _read() -> tuple[bytes, dict[str, Any]]:
            header = self.recv_exact(32)
            assert header[:4] == b"RMDY"
            _ver, _kind, _flags, plen = struct.unpack_from("<HHII", header, 4)
            payload = self.recv_exact(plen) if plen else b""
            return header[16:32], json.loads(payload.decode("utf-8"))

        return _bounded(f"recv {what}", _read)

    def close(self) -> None:
        """Drop the endpoint; the worker must then exit on its own."""
        if sys.platform == "win32":
            if self._pipe is not None:
                self._k32.CloseHandle(ctypes.c_void_p(self._pipe))
                self._pipe = None
        else:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._sock is not None:
                self._sock.close()
                self._sock = None

    def __exit__(self, *_exc: object) -> None:
        self.close()


def test_real_endpoint_overlapping_requests_fast_returns_first(tmp_path: Path) -> None:
    with _EndpointServer(tmp_path) as server:
        args = ["--endpoint", server.endpoint]
        with _worker_process(
            args,
            _worker_env(tmp_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        ) as proc:
            server.accept()
            server.send(_tool_frame(_corr(1), "debug.sleep", {"seconds": 1.0}))
            time.sleep(0.15)
            server.send(_tool_frame(_corr(2), "text.slugify", {"text": "Fast Lane"}))
            t0 = time.monotonic()
            first_corr, first_body = server.recv_frame("fast result")
            elapsed = time.monotonic() - t0
            second_corr, second_body = server.recv_frame("slow result")
            assert first_corr == _corr(2), "fast request must not wait behind the slow one"
            assert first_body["output"] == {"slug": "fast-lane"}
            assert elapsed < 0.9
            assert second_corr == _corr(1)
            assert second_body["output"]["slept"] == 1.0

            # SystemExit inside a tool: error frame, worker still serving.
            server.send(_tool_frame(_corr(3), "debug.exit", {"code": 2}))
            corr3, body3 = server.recv_frame("SystemExit result")
            assert corr3 == _corr(3) and body3["ok"] is False
            server.send(_frame(worker._KIND_HEALTH, b"", _corr(4)))
            corr4, body4 = server.recv_frame("health")
            assert corr4 == _corr(4) and body4["ready"] is True
            assert proc.poll() is None

            # Dropping the endpoint must end the worker by itself: a leaked
            # worker is exactly what supervision has to prevent.
            server.close()
            _bounded(
                "worker exit after endpoint close",
                lambda: proc.wait(timeout=_EXIT_TIMEOUT),
                _EXIT_TIMEOUT + 5,
            )
            assert proc.poll() is not None
    # Log file was created under the sandboxed home.
    assert (tmp_path / "home" / "logs" / "rmdy_worker.log").is_file()


def test_stdio_subprocess_oversize_result_does_not_kill_worker(tmp_path: Path) -> None:
    with _worker_process(
        [],
        _worker_env(tmp_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as proc:
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(_tool_frame(_corr(1), "debug.big", {"chars": worker._MAX_PAYLOAD + 5}))
        proc.stdin.write(_tool_frame(_corr(2), "text.word_count", {"text": "x y z"}))
        proc.stdin.flush()
        # Read proc.stdout directly: wrapping an already-buffered pipe in a
        # second BufferedReader makes each read demand a full 8 KiB block,
        # which deadlocks against a worker that answers in small frames.
        stdout = proc.stdout
        got: dict[bytes, dict[str, Any]] = {}

        def _read_result() -> None:
            header = stdout.read(32)
            assert len(header) == 32, "worker closed stdout before answering"
            _ver, _kind, _flags, plen = struct.unpack_from("<HHII", header, 4)
            got[header[16:32]] = json.loads(stdout.read(plen).decode("utf-8"))

        _bounded("oversize result", _read_result)
        _bounded("second result", _read_result)
        assert got[_corr(1)]["ok"] is False and "16 MiB" in got[_corr(1)]["error"]
        assert got[_corr(2)]["output"] == {"words": 3}
        proc.stdin.close()
        # Closing the stream ends the worker cleanly — no leftover process.
        code = _bounded(
            "worker exit after stdin close",
            lambda: proc.wait(timeout=_EXIT_TIMEOUT),
            _EXIT_TIMEOUT + 5,
        )
        assert code == 0
