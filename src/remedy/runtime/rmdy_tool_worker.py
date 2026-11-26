"""RMDY-framed Python tool worker for remedy-runtime.

Reads Remedy protocol frames (magic ``RMDY``) from a binary stream, answers
``KindHealth`` and ``KindToolRequest``, and writes ``KindToolResult`` responses.
Go owns the Tool ABI registry; this process only executes RuntimePython tools
over the versioned wire payloads defined in ``native/go/tools/frame_payload.go``.

Stdio mode (default)::

    python -m remedy.runtime.rmdy_tool_worker

IPC mode (supervised by ``remedy-runtime --serve``)::

    REMEDY_RMDY_ENDPOINT=\\\\.\\pipe\\remedy-tools-… python -m remedy.runtime.rmdy_tool_worker
    REMEDY_RMDY_ENDPOINT=/tmp/remedy-tools-….sock python -m remedy.runtime.rmdy_tool_worker

The worker never logs to stdout (that is the wire). Tracebacks go to stderr
and to ``<REMEDY_HOME>/logs/rmdy_worker.log`` (rotating, 2 MB).

Concurrency
-----------
``KindToolRequest`` frames run on a thread pool (``_MAX_WORKERS``); responses
are written under one lock. Tools whose id starts with a ``_SERIAL_PREFIXES``
entry share the cached ``BasicRuntime`` (not thread-safe) and therefore run one
at a time under ``_serial_lock``. ``KindCancel`` marks the correlation id so its
late response is dropped instead of written.

Go-bound fields
---------------
``home_dir``, ``workspace_root`` and ``project_path`` in a tool input are only
honoured when the Go side vouches for them: either the request envelope carries
``"_go_bound": true`` (top-level, next to ``tool_id``) or the input object
itself carries ``"_go_bound": true``. Go's injection helper
(``httpapi.injectWorkspaceRoot`` and friends) sets the input-level field after
it has overwritten those paths with the session root; it must first delete any
model-supplied ``_go_bound``. Without the flag the worker strips those keys and
falls back to environment / config resolution, so a prompt-injected
``workspace_root`` can never retarget the jail.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO, cast

logger = logging.getLogger("remedy.runtime.rmdy_tool_worker")

_PROTOCOL_VERSION = 1
_HEADER_SIZE = 32
_MAX_PAYLOAD = 16 << 20
_MAGIC = b"RMDY"
_MAX_WORKERS = 8
_LOG_MAX_BYTES = 2 * 1024 * 1024
_LOG_BACKUPS = 3

# Tools sharing the cached BasicRuntime run one at a time.
_SERIAL_PREFIXES = ("prompt.", "memory.", "skill.", "voice.", "vision.", "partner.", "house_rules", "setup.")
_serial_lock = threading.Lock()

# Envelope / input field the Go side sets after binding paths (see module doc).
GO_BOUND_FIELD = "_go_bound"
# Input keys ignored unless the request is Go-bound.
_GO_BOUND_ONLY_KEYS = ("home_dir", "workspace_root", "project_path")

# Packaged installs ship the third-party closure (pydantic, PyYAML, …) in a
# directory beside the zipapp because pydantic_core and _yaml are compiled
# extensions that cannot be imported from inside an archive. The Go launcher
# passes that directory here; scripts/build_rmdy_worker.py stages it.
_ENV_DEPS_DIR = "REMEDY_RMDY_DEPS"
# Third-party modules every RMDY handler chain needs (prompt.assemble pulls
# remedy.interfaces.config and remedy.models). Kept in step with
# DEPS_IMPORT_NAMES in scripts/build_rmdy_worker.py.
_REQUIRED_THIRD_PARTY = ("yaml", "pydantic", "pydantic_core")
# Distinct exit code the Go supervisor reports as a broken install
# (sysexits.h EX_CONFIG). Must match workers.ExitMissingDependencies.
EXIT_MISSING_DEPENDENCIES = 78

# Hidden test-only tools (never advertised) — enabled by REMEDY_RMDY_TEST_TOOLS=1.
_ENV_TEST_TOOLS = "REMEDY_RMDY_TEST_TOOLS"

_KIND_TOOL_REQUEST = 1
_KIND_TOOL_RESULT = 2
_KIND_CANCEL = 6
_KIND_HEALTH = 7

ToolHandler = Callable[[Mapping[str, Any]], Mapping[str, Any]]










































def _prompt_turn_end(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.core.production_learn import finish_production_turn

    return finish_production_turn(inp)






def _voice_speak(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_speak

    return voice_speak(inp)


def _voice_transcribe(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_transcribe

    return voice_transcribe(inp)


def _voice_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import voice_install

    return voice_install(inp)


def _vision_activate(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_activate

    return vision_activate(inp)


def _vision_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_install

    return vision_install(inp)


def _vision_cancel_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_cancel_install

    return vision_cancel_install(inp)


def _vision_reinstall_runtime(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_reinstall_runtime

    return vision_reinstall_runtime(inp)


def _vision_uninstall(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_uninstall

    return vision_uninstall(inp)


def _vision_start(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_start

    return vision_start(inp)


def _vision_stop(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_stop

    return vision_stop(inp)


def _vision_progress(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.runtime.voice_vision_rmdy import vision_progress

    return vision_progress(inp)










































def _worker_probe(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Go's attach probe: import what the live tools need, run nothing.

    A packaged worker missing a dependency fails here at startup, with the
    traceback in the worker log, instead of on the first chat turn.
    """
    _ = inp
    import remedy.core.production_learn  # noqa: F401
    import remedy.runtime.voice_vision_rmdy  # noqa: F401

    return {"ok": True}


