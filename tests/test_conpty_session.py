"""The ConPTY spawn path — Zig-backed via ``host_binding`` (ABI 5).

What breaks if this code is wrong: ``execution/host/conpty`` asks ``remedy_core``
to create a pseudoconsole and attach a child. Get a guard wrong and Remedy tries
that on a machine with no such API; get a failure path wrong and every aborted
spawn leaks a session; get the argument marshalling wrong and the child is
launched with the wrong command line or environment.

Win32 stays inside Zig. Tests mock ``host_binding`` through
``tests.harness.fake_host_binding.install_fake_conpty`` (backed by
``FakeConsoleHost``) — no real console, no real process.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Any

import pytest

from remedy.execution.host import conpty
from remedy.execution.host.conpty import (
    _ConPTYProcess,
    _HandleStream,
    spawn_conpty,
    spawn_conpty_supported,
)
from tests.harness.fake_host_binding import FakeHostConpty, install_fake_conpty
from tests.harness.fake_win32 import (
    CONPTY_PRELUDE,
    FakeConsoleHost,
    FakeShellExitError,
    fake_cmd_shell,
)

WINDOWS = sys.platform == "win32"

# Binding fakes are portable; live Zig ConPTY still needs Windows.
windows_only = pytest.mark.skipif(not WINDOWS, reason="ConPTY is Windows-only")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def wired() -> Any:
    host = FakeConsoleHost()
    with install_fake_conpty(console=host) as fake:
        yield host, fake


@pytest.fixture(autouse=True)
def _no_leaked_override() -> Any:
    yield
    if hasattr(spawn_conpty, "_override"):
        delattr(spawn_conpty, "_override")


def _session_of(fake: FakeHostConpty, proc: _ConPTYProcess) -> Any:
    return fake.sessions[proc._handle]


# ---------------------------------------------------------------------------
# spawn_conpty_supported
# ---------------------------------------------------------------------------


def test_conpty_is_never_reported_supported_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert spawn_conpty_supported() is False


def test_the_support_probe_off_windows_does_not_call_the_binding() -> None:
    host = FakeConsoleHost()
    with install_fake_conpty(console=host, platform="linux") as fake:
        assert spawn_conpty_supported() is False
    assert fake.calls == []


def test_conpty_is_supported_when_the_binding_reports_available() -> None:
    with install_fake_conpty(available=True):
        assert spawn_conpty_supported() is True


def test_conpty_is_unsupported_when_the_binding_reports_unavailable() -> None:
    with install_fake_conpty(available=False):
        assert spawn_conpty_supported() is False


def test_a_binding_that_refuses_to_load_is_reported_unsupported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remedy.core.computer import host_binding
    from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

    monkeypatch.setattr(sys, "platform", "win32")

    def explode() -> bool:
        raise NativeRuntimeUnavailableError("no remedy_core")

    monkeypatch.setattr(host_binding, "conpty_available", explode)
    assert spawn_conpty_supported() is False


def test_probing_for_support_never_creates_a_console_or_a_process(wired: Any) -> None:
    host, fake = wired
    assert spawn_conpty_supported() is True
    assert host.pseudoconsoles == []
    assert host.spawns == []
    assert fake.spawns == []


# ---------------------------------------------------------------------------
# spawn_conpty — async entry + override
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_is_refused_off_windows_rather_than_falling_back_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="ConPTY is Windows-only"):
        await spawn_conpty(["echo", "hi"])


@pytest.mark.asyncio
async def test_an_installed_override_receives_the_argv_cwd_and_env_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def fake_spawn(argv: list[str], *, cwd: str | None, env: dict[str, str] | None) -> str:
        seen.update(argv=argv, cwd=cwd, env=env)
        return "sentinel-process"

    monkeypatch.setattr(spawn_conpty, "_override", fake_spawn, raising=False)
    got = await spawn_conpty(["cmd.exe", "/c", "dir"], cwd="C:\\work", env={"A": "1"})

    assert got == "sentinel-process"
    assert seen == {"argv": ["cmd.exe", "/c", "dir"], "cwd": "C:\\work", "env": {"A": "1"}}


@pytest.mark.asyncio
async def test_an_override_wins_over_the_platform_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_spawn(_argv: list[str], **_kw: Any) -> str:
        return "ok"

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(spawn_conpty, "_override", fake_spawn, raising=False)
    assert await spawn_conpty(["true"]) == "ok"


@pytest.mark.asyncio
async def test_an_override_set_back_to_none_stops_intercepting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(spawn_conpty, "_override", None, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="Windows-only"):
        await spawn_conpty(["true"])


@pytest.mark.asyncio
async def test_a_failing_override_propagates_so_the_caller_can_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(_argv: list[str], **_kw: Any) -> Any:
        raise OSError("no console for you")

    monkeypatch.setattr(spawn_conpty, "_override", boom, raising=False)
    with pytest.raises(OSError, match="no console for you"):
        await spawn_conpty(["cmd.exe"])


@pytest.mark.asyncio
async def test_the_blocking_spawn_runs_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_sync(argv: list[str], cwd: str | None, env: dict[str, str] | None) -> str:
        seen.update(thread=threading.get_ident(), argv=argv, cwd=cwd, env=env)
        return "proc"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(conpty, "_spawn_conpty_sync", fake_sync)

    got = await spawn_conpty(["cmd.exe"], cwd=None, env={"K": "V"})

    assert got == "proc"
    assert seen["thread"] != threading.get_ident()
    assert (seen["argv"], seen["cwd"], seen["env"]) == (["cmd.exe"], None, {"K": "V"})


@pytest.mark.asyncio
async def test_a_win32_spawn_failure_reaches_the_caller_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_sync(*_a: Any) -> Any:
        raise OSError("conpty_spawn: operation failed winerr=2")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(conpty, "_spawn_conpty_sync", fake_sync)
    with pytest.raises(OSError, match="winerr=2"):
        await spawn_conpty(["nope.exe"])


# ---------------------------------------------------------------------------
# _spawn_conpty_sync — binding arguments and cleanup
# ---------------------------------------------------------------------------


@windows_only
def test_the_pseudoconsole_gets_the_pty_ends_of_both_pipes(wired: Any) -> None:
    host, _fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert len(host.pipes) == 2
    assert host.pseudoconsoles == [
        {
            "cols": 120,
            "rows": 40,
            "input": host.pipes[0].read_handle,
            "output": host.pipes[1].write_handle,
            "flags": 0,
        }
    ]


@windows_only
def test_the_parent_closes_the_pty_side_ends_and_keeps_its_own(wired: Any) -> None:
    host, _fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.is_closed(host.pipes[0].read_handle), "pty input end must be released"
    assert host.is_closed(host.pipes[1].write_handle), "pty output end must be released"
    assert not host.is_closed(host.pipes[0].write_handle), "parent still writes here"
    assert not host.is_closed(host.pipes[1].read_handle), "parent still reads here"


@windows_only
@pytest.mark.parametrize(
    "argv",
    [
        ["cmd.exe"],
        ["cmd.exe", "/c", "echo hi"],
        ["C:\\Program Files\\x.exe", "-v"],
        ["cmd.exe", "/c", "echo a & del b"],
        ["cmd.exe", "/c", 'say "hi"'],
    ],
)
def test_the_command_line_is_quoted_argument_by_argument(
    wired: Any, argv: list[str]
) -> None:
    """Authorized ConPTY resolves argv[0] via PATH, then quotes argument-by-argument."""
    import shutil
    from pathlib import Path

    host, fake = wired
    conpty._spawn_conpty_sync(argv, None, None)
    expected_argv = [str(a) for a in argv]
    if not Path(expected_argv[0]).is_absolute():
        found = shutil.which(expected_argv[0])
        assert found is not None, expected_argv[0]
        expected_argv[0] = str(Path(found).resolve())
    cmdline = subprocess.list2cmdline(expected_argv)
    assert fake.spawns[0]["cmdline"] == cmdline
    assert host.spawns[0]["cmdline"] == cmdline


@windows_only
@pytest.mark.parametrize("cwd", [None, "C:\\work", "\\\\server\\share"])
def test_the_working_directory_is_handed_over_unchanged(wired: Any, cwd: str | None) -> None:
    _host, fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], cwd, None)
    assert fake.spawns[0]["cwd"] == cwd


@windows_only
def test_the_child_gets_a_unicode_environment_and_extended_flags(wired: Any) -> None:
    host, fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, {"A": "b"})
    assert fake.spawns[0]["env"] == {"A": "b"}
    assert host.spawns[0]["flags"] & 0x00080000
    assert host.spawns[0]["flags"] & 0x00000400
    assert host.spawns[0]["env"] == "A=b"


@windows_only
def test_no_environment_block_is_passed_when_none_was_requested(wired: Any) -> None:
    host, fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert fake.spawns[0]["env"] is None
    assert host.spawns[0]["env"] is None


@windows_only
def test_the_pipe_ends_are_created_non_inheritable(wired: Any) -> None:
    host, _fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert [p.inheritable for p in host.pipes] == [False, False]


@windows_only
def test_the_child_inherits_no_handles(wired: Any) -> None:
    host, fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert fake.spawns[0]["inherit"] is False
    assert host.spawns[0]["inherit"] is False


@windows_only
def test_the_pseudoconsole_is_attached_through_the_documented_attribute(wired: Any) -> None:
    host, _fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert len(host.attribute_updates) == 1
    args = host.attribute_updates[0]
    assert args[2] == 0x00020016


@windows_only
def test_the_thread_handle_is_closed_immediately(wired: Any) -> None:
    host, _fake = wired
    conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    threads = [h for h, kind in host.kinds.items() if kind == "thread"]
    assert threads and all(host.is_closed(h) for h in threads)


@windows_only
def test_a_successful_spawn_returns_a_usable_process(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.spawns, "no child was created"
    assert proc.pid > 0
    assert proc.stdin is not None and proc.stdout is not None
    assert proc._handle in fake.sessions
    session = _session_of(fake, proc)
    assert session.pc_handle
    assert session.process_handle


@windows_only
def test_a_failed_input_pipe_stops_before_a_console_or_a_child_exists() -> None:
    host = FakeConsoleHost(fail_create_pipe=True)
    with install_fake_conpty(console=host):
        with pytest.raises(OSError, match="operation failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.pseudoconsoles == []
    assert host.spawns == []
    assert host.open_handles == []


@windows_only
def test_a_failed_output_pipe_releases_the_input_pipe_it_already_opened() -> None:
    host = FakeConsoleHost()
    with install_fake_conpty(console=host, fail_after_pipes=2):
        with pytest.raises(OSError, match="operation failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.open_handles == [], f"leaked {host.open_handles}"
    assert host.double_closed == []
    assert host.pseudoconsoles == []
    assert host.spawns == []


@windows_only
def test_a_failed_pseudoconsole_reports_the_error_and_leaks_nothing() -> None:
    host = FakeConsoleHost(create_pseudoconsole_hr=-2147024809)
    with install_fake_conpty(console=host):
        with pytest.raises(OSError, match=r"winerr=-2147024809"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.open_handles == []
    assert host.double_closed == []
    assert host.spawns == []


@windows_only
def test_a_failed_attribute_list_closes_the_console_and_starts_nothing() -> None:
    host = FakeConsoleHost(fail_attribute_list=True)
    with install_fake_conpty(console=host):
        with pytest.raises(OSError, match="operation failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.closed_pseudoconsoles, "the pseudoconsole must be released"
    assert host.open_handles == []
    assert host.spawns == []


@windows_only
def test_a_failed_attribute_update_closes_the_console() -> None:
    host = FakeConsoleHost()
    with install_fake_conpty(console=host, fail_attribute_update=True):
        with pytest.raises(OSError, match="operation failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.closed_pseudoconsoles
    assert host.open_handles == []
    assert host.spawns == []


@windows_only
def test_a_failed_createprocess_closes_every_handle_and_the_console() -> None:
    host = FakeConsoleHost(fail_create_process=True)
    with install_fake_conpty(console=host):
        with pytest.raises(OSError, match=r"winerr=2"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.open_handles == [], f"leaked {host.open_handles}"
    assert host.closed_pseudoconsoles
    assert host.terminated == []


# ---------------------------------------------------------------------------
# _HandleStream / _ConPTYProcess
# ---------------------------------------------------------------------------


@windows_only
def test_the_stdin_stream_writes_the_bytes_it_was_given(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    proc.stdin.write(b"dir\r\n")
    assert host.written(session.stdin_handle) == b"dir\r\n"


@windows_only
def test_a_read_stream_refuses_to_write(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    proc.stdout.write(b"nope")
    assert host.written(session.stdout_handle) == b""


@windows_only
def test_writing_after_close_is_ignored_rather_than_raising(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    stdin_h = session.stdin_handle
    proc.stdin.close()
    proc.stdin.write(b"too late")
    assert host.written(stdin_h) == b""


@windows_only
@pytest.mark.asyncio
async def test_the_stdout_stream_reads_what_the_child_produced(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"hello world")
    assert await proc.stdout.read(4096) == b"hello world"


@windows_only
@pytest.mark.asyncio
async def test_a_read_returns_only_the_bytes_the_api_reported(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"abc")
    got = await proc.stdout.read(4096)
    assert len(got) == 3
    assert b"\x00" not in got


@windows_only
@pytest.mark.asyncio
async def test_a_read_is_capped_by_the_requested_size(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"abcdef")
    assert await proc.stdout.read(2) == b"ab"
    assert await proc.stdout.read(4) == b"cdef"


@windows_only
@pytest.mark.asyncio
async def test_an_empty_pipe_reads_as_end_of_stream_not_an_error(wired: Any) -> None:
    _host, _fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert await proc.stdout.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_a_zero_length_read_still_consumes_a_byte(wired: Any) -> None:
    """Documents today's behaviour: n is clamped up to 1, not down to 0."""
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"ab")
    assert await proc.stdout.read(0) == b"a"


