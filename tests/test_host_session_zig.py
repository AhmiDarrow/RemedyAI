"""Phase 3: Zig HostSession protocol + live orchestration (owns the shell host)."""

from __future__ import annotations

import sys

import pytest

from remedy.core.computer import host_binding
from remedy.core.computer.host_binding import HostError
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
def test_zig_wrap(host: str, command: str, sentinel: str) -> None:
    wrapped = host_binding.host_session_wrap(host=host, command=command, sentinel=sentinel)
    assert wrapped.startswith(command + "\n")
    assert sentinel in wrapped
    if host == "pwsh":
        assert "$LASTEXITCODE" in wrapped
    elif host == "cmd" or WINDOWS:
        # Zig (and the retired Python twin) use cmd ERRORLEVEL whenever the
        # process is on Windows, even if the dialect label is "posix".
        assert "%ERRORLEVEL%" in wrapped
    else:
        assert "$?" in wrapped


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
def test_zig_split(text: str, code: int, body: str) -> None:
    zig_code, zig_body = host_binding.host_session_split(
        text=text, sentinel="REMEDY_HOST_DONE_abc"
    )
    assert (zig_code, zig_body) == (code, body)


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
@windows_only
@pytest.mark.asyncio
async def test_python_host_session_is_thin_zig_binding() -> None:
    """HostSession on Windows must open/run/close only through Zig authorized open."""
    from remedy.execution.host.session import HostSession

    sess = HostSession(host="cmd", use_conpty=False)
    try:
        await sess.start()
        assert sess._zig_handle
        assert sess._proc is None
        res = await sess.run("echo thin-binding-ok", timeout=20.0)
        assert res.timed_out is False
        assert "thin-binding-ok" in res.stdout
    finally:
        await sess.close()
    assert sess._zig_handle == 0


@requires_core
@windows_only
def test_zig_host_session_open_authorized_round_trip() -> None:
    token, now = host_binding.issue_host_session_token("cmd")
    handle = host_binding.host_session_open_authorized(
        host="cmd", use_conpty=False, token=token, now_ms=now
    )
    try:
        result = host_binding.host_session_run(
            handle, "echo host-session-auth-ok", timeout_ms=20_000
        )
        assert result.get("timed_out") is False
        assert "host-session-auth-ok" in (result.get("stdout") or "")
    finally:
        host_binding.host_session_close(handle)


@requires_core
@pytest.mark.skipif(WINDOWS, reason="live open is Windows-only")
def test_zig_host_session_open_unsupported_off_windows() -> None:
    with pytest.raises(HostError) as ei:
        host_binding.host_session_open(host="posix")
    assert ei.value.status == 4  # REMEDY_CORE_UNSUPPORTED