_HANDLERS: dict[tuple[str, int], ToolHandler] = {
    ("worker.probe", 1): _worker_probe,
    ("prompt.turn_end", 1): _prompt_turn_end,
    ("voice.speak", 1): _voice_speak,
    ("voice.transcribe", 1): _voice_transcribe,
    ("voice.install", 1): _voice_install,
    ("vision.activate", 1): _vision_activate,
    ("vision.install", 1): _vision_install,
    ("vision.cancel_install", 1): _vision_cancel_install,
    ("vision.reinstall_runtime", 1): _vision_reinstall_runtime,
    ("vision.uninstall", 1): _vision_uninstall,
    ("vision.start", 1): _vision_start,
    ("vision.stop", 1): _vision_stop,
    ("vision.progress", 1): _vision_progress,
}


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("rmdy stream closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: BinaryIO) -> tuple[int, int, bytes, bytes]:
    header = _read_exact(stream, _HEADER_SIZE)
    if header[:4] != _MAGIC:
        raise ValueError("invalid RMDY magic")
    version, kind, flags, payload_len = struct.unpack_from("<HHII", header, 4)
    if version != _PROTOCOL_VERSION:
        raise ValueError(f"unsupported RMDY version {version}")
    if kind == 0:
        raise ValueError("invalid RMDY kind")
    if payload_len > _MAX_PAYLOAD:
        raise ValueError("RMDY payload too large")
    correlation = header[16:32]
    payload = _read_exact(stream, payload_len) if payload_len else b""
    return kind, flags, correlation, payload


def write_frame(
    stream: BinaryIO, kind: int, correlation: bytes, payload: bytes, *, flags: int = 0
) -> None:
    if len(correlation) != 16:
        raise ValueError("correlation id must be 16 bytes")
    if len(payload) > _MAX_PAYLOAD:
        raise ValueError("RMDY payload too large")
    header = (
        _MAGIC + struct.pack("<HHII", _PROTOCOL_VERSION, kind, flags, len(payload)) + correlation
    )
    stream.write(header + payload)
    stream.flush()


def _debug_sleep(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: block the calling pool thread for ``seconds`` (max 30)."""
    try:
        seconds = float(inp.get("seconds") or 0.0)
    except (TypeError, ValueError):
        seconds = 0.0
    seconds = max(0.0, min(30.0, seconds))
    time.sleep(seconds)
    return {"slept": seconds}


def _debug_exit(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: raise SystemExit inside a tool (must never stop the worker)."""
    raise SystemExit(int(inp.get("code") or 3))


def _debug_big(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Test-only: return ``chars`` bytes of ASCII (exercise the frame limit)."""
    try:
        chars = int(inp.get("chars") or 0)
    except (TypeError, ValueError):
        chars = 0
    return {"blob": "x" * max(0, chars)}


_TEST_HANDLERS: dict[tuple[str, int], ToolHandler] = {
    ("debug.sleep", 1): _debug_sleep,
    ("debug.exit", 1): _debug_exit,
    ("debug.big", 1): _debug_big,
}


def _test_tools_enabled() -> bool:
    return (os.environ.get(_ENV_TEST_TOOLS) or "").strip().lower() in {"1", "true", "yes"}


def _lookup_handler(tool_id: str, version: int) -> ToolHandler | None:
    handler = _HANDLERS.get((tool_id, version))
    if handler is None and _test_tools_enabled():
        handler = _TEST_HANDLERS.get((tool_id, version))
    return handler


def _is_serial_tool(tool_id: str) -> bool:
    return tool_id.startswith(_SERIAL_PREFIXES)


def _error_payload(message: str, **extra: Any) -> bytes:
    body: dict[str, Any] = {"ok": False, "error": message}
    body.update(extra)
    return json.dumps(body, separators=(",", ":"), default=str).encode("utf-8")


def _encode_result(output: Any) -> bytes:
    """Serialize a tool result; oversize results become an error payload."""
    encoded = json.dumps({"ok": True, "output": output}, separators=(",", ":"), default=str).encode(
        "utf-8"
    )
    if len(encoded) > _MAX_PAYLOAD:
        return _error_payload("result exceeds 16 MiB frame limit", size=len(encoded))
    return encoded


def _is_go_bound(request: Mapping[str, Any], raw_input: Mapping[str, Any]) -> bool:
    return bool(request.get(GO_BOUND_FIELD)) or bool(raw_input.get(GO_BOUND_FIELD))


def _sanitize_input(request: Mapping[str, Any], raw_input: Mapping[str, Any]) -> dict[str, Any]:
    """Drop the marker field and, unless Go-bound, the path-binding keys."""
    bound = _is_go_bound(request, raw_input)
    cleaned: dict[str, Any] = {}
    for key, value in raw_input.items():
        if key == GO_BOUND_FIELD:
            continue
        if not bound and key in _GO_BOUND_ONLY_KEYS:
            continue
        cleaned[key] = value
    return cleaned


def _handle_tool(payload: bytes) -> bytes:
    """Run one KindToolRequest. Never raises; the wire always gets a payload."""
    try:
        request = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _error_payload(f"invalid tool request: {exc}")
    if not isinstance(request, Mapping):
        return _error_payload("tool request must be a JSON object")
    tool_id = str(request.get("tool_id") or "")
    try:
        version = int(request.get("version") or 0)
    except (TypeError, ValueError):
        return _error_payload(f"invalid tool version {request.get('version')!r}")
    raw_input = request.get("input", {})
    if isinstance(raw_input, str):
        try:
            raw_input = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return _error_payload(f"invalid input: {exc}")
    if raw_input is None:
        raw_input = {}
    if not isinstance(raw_input, Mapping):
        return _error_payload("input must be a JSON object")
    handler = _lookup_handler(tool_id, version)
    if handler is None:
        return _error_payload(f"unknown tool {tool_id}@{version}")
    tool_input = _sanitize_input(request, raw_input)
    try:
        if _is_serial_tool(tool_id):
            with _serial_lock:
                output = handler(tool_input)
        else:
            output = handler(tool_input)
        return _encode_result(output)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:  # noqa: BLE001 — SystemExit et al. must not stop the worker
        logger.exception("tool %s@%s failed", tool_id, version)
        message = str(exc) or exc.__class__.__name__
        if isinstance(exc, SystemExit):
            message = f"tool raised SystemExit({exc.code!r})"
        return _error_payload(message)


def _health_payload() -> bytes:
    return json.dumps(
        {
            "protocol": _PROTOCOL_VERSION,
            "ready": True,
            "capabilities": ["tools", "speech", "vision"],
        },
        separators=(",", ":"),
    ).encode("utf-8")


class _FrameWriter:
    """Serializes frame writes from the pool onto one stream."""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._lock = threading.Lock()
        self.broken = False

    def send(self, kind: int, correlation: bytes, payload: bytes, *, flags: int = 0) -> bool:
        with self._lock:
            if self.broken:
                return False
            try:
                write_frame(self._stream, kind, correlation, payload, flags=flags)
                return True
            except (OSError, ValueError):
                self.broken = True
                logger.exception("rmdy frame write failed; peer gone")
                return False


class _Dispatcher:
    """Thread-pool tool dispatch with KindCancel suppression."""

    def __init__(self, writer: _FrameWriter, max_workers: int = _MAX_WORKERS) -> None:
        self._writer = writer
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="rmdy-tool")
        self._lock = threading.Lock()
        self._inflight: dict[bytes, Future[None]] = {}
        self._cancelled: set[bytes] = set()

    def submit(self, correlation: bytes, payload: bytes) -> None:
        with self._lock:
            if correlation in self._inflight:
                self._writer.send(
                    _KIND_TOOL_RESULT,
                    correlation,
                    _error_payload("duplicate active correlation ID"),
                )
                return
            future = self._pool.submit(self._run, correlation, payload)
            self._inflight[correlation] = future

    def cancel(self, correlation: bytes) -> None:
        with self._lock:
            if correlation in self._inflight:
                self._cancelled.add(correlation)

    def _run(self, correlation: bytes, payload: bytes) -> None:
        try:
            response = _handle_tool(payload)
        except BaseException:  # noqa: BLE001 — a pool thread must never die silently
            logger.exception("rmdy dispatcher failure")
            response = _error_payload("internal worker error")
        with self._lock:
            self._inflight.pop(correlation, None)
            suppressed = correlation in self._cancelled
            self._cancelled.discard(correlation)
        if suppressed:
            logger.info("dropping late response for cancelled request %s", correlation.hex())
            return
        self._writer.send(_KIND_TOOL_RESULT, correlation, response)

    def shutdown(self) -> None:
        # Wait for tools already submitted. Cancelling pending futures would
        # drop work the peer already sent if EOF arrives before a pool thread
        # picks it up (the system-exit-then-next-tool test, and a real
        # short-lived attach).
        self._pool.shutdown(wait=True, cancel_futures=False)


def serve(reader: BinaryIO, writer: BinaryIO) -> None:
    """Serve frames until EOF on *reader*.

    Tool failures of any kind are answered on the wire and never end the
    loop; only stream EOF (or an unrecoverable protocol desync, which is
    logged) returns.
    """
    out = _FrameWriter(writer)
    dispatcher = _Dispatcher(out)
    try:
        while True:
            try:
                kind, _flags, correlation, payload = read_frame(reader)
            except EOFError:
                return
            except ValueError:
                logger.exception("rmdy protocol desync; closing stream")
                return
            except OSError:
                logger.exception("rmdy stream read failed")
                return
            if kind == _KIND_HEALTH:
                out.send(_KIND_HEALTH, correlation, _health_payload())
                continue
            if kind == _KIND_TOOL_REQUEST:
                dispatcher.submit(correlation, payload)
                continue
            if kind == _KIND_CANCEL:
                dispatcher.cancel(correlation)
                continue
            err = f"unsupported RMDY frame kind {kind}".encode()
            out.send(_KIND_TOOL_RESULT, correlation, err, flags=1)
    finally:
        dispatcher.shutdown()


def _log_dir() -> Path | None:
    try:
        from remedy.home import default_home

        home = default_home()
    except Exception:  # noqa: BLE001 — logging must never block startup
        raw = (os.environ.get("REMEDY_HOME") or "").strip()
        if not raw:
            return None
        home = Path(raw).expanduser()
    return home / "logs"


def bootstrap_dependency_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Put the packaged dependency directory last on ``sys.path``.

    Packaged installs run a managed CPython with only the standard library, so
    the launcher points ``REMEDY_RMDY_DEPS`` at the staged closure. Appending
    (never inserting) keeps a developer venv authoritative: the deps directory
    is purely additive and only answers imports nothing else provides.

    Runs before anything imports ``remedy.*``, so it must not log — the caller
    reports a configured-but-missing directory once logging is up.
    """
    source = env if env is not None else os.environ
    raw = (source.get(_ENV_DEPS_DIR) or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir():
        return None
    entry = str(path)
    if entry not in sys.path:
        sys.path.append(entry)
    return path


def verify_dependencies() -> None:
    """Fail fast and legibly when the third-party closure is not importable.

    Without this the first ``prompt.assemble`` call — the Go attach probe —
    dies deep inside ``remedy.interfaces.config`` and the operator sees a tool
    failure rather than a broken install.
    """
    import importlib

    for name in _REQUIRED_THIRD_PARTY:
        try:
            importlib.import_module(name)
        except Exception:
            deps = (os.environ.get(_ENV_DEPS_DIR) or "").strip() or "<unset>"
            logger.exception(
                "rmdy worker cannot import required dependency %r "
                "(%s=%s, executable=%s); the install is incomplete",
                name,
                _ENV_DEPS_DIR,
                deps,
                sys.executable,
            )
            logging.shutdown()
            raise SystemExit(EXIT_MISSING_DEPENDENCIES) from None


def configure_logging() -> Path | None:
    """stderr + rotating ``<REMEDY_HOME>/logs/rmdy_worker.log``; returns the log path."""
    from logging.handlers import RotatingFileHandler

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not any(getattr(h, "_rmdy_stderr", False) for h in root.handlers):
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(fmt)
        stderr_handler._rmdy_stderr = True  # type: ignore[attr-defined]
        root.addHandler(stderr_handler)
    log_dir = _log_dir()
    if log_dir is None:
        return None
    path = log_dir / "rmdy_worker.log"
    if any(getattr(h, "_rmdy_file", None) == str(path) for h in root.handlers):
        return path
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUPS, encoding="utf-8"
        )
    except OSError:
        logger.warning("cannot open worker log at %s", path)
        return None
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(fmt)
    file_handler._rmdy_file = str(path)  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    return path


_ERROR_IO_PENDING = 997
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_OPERATION_ABORTED = 995
_FILE_FLAG_OVERLAPPED = 0x40000000
_win32_lock = threading.Lock()
_win32_cache: dict[str, Any] = {}


def _win32_last_error() -> int:
    """Win32 GetLastError via ctypes.

    Declared through ``getattr`` because ``ctypes.get_last_error`` is absent
    from the non-Windows typeshed stubs the repo type-checks against.
    """
    import ctypes

    getter = getattr(ctypes, "get_last_error", None)
    if getter is None:
        return 0
    return int(getter())


def _win32_kernel32() -> Any:
    """kernel32 with ``use_last_error`` — without assuming Linux stubs have WinDLL."""
    with _win32_lock:
        cached = _win32_cache.get("kernel32")
        if cached is not None:
            return cached
        import ctypes

        win_dll = getattr(ctypes, "WinDLL", None)
        if win_dll is None:
            raise OSError("ctypes.WinDLL is only available on Windows")
        kernel32 = win_dll("kernel32", use_last_error=True)
        # Explicit HANDLE prototypes: a bare Python int would be narrowed to a
        # C int and 64-bit handle values could be truncated.
        from ctypes import wintypes

        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        for fn in (kernel32.ReadFile, kernel32.WriteFile):
            fn.restype = wintypes.BOOL
            fn.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
        kernel32.GetOverlappedResult.restype = wintypes.BOOL
        kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _win32_cache["kernel32"] = kernel32
        return kernel32


def _win32_overlapped_type() -> Any:
    with _win32_lock:
        cached = _win32_cache.get("OVERLAPPED")
        if cached is not None:
            return cached
        import ctypes
        from ctypes import wintypes

        class _Overlapped(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_void_p),
                ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD),
                ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            ]

        _win32_cache["OVERLAPPED"] = _Overlapped
        return _Overlapped


class _PipeFile:
    """Binary file-like over a Windows named-pipe HANDLE (CreateFileW).

    The handle is opened with ``FILE_FLAG_OVERLAPPED`` and every operation
    carries its own OVERLAPPED + event. A synchronous pipe handle serializes
    I/O per file object, so a pool thread's WriteFile would block behind the
    reader thread's pending ReadFile and the worker would deadlock waiting
    for a request that Go only sends after our response.
    """

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False

    def _overlapped_io(self, op: str, buf: Any, size: int) -> int:
        """Run ReadFile/WriteFile with a private OVERLAPPED; returns bytes moved."""
        import ctypes
        from ctypes import wintypes

        kernel32 = _win32_kernel32()
        overlapped_type = _win32_overlapped_type()
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise OSError(f"CreateEventW failed: Win32 {_win32_last_error()}")
        try:
            ov = overlapped_type()
            ov.hEvent = event
            moved = wintypes.DWORD(0)
            fn = kernel32.ReadFile if op == "read" else kernel32.WriteFile
            ok = fn(self._handle, buf, size, ctypes.byref(moved), ctypes.byref(ov))
            if not ok:
                err = _win32_last_error()
                if err in (_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED):
                    return 0
                if err != _ERROR_IO_PENDING:
                    raise OSError(f"{op} failed on named pipe: Win32 {err}")
            done = wintypes.DWORD(0)
            if not kernel32.GetOverlappedResult(
                self._handle, ctypes.byref(ov), ctypes.byref(done), True
            ):
                err = _win32_last_error()
                if err in (
                    _ERROR_BROKEN_PIPE,
                    _ERROR_PIPE_NOT_CONNECTED,
                    _ERROR_OPERATION_ABORTED,
                ):
                    return 0
                raise OSError(f"{op} failed on named pipe: Win32 {err}")
            return int(done.value)
        finally:
            kernel32.CloseHandle(event)

    def read(self, size: int = -1) -> bytes:
        if self._closed or size == 0:
            return b""
        import ctypes

        to_read = 65536 if size < 0 else min(65536, size)
        buf = (ctypes.c_char * to_read)()
        got = self._overlapped_io("read", buf, to_read)
        if got <= 0:
            return b""
        return buf.raw[:got]

    def write(self, data: bytes) -> int:
        if self._closed:
            return 0
        import ctypes

        written_total = 0
        while written_total < len(data):
            chunk = data[written_total:]
            buf = (ctypes.c_char * len(chunk)).from_buffer_copy(chunk)
            written = self._overlapped_io("write", buf, len(chunk))
            if written <= 0:
                raise OSError("WriteFile on named pipe wrote nothing (peer closed)")
            written_total += written
        return written_total

    def flush(self) -> None:
        # Byte-mode pipe writes are visible to the peer once WriteFile
        # completes; FlushFileBuffers would block until Go drained the pipe.
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        _win32_kernel32().CloseHandle(self._handle)


def _dial_windows_pipe(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    import ctypes

    kernel32 = _win32_kernel32()
    generic_read = 0x80000000
    generic_write = 0x40000000
    open_existing = 3
    invalid_handle = ctypes.c_void_p(-1).value
    handle = kernel32.CreateFileW(
        endpoint,
        generic_read | generic_write,
        0,
        None,
        open_existing,
        _FILE_FLAG_OVERLAPPED,
        None,
    )
    if handle in (None, 0, invalid_handle, -1):
        raise OSError(f"CreateFileW({endpoint!r}) failed: Win32 {_win32_last_error()}")
    pipe = _PipeFile(int(handle))
    stream = cast(BinaryIO, pipe)
    return stream, stream, pipe.close


def _dial_unix(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    af_unix = getattr(socket, "AF_UNIX", None)
    if af_unix is None:
        raise OSError("AF_UNIX sockets are not available on this platform")
    sock = socket.socket(af_unix, socket.SOCK_STREAM)
    sock.connect(endpoint)
    reader = cast(BinaryIO, sock.makefile("rb", buffering=0))
    writer = cast(BinaryIO, sock.makefile("wb", buffering=0))

    def _close() -> None:
        try:
            reader.close()
        finally:
            try:
                writer.close()
            finally:
                sock.close()

    return reader, writer, _close


def dial_endpoint(endpoint: str) -> tuple[BinaryIO, BinaryIO, Callable[[], None]]:
    endpoint = endpoint.strip()
    if not endpoint:
        raise ValueError("empty RMDY endpoint")
    if endpoint.startswith("\\\\.\\pipe\\") or endpoint.startswith("//./pipe/"):
        # Normalize forward-slash form if a shell mangled it.
        normalized = endpoint.replace("/", "\\")
        return _dial_windows_pipe(normalized)
    return _dial_unix(endpoint)


def serve_endpoint(endpoint: str) -> None:
    last_err: Exception | None = None
    for _ in range(50):
        try:
            reader, writer, closer = dial_endpoint(endpoint)
        except OSError as exc:
            last_err = exc
            time.sleep(0.1)
            continue
        logger.info("rmdy worker attached to %s (pid=%d)", endpoint, os.getpid())
        try:
            serve(reader, writer)
        finally:
            closer()
        logger.info("rmdy worker stream closed; exiting")
        return
    raise SystemExit(f"failed to dial RMDY endpoint {endpoint!r}: {last_err}")


def _start_local_search() -> None:
    """Bring up the managed OpenSERP in the background (docs/WEB_ETIQUETTE.md).

    The Go runtime hosts web.search in this worker, so this is where the
    first-run download + start belongs. Without it every search fell through
    to DuckDuckGo's HTML page, which answers automated reads with a human
    check. Never blocks the worker; failures only mean the fallback is used.
    """
    try:
        from remedy.core.web_helpers import web_tools_enabled
        from remedy.home import default_home
        from remedy.runtime.web_search_host import schedule_ensure

        schedule_ensure(default_home(), enabled=web_tools_enabled(None))
    except Exception:
        logger.debug("local search host not scheduled", exc_info=True)


def _start_voice_evolution() -> None:
    """Start daily voice drift once the worker is up. Local-only; fail-safe."""
    try:
        from remedy.voice.service import start_voice_evolution

        start_voice_evolution()
    except Exception:
        logger.debug("voice evolution not started", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="remedy.runtime.rmdy_tool_worker")
    parser.add_argument(
        "--endpoint",
        default="",
        help="IPC endpoint (named pipe / unix socket); default REMEDY_RMDY_ENDPOINT or stdio",
    )
    args = parser.parse_args(argv)
    # sys.path first: nothing may import remedy.* before the packaged
    # dependency directory is in place. Logging follows, then the preflight.
    deps_dir = bootstrap_dependency_path()
    configure_logging()
    configured = (os.environ.get(_ENV_DEPS_DIR) or "").strip()
    if configured and deps_dir is None:
        logger.warning("%s points at a missing directory: %s", _ENV_DEPS_DIR, configured)
    verify_dependencies()
    _start_voice_evolution()
    _start_local_search()
    endpoint = (args.endpoint or os.environ.get("REMEDY_RMDY_ENDPOINT") or "").strip()
    if endpoint:
        serve_endpoint(endpoint)
        return 0
    # Windows stdio is text by default; reopen as binary for RMDY frames.
    serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
