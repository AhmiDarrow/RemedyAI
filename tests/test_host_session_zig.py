"""Phase 3: Zig HostSession protocol + live orchestration parity."""

from __future__ import annotations

import sys

import pytest

from remedy.core.computer import host_binding
from remedy.core.computer.host_binding import HostError
from remedy.execution.host.session import (
    _split_sentinel,
    _wrap_with_sentinel,
)
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WINDOWS, reason="Zig HostSession live I/O is Windows-only")


def _core_available() -> bool:
    try:
        host_binding._lib()
        return True
    except (NativeRuntimeUnavailableError, OSError, AttributeError):
        return False


requires_core = pytest.mark.skipif(not _core_available(), reason="remedy_core not built")


@requires_core
@pytest.mark.parametrize(
    ("host", "command", "sentinel"),
    [
        ("cmd", "echo hello", "REMEDY_HOST_DONE_abc"),
        ("pwsh", "echo hello", "REMEDY_HOST_DONE_abc"),
        ("posix", "echo hello", "REMEDY_HOST_DONE_abc"),
    ],
)
def test_zig_wrap_matches_python(host: str, command: str, sentinel: str) -> None:
    zig = host_binding.host_session_wrap(host=host, command=command, sentinel=sentinel)
    py = _wrap_with_sentinel(host, command, sentinel)
    assert zig == py


@requires_core
@pytest.mark.parametrize(
    ("text", "code", "body"),
    [
        ("hi\r\nREMEDY_HOST_DONE_abc:0\r\n", 0, "hi"),
        ("REMEDY_HOST_DONE_abc:12\r\n", 12, ""),
        ("x\nREMEDY_HOST_DONE_abc:-1\n", -1, "x"),
        ("out\r\nREMEDY_HOST_DONE_abc:\r\n", 0, "out"),
        ("hello\r\nREMEDY_HOST_DONE_abc:0\x1b[5;1H", 0, "hello"),
    ],
)
def test_zig_split_matches_python(text: str, code: int, body: str) -> None:
    zig_code, zig_body = host_binding.host_session_split(
        text=text, sentinel="REMEDY_HOST_DONE_abc"
    )
    py_code, py_body = _split_sentinel(text, "REMEDY_HOST_DONE_abc")
    assert (zig_code, zig_body) == (code, body)
    assert (zig_code, zig_body) == (py_code, py_body)


@requires_core
def test_zig_rejects_unexpanded_echo() -> None:
    code, _body = host_binding.host_session_split(
        text="echo REMEDY_HOST_DONE_abc:%ERRORLEVEL%\r\n",
        sentinel="REMEDY_HOST_DONE_abc",
    )
    assert code == -1


@requires_core
@windows_only
def test_zig_host_session_echo_round_trip() -> None:
    handle = host_binding.host_session_open(host="cmd", use_conpty=False)
    try:
        result = host_binding.host_session_run(
            handle, "echo host-session-ok", timeout_ms=20_000
        )
        assert result.get("timed_out") is False
        assert "host-session-ok" in (result.get("stdout") or "")
    finally:
        host_binding.host_session_close(handle)


@requires_core
@pytest.mark.skipif(WINDOWS, reason="live open is Windows-only")
def test_zig_host_session_open_unsupported_off_windows() -> None:
    with pytest.raises(HostError) as ei:
        host_binding.host_session_open(host="posix")
    assert ei.value.status == 4  # REMEDY_CORE_UNSUPPORTED