@windows_only
@pytest.mark.asyncio
async def test_a_write_stream_never_returns_data(wired: Any) -> None:
    _host, _fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert await proc.stdin.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_after_close_returns_end_of_stream(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"leftover")
    proc.stdout.close()
    assert await proc.stdout.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_does_not_block_the_event_loop(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    seen: dict[str, int] = {}
    original = fake.conpty_read

    def note_thread(handle: int, max_len: int = 4096) -> bytes:
        seen["thread"] = threading.get_ident()
        return original(handle, max_len)

    fake.conpty_read = note_thread  # type: ignore[method-assign]
    from remedy.core.computer import host_binding as H

    H.conpty_read = note_thread
    host.feed(session.stdout_handle, b"x")
    assert await proc.stdout.read() == b"x"
    assert seen["thread"] != threading.get_ident()


@windows_only
@pytest.mark.asyncio
async def test_drain_is_a_no_op_that_still_awaits(wired: Any) -> None:
    _host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    before = len(fake.calls)
    assert await proc.stdin.drain() is None
    assert len(fake.calls) == before


@windows_only
def test_closing_a_stream_twice_closes_the_handle_once(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    stdin_h = session.stdin_handle
    proc.stdin.close()
    proc.stdin.close()
    assert host.closed.count(stdin_h) == 1
    assert host.double_closed == []


@windows_only
def test_a_new_process_looks_like_an_asyncio_subprocess(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)

    assert proc.pid == host.pid
    assert proc.returncode is None
    assert proc.poll() is None
    assert proc.stderr is None
    assert isinstance(proc.stdin, _HandleStream)
    assert isinstance(proc.stdout, _HandleStream)
    assert proc.stdin._write is True
    assert proc.stdout._write is False
    assert session.stdin_handle
    assert session.stdout_handle


@windows_only
@pytest.mark.parametrize("method", ["kill", "terminate"])
def test_killing_the_process_terminates_it_and_releases_everything(
    wired: Any, method: str
) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    handles = {
        "stdin": session.stdin_handle,
        "stdout": session.stdout_handle,
        "pc": session.pc_handle,
        "process": session.process_handle,
    }

    getattr(proc, method)()

    assert host.terminated == [(handles["process"], 1)]
    assert proc.returncode == 1
    assert proc.poll() == 1
    assert host.is_closed(handles["stdin"])
    assert host.is_closed(handles["stdout"])
    assert host.closed_pseudoconsoles == [handles["pc"]]
    assert host.is_closed(handles["process"])


@windows_only
def test_killing_twice_does_not_close_anything_twice(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    pc = session.pc_handle

    proc.kill()
    proc.kill()

    assert host.double_closed == []
    assert host.closed_pseudoconsoles == [pc]
    assert proc.returncode == 1


@windows_only
def test_a_terminated_process_stops_accepting_input(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    stdin_h = session.stdin_handle
    host.feed(session.stdout_handle, b"still buffered")

    proc.terminate()
    proc.stdin.write(b"echo\r\n")

    assert host.written(stdin_h) == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_from_a_terminated_process_returns_end_of_stream(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    host.feed(session.stdout_handle, b"still buffered")
    proc.terminate()
    assert await proc.stdout.read() == b""


@windows_only
def test_a_terminateprocess_that_fails_still_tears_the_session_down(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    pc = session.pc_handle
    stdin_h = session.stdin_handle

    def boom(_handle: int) -> None:
        raise OSError("access denied")

    from remedy.core.computer import host_binding as H

    H.conpty_kill = boom
    proc.kill()

    assert proc.returncode == 1
    assert host.is_closed(stdin_h)
    assert host.closed_pseudoconsoles == [pc]


@windows_only
def test_a_process_with_no_handles_at_all_terminates_without_a_wild_close(wired: Any) -> None:
    host, _fake = wired
    proc = _ConPTYProcess(pid=0, handle=0)
    proc.terminate()
    assert host.closed_pseudoconsoles == []
    assert proc.returncode == 1


@windows_only
def test_the_streams_are_closed_even_when_one_of_them_throws(wired: Any) -> None:
    host, fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    session = _session_of(fake, proc)
    stdout_h = session.stdout_handle

    def boom() -> None:
        raise OSError("stdin close failed")

    proc.stdin.close = boom  # type: ignore[method-assign]
    proc.kill()

    assert host.is_closed(stdout_h), "one bad stream must not strand the other"
    assert proc.returncode == 1


@windows_only
@pytest.mark.asyncio
async def test_a_spawned_session_round_trips_a_command_through_the_double() -> None:
    host = FakeConsoleHost()
    with install_fake_conpty(console=host) as fake:
        proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)

        async def override(argv: list[str], *, cwd: Any = None, env: Any = None) -> Any:
            assert argv == ["cmd.exe"]
            return proc

        spawn_conpty._override = override  # type: ignore[attr-defined]
        got = await spawn_conpty(["cmd.exe"])
        assert got is proc

        got.stdin.write(b"ver\r\n")
        await got.stdin.drain()
        session = _session_of(fake, proc)
        stdin_h = session.stdin_handle
        stdout_h = session.stdout_handle
        process_h = session.process_handle
        host.feed(stdout_h, b"Microsoft Windows [Version 10.0]\r\n")
        assert await got.stdout.read() == b"Microsoft Windows [Version 10.0]\r\n"

        got.kill()

    assert host.written(stdin_h) == b"ver\r\n"
    assert host.terminated == [(process_h, 1)]
    assert host.double_closed == []


# ---------------------------------------------------------------------------
# Sentinel protocol — Zig HostSession owns wrap/split (no Python twin)
# ---------------------------------------------------------------------------


def _core_available() -> bool:
    try:
        from remedy.core.computer import host_binding

        host_binding._lib()
        return True
    except Exception:
        return False


requires_core = pytest.mark.skipif(not _core_available(), reason="remedy_core not built")


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
def test_the_sentinel_is_read_in_its_expanded_form(text: str, code: int, body: str) -> None:
    from remedy.core.computer import host_binding

    assert host_binding.host_session_split(text=text, sentinel="REMEDY_HOST_DONE_abc") == (
        code,
        body,
    )


@requires_core
@pytest.mark.parametrize(
    "echoed",
    [
        "echo REMEDY_HOST_DONE_abc:%ERRORLEVEL%\r\n",
        'Write-Output "REMEDY_HOST_DONE_abc:$LASTEXITCODE"\r\n',
        "echo REMEDY_HOST_DONE_abc:$?\n",
    ],
)
def test_an_echoed_unexpanded_sentinel_is_not_a_completion(echoed: str) -> None:
    from remedy.core.computer import host_binding

    # Unexpanded echo of the sentinel template is not completion.
    assert host_binding.host_session_split(text=echoed, sentinel="REMEDY_HOST_DONE_abc")[0] == -1
    # Expanded SENTINEL:0 after the echo is completion.
    code, _body = host_binding.host_session_split(
        text=echoed + "REMEDY_HOST_DONE_abc:0\r\n",
        sentinel="REMEDY_HOST_DONE_abc",
    )
    assert code == 0


@requires_core
def test_the_split_for_a_pseudoconsole_strips_vt_and_the_echoed_command() -> None:
    from remedy.core.computer import host_binding

    sentinel = "REMEDY_HOST_DONE_abc"
    wrapped = host_binding.host_session_wrap(
        host="cmd", command="echo hello", sentinel=sentinel
    )
    stream = (
        CONPTY_PRELUDE.decode()
        + "chcp 65001 >NUL & @echo off\x1b[2;1H"
        + "echo hello\x1b[3;1Hhello\r\n"
        + f"echo {sentinel}:%ERRORLEVEL%\x1b[5;1H"
        + f"\x1b[?25h{sentinel}:0\r\n"
    )
    assert host_binding.host_session_split(
        text=stream, sentinel=sentinel, command=wrapped, conpty=True
    ) == (0, "hello")


def test_fake_cmd_shell_exit_ends_the_process() -> None:
    run = fake_cmd_shell()
    with pytest.raises(FakeShellExitError) as ei:
        run("exit /b 7")
    assert ei.value.code == 7
    with pytest.raises(FakeShellExitError) as ei:
        run("exit")
    assert ei.value.code == 0
    with pytest.raises(FakeShellExitError) as ei:
        run("exit 3")
    assert ei.value.code == 3
    out = run("no_such_cmd")
    assert "not recognized" in out
