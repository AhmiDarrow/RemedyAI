"""The ConPTY spawn path — the one place Remedy can start a real Windows console.

What breaks if this code is wrong: ``execution/host/conpty`` asks kernel32 to
create a pseudoconsole and then ``CreateProcessW`` a child attached to it. Get a
guard wrong and Remedy tries that on a machine with no such API; get a failure
path wrong and every aborted spawn leaks a pipe, a console and a live child
process into the owner's session — the session layer swallows the exception, so
nothing would ever complain out loud. Get the argument marshalling wrong and the
child is launched with the wrong command line, the wrong environment or handles
it should never have inherited.

So the emphasis here is the negative side: what must be refused before a single
handle is opened, what must be closed again when a step fails, what must NOT be
attempted once an earlier step failed, and what the parent must never hand to
the child. Every Win32 call goes through ``tests.harness.fake_win32`` — no real
console is ever created and no real process is ever started.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import subprocess
import sys
import threading
import types
from typing import Any

import pytest

from remedy.execution.host import conpty
from remedy.execution.host.conpty import (
    _ConPTYProcess,
    _env_block,
    _HandleStream,
    spawn_conpty,
    spawn_conpty_supported,
)
from tests.harness.fake_win32 import (
    FakeConsoleHost,
    FakeWinDLL,
    handle_value,
    install_fake_win32,
)

WINDOWS = sys.platform == "win32"

# ``_spawn_conpty_sync`` and ``_HandleStream`` build ctypes.wintypes structures
# and ctypes.HRESULT, neither of which the double can conjure off Windows.
windows_only = pytest.mark.skipif(
    not WINDOWS,
    reason="the code under test builds ctypes.wintypes structures, Windows-only",
)

_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def wired() -> Any:
    """A fake kernel32 with the in-memory console host behind it."""
    host = FakeConsoleHost()
    with install_fake_win32(console=host) as fake:
        yield host, fake


@pytest.fixture(autouse=True)
def _no_leaked_override() -> Any:
    """``spawn_conpty._override`` is process-global; never let it escape a test."""
    yield
    if hasattr(spawn_conpty, "_override"):
        delattr(spawn_conpty, "_override")


def _spawn_tolerating_the_return_bug(
    argv: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    """Run a spawn that gets all the way to CreateProcessW.

    Today the final ``int(wintypes.HANDLE())`` raises ValueError (see
    ``test_a_successful_spawn_currently_dies_...``), so tests that only care
    about *what was asked of kernel32* must not depend on the return.
    """
    with contextlib.suppress(ValueError):
        return conpty._spawn_conpty_sync(argv, cwd, env)
    return None


def _new_pipe(host: FakeConsoleHost) -> tuple[int, int]:
    """(read_handle, write_handle) allocated through the fake's bookkeeping."""
    read_h, write_h = ctypes.c_void_p(), ctypes.c_void_p()
    host.CreatePipe(ctypes.byref(read_h), ctypes.byref(write_h))
    return int(read_h.value or 0), int(write_h.value or 0)


def _open_process(host: FakeConsoleHost) -> tuple[_ConPTYProcess, dict[str, int]]:
    """A ``_ConPTYProcess`` wired to handles the fake console host tracks."""
    _pty_in, stdin_h = _new_pipe(host)
    stdout_h, _pty_out = _new_pipe(host)
    pc = ctypes.c_void_p()
    host.CreatePseudoConsole(types.SimpleNamespace(X=120, Y=40), _pty_in, _pty_out, 0, pc)
    host.CreateProcessW()
    handles = {
        "stdin": stdin_h,
        "stdout": stdout_h,
        "pc": int(pc.value or 0),
        "process": host.process_handle,
    }
    proc = _ConPTYProcess(
        pid=host.pid,
        process_handle=handles["process"],
        stdin_handle=stdin_h,
        stdout_handle=stdout_h,
        pc_handle=handles["pc"],
    )
    return proc, handles


# ---------------------------------------------------------------------------
# spawn_conpty_supported — the guard that keeps us off unsupported Windows
# ---------------------------------------------------------------------------


def test_conpty_is_never_reported_supported_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert spawn_conpty_supported() is False


def test_the_support_probe_off_windows_does_not_even_load_kernel32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    windll = FakeWinDLL()
    with install_fake_win32(windll=windll, platform="linux"):
        assert spawn_conpty_supported() is False
    assert len(windll.log) == 0
    assert windll.dlls == {}, "the platform guard must come before any DLL load"


def test_conpty_is_supported_when_kernel32_exports_createpseudoconsole() -> None:
    with install_fake_win32():
        assert spawn_conpty_supported() is True


def test_conpty_is_unsupported_on_a_windows_without_createpseudoconsole() -> None:
    windll = FakeWinDLL()
    with install_fake_win32(windll=windll):
        windll.dll("kernel32").set_missing("CreatePseudoConsole")
        assert spawn_conpty_supported() is False


def test_a_kernel32_that_refuses_to_load_is_reported_unsupported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    def explode(*_a: Any, **_k: Any) -> Any:
        raise OSError("kernel32 is not available here")

    monkeypatch.setattr(ctypes, "WinDLL", explode, raising=False)
    assert spawn_conpty_supported() is False


def test_probing_for_support_never_creates_a_console_or_a_process(wired: Any) -> None:
    host, fake = wired
    assert spawn_conpty_supported() is True
    assert host.pseudoconsoles == []
    assert host.spawns == []
    assert "CreatePseudoConsole" not in fake.log
    assert "CreateProcessW" not in fake.log


# ---------------------------------------------------------------------------
# spawn_conpty — the async entry point and its override hook
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
async def test_the_blocking_win32_spawn_runs_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CreateProcessW plus four pipe calls is long enough to stall a UI; running
    # it inline on the loop would freeze every other Remedy task.
    seen: dict[str, Any] = {}

    def fake_sync(argv: list[str], cwd: str | None, env: dict[str, str] | None) -> str:
        seen.update(thread=threading.get_ident(), argv=argv, cwd=cwd, env=env)
        return "proc"

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(conpty, "_spawn_conpty_sync", fake_sync)

    got = await spawn_conpty(["cmd.exe"], cwd=None, env={"K": "V"})

    assert got == "proc"
    assert seen["thread"] != threading.get_ident()
    # argv/cwd/env are handed over positionally, in that order.
    assert (seen["argv"], seen["cwd"], seen["env"]) == (["cmd.exe"], None, {"K": "V"})


@pytest.mark.asyncio
async def test_a_win32_spawn_failure_reaches_the_caller_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_sync(*_a: Any) -> Any:
        raise OSError("CreateProcessW failed winerr=2")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(conpty, "_spawn_conpty_sync", fake_sync)
    with pytest.raises(OSError, match="winerr=2"):
        await spawn_conpty(["nope.exe"])


# ---------------------------------------------------------------------------
# _env_block
# ---------------------------------------------------------------------------


def test_no_environment_means_no_environment_block() -> None:
    # None must stay None: an empty block would wipe the child's environment.
    assert _env_block(None) is None


def test_an_environment_block_is_nul_separated_and_double_nul_terminated() -> None:
    block = _env_block({"A": "1", "B": "2"})
    assert block is not None
    assert block[:].startswith("A=1\0B=2\0\0")


def test_an_empty_environment_still_produces_a_terminated_block() -> None:
    block = _env_block({})
    assert block is not None
    assert block[:].startswith("\0\0")


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"PATH": "C:\\bin;C:\\other"}, "PATH=C:\\bin;C:\\other\0\0"),
        ({"EQ": "a=b"}, "EQ=a=b\0\0"),
        ({"UNI": "café"}, "UNI=café\0\0"),
        ({"EMPTY": ""}, "EMPTY=\0\0"),
    ],
)
def test_environment_values_are_passed_through_untouched(env: dict[str, str], expected: str) -> None:
    block = _env_block(env)
    assert block is not None
    assert block[:].startswith(expected)


def test_environment_order_is_preserved() -> None:
    block = _env_block({"Z": "1", "A": "2", "M": "3"})
    assert block is not None
    assert block[:].startswith("Z=1\0A=2\0M=3\0\0")


# ---------------------------------------------------------------------------
# _spawn_conpty_sync — what the parent asks of kernel32
# ---------------------------------------------------------------------------


@windows_only
def test_the_pseudoconsole_gets_the_pty_ends_of_both_pipes(wired: Any) -> None:
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])

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
    # Holding the pty ends open would keep the child's console alive forever;
    # closing the parent ends would blind the session.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])

    assert host.is_closed(host.pipes[0].read_handle), "pty input end must be released"
    assert host.is_closed(host.pipes[1].write_handle), "pty output end must be released"
    assert not host.is_closed(host.pipes[0].write_handle), "parent still writes here"
    assert not host.is_closed(host.pipes[1].read_handle), "parent still reads here"


@windows_only
@pytest.mark.parametrize(
    ("argv", "cmdline"),
    [
        (["cmd.exe"], "cmd.exe"),
        (["cmd.exe", "/c", "echo hi"], 'cmd.exe /c "echo hi"'),
        (["C:\\Program Files\\x.exe", "-v"], '"C:\\Program Files\\x.exe" -v'),
        (["cmd.exe", "/c", "echo a & del b"], 'cmd.exe /c "echo a & del b"'),
        (["cmd.exe", '/c', 'say "hi"'], 'cmd.exe /c "say \\"hi\\""'),
    ],
)
def test_the_command_line_is_quoted_argument_by_argument(
    wired: Any, argv: list[str], cmdline: str
) -> None:
    # An argument containing spaces, quotes or a shell metacharacter must stay
    # ONE argument — never split, never able to append a second command.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(argv)
    assert host.spawns[0]["cmdline"] == cmdline
    assert host.spawns[0]["cmdline"] == subprocess.list2cmdline(argv)


@windows_only
def test_the_executable_is_named_only_by_the_command_line(wired: Any) -> None:
    # lpApplicationName is NULL, so argv[0] is resolved the same way a shell
    # would resolve it; passing both is where quoting bugs hide.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe", "/c", "ver"])
    assert host.spawns[0]["app"] is None


@windows_only
@pytest.mark.parametrize("cwd", [None, "C:\\work", "\\\\server\\share"])
def test_the_working_directory_is_handed_over_unchanged(wired: Any, cwd: str | None) -> None:
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"], cwd)
    assert host.spawns[0]["cwd"] == cwd


@windows_only
def test_the_child_gets_a_unicode_environment_and_an_extended_startupinfo(wired: Any) -> None:
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"], None, {"A": "b"})
    flags = host.spawns[0]["flags"]
    assert flags & _EXTENDED_STARTUPINFO_PRESENT, "without this the pty attribute is ignored"
    assert flags & _CREATE_UNICODE_ENVIRONMENT, "an ANSI block would mangle the unicode buffer"
    assert host.spawns[0]["env"] == "A=b"


@windows_only
def test_no_environment_block_is_passed_when_none_was_requested(wired: Any) -> None:
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"], None, None)
    assert host.spawns[0]["env"] is None


@windows_only
def test_the_pipe_ends_are_created_non_inheritable(wired: Any) -> None:
    # Inheritable pipe ends would be handed to every process Remedy later
    # spawns, and would keep the console's write end alive past the child's exit
    # — a shell that never reports EOF.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])
    assert [p.inheritable for p in host.pipes] == [False, False]


@windows_only
def test_the_child_inherits_no_handles(wired: Any) -> None:
    # The pseudoconsole duplicates what the child needs; blanket inheritance
    # would hand the child every other handle Remedy has open.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])
    assert host.spawns[0]["inherit"] is False


@windows_only
def test_the_pseudoconsole_is_attached_through_the_documented_attribute(wired: Any) -> None:
    # A wrong attribute id means CreateProcessW succeeds and the child simply
    # has no console — the failure would be invisible until output went missing.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])

    assert len(host.attribute_updates) == 1
    args = host.attribute_updates[0]
    assert args[2] == _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
    assert host.kinds.get(handle_value(args[3])) == "pseudoconsole"


@windows_only
def test_the_attribute_list_is_sized_by_a_probe_before_it_is_built(wired: Any) -> None:
    _host, fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])
    calls = fake.log.calls("InitializeProcThreadAttributeList")
    assert len(calls) == 2
    assert calls[0].args[0] is None, "first call must be the NULL size probe"
    assert calls[1].args[0] is not None


@windows_only
def test_the_attribute_list_is_released_once_the_process_exists(wired: Any) -> None:
    _host, fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])
    assert fake.log.count("DeleteProcThreadAttributeList") == 1


@windows_only
def test_the_thread_handle_is_closed_immediately(wired: Any) -> None:
    # Only the process handle is useful to us; a retained thread handle is a
    # per-command leak in a long-lived session.
    host, _fake = wired
    _spawn_tolerating_the_return_bug(["cmd.exe"])
    threads = [h for h, kind in host.kinds.items() if kind == "thread"]
    assert threads and all(host.is_closed(h) for h in threads)


@windows_only
def test_a_successful_spawn_returns_a_usable_process(wired: Any) -> None:
    """ConPTY could never actually succeed.

    ``int(wintypes.HANDLE())`` raises ValueError — a HANDLE is a c_void_p,
    which exposes a buffer rather than an ``__int__`` — so constructing the
    ``_ConPTYProcess`` threw on the *last* statement, after the child had
    already been created. The session layer caught everything and fell back to
    pipes, so the only visible symptom was that ConPTY silently never worked,
    while each attempt left a running child, its process handle, the
    pseudoconsole and both parent pipe ends behind.
    """
    host, _fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.spawns, "no child was created"
    assert proc.pid > 0
    # The handles become the two ends of the duck-typed stdio pair.
    assert proc.stdin is not None and proc.stdout is not None
    assert proc._pc, "the pseudoconsole handle was lost"
    assert proc._ph, "the process handle was lost"


@windows_only
def test_the_spawned_handles_are_the_ones_the_pipes_actually_returned(
    wired: Any,
) -> None:
    """A silently-zeroed handle would read as "no pipe" rather than an error."""
    host, _fake = wired
    proc = conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.kinds[proc._pc] == "pseudoconsole"
    assert host.kinds[proc._ph] == "process"
    assert proc.stdin._handle in host.kinds
    assert proc.stdout._handle in host.kinds


# ---------------------------------------------------------------------------
# _spawn_conpty_sync — every failure path cleans up and stops
# ---------------------------------------------------------------------------


@windows_only
def test_a_failed_input_pipe_stops_before_a_console_or_a_child_exists() -> None:
    host = FakeConsoleHost(fail_create_pipe=True)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match="CreatePipe input failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
    assert host.pseudoconsoles == []
    assert host.spawns == []
    assert host.open_handles == []


@windows_only
def test_a_failed_output_pipe_releases_the_input_pipe_it_already_opened() -> None:
    host = FakeConsoleHost()
    with install_fake_win32(console=host) as fake:
        calls = {"n": 0}
        real = host.CreatePipe

        def second_one_fails(*args: Any, **kw: Any) -> int:
            calls["n"] += 1
            return 0 if calls["n"] == 2 else real(*args, **kw)

        fake.kernel32.on("CreatePipe", second_one_fails)
        with pytest.raises(OSError, match="CreatePipe output failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.open_handles == [], f"leaked {host.open_handles}"
    assert host.double_closed == []
    assert host.pseudoconsoles == []
    assert host.spawns == []


@windows_only
def test_a_failed_pseudoconsole_reports_the_hresult_and_leaks_nothing() -> None:
    host = FakeConsoleHost(create_pseudoconsole_hr=-2147024809)
    with install_fake_win32(console=host):
        with pytest.raises(OSError, match=r"CreatePseudoConsole failed hr=-2147024809"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.open_handles == []
    assert host.double_closed == [], "every pipe end is closed exactly once"
    assert host.spawns == [], "no child may be started once the console failed"


@windows_only
def test_a_failed_attribute_list_closes_the_console_and_starts_nothing() -> None:
    host = FakeConsoleHost(fail_attribute_list=True)
    with install_fake_win32(console=host) as fake:
        with pytest.raises(OSError, match="InitializeProcThreadAttributeList failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

    assert host.closed_pseudoconsoles, "the pseudoconsole must be released"
    assert host.open_handles == []
    assert host.spawns == []
    assert fake.log.count("DeleteProcThreadAttributeList") == 0, "nothing was initialised"


@windows_only
def test_a_failed_attribute_update_deletes_the_list_it_had_built() -> None:
    host = FakeConsoleHost()
    with install_fake_win32(console=host) as fake:
        fake.kernel32.on("UpdateProcThreadAttribute", lambda *_a: 0)
        with pytest.raises(OSError, match="UpdateProcThreadAttribute failed"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)

        assert fake.log.count("DeleteProcThreadAttributeList") == 1

    assert host.closed_pseudoconsoles
    assert host.open_handles == []
    assert host.spawns == []


@windows_only
def test_a_failed_createprocess_closes_every_handle_and_the_console() -> None:
    host = FakeConsoleHost(fail_create_process=True)
    with install_fake_win32(console=host) as fake:
        with pytest.raises(OSError, match=r"CreateProcessW failed winerr=\d+"):
            conpty._spawn_conpty_sync(["cmd.exe"], None, None)
        assert fake.log.count("DeleteProcThreadAttributeList") == 1

    assert host.open_handles == [], f"leaked {host.open_handles}"
    assert host.closed_pseudoconsoles
    assert host.terminated == [], "there is no child to terminate"


# ---------------------------------------------------------------------------
# _HandleStream
# ---------------------------------------------------------------------------


@windows_only
def test_the_stdin_stream_writes_the_bytes_it_was_given(wired: Any) -> None:
    host, _fake = wired
    _read, write_h = _new_pipe(host)
    stream = _HandleStream(write_h, write=True)
    stream.write(b"dir\r\n")
    assert host.written(write_h) == b"dir\r\n"


@windows_only
def test_a_read_stream_refuses_to_write(wired: Any) -> None:
    # stdout is read-only; a write here would be a bug that silently corrupts
    # whatever the other end of the pipe is doing.
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    stream = _HandleStream(read_h, write=False)
    stream.write(b"nope")
    assert host.written(read_h) == b""


@windows_only
def test_writing_after_close_is_ignored_rather_than_raising(wired: Any) -> None:
    host, _fake = wired
    _read, write_h = _new_pipe(host)
    stream = _HandleStream(write_h, write=True)
    stream.close()
    stream.write(b"too late")
    assert host.written(write_h) == b""


@windows_only
def test_a_write_to_a_dead_handle_does_not_raise(wired: Any) -> None:
    _host, _fake = wired
    stream = _HandleStream(0xDEAD, write=True)
    stream.write(b"x")  # WriteFile returns FALSE; the session must survive it


@windows_only
@pytest.mark.asyncio
async def test_the_stdout_stream_reads_what_the_child_produced(wired: Any) -> None:
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    host.feed(read_h, b"hello world")
    stream = _HandleStream(read_h, write=False)
    assert await stream.read(4096) == b"hello world"


@windows_only
@pytest.mark.asyncio
async def test_a_read_returns_only_the_bytes_the_api_reported(wired: Any) -> None:
    # The buffer is as long as the request; handing all of it back would append
    # NUL padding to every chunk of console output the session decodes.
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    host.feed(read_h, b"abc")
    got = await _HandleStream(read_h, write=False).read(4096)
    assert len(got) == 3
    assert b"\x00" not in got


@windows_only
@pytest.mark.asyncio
async def test_a_read_is_capped_by_the_requested_size(wired: Any) -> None:
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    host.feed(read_h, b"abcdef")
    stream = _HandleStream(read_h, write=False)
    assert await stream.read(2) == b"ab"
    assert await stream.read(4) == b"cdef"


@windows_only
@pytest.mark.asyncio
async def test_an_empty_pipe_reads_as_end_of_stream_not_an_error(wired: Any) -> None:
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    stream = _HandleStream(read_h, write=False)
    assert await stream.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_a_zero_length_read_still_consumes_a_byte(wired: Any) -> None:
    """Documents today's behaviour: n is clamped up to 1, not down to 0.

    ``read(0)`` therefore takes a byte off the pipe instead of returning
    immediately — harmless for the session (which always asks for 4096) but not
    what the signature suggests.
    """
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    host.feed(read_h, b"ab")
    stream = _HandleStream(read_h, write=False)
    assert await stream.read(0) == b"a"


@windows_only
@pytest.mark.asyncio
async def test_a_write_stream_never_returns_data(wired: Any) -> None:
    host, _fake = wired
    _read, write_h = _new_pipe(host)
    host.feed(write_h, b"not for you")
    stream = _HandleStream(write_h, write=True)
    assert await stream.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_after_close_returns_end_of_stream(wired: Any) -> None:
    host, _fake = wired
    read_h, _write = _new_pipe(host)
    host.feed(read_h, b"leftover")
    stream = _HandleStream(read_h, write=False)
    stream.close()
    assert await stream.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_a_failing_readfile_reads_as_end_of_stream(wired: Any) -> None:
    _host, fake = wired
    fake.kernel32.on("ReadFile", lambda *_a: 0)
    stream = _HandleStream(0x100, write=False)
    assert await stream.read() == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_does_not_block_the_event_loop(wired: Any) -> None:
    host, fake = wired
    read_h, _write = _new_pipe(host)
    seen: dict[str, int] = {}
    original = host.ReadFile

    def note_thread(*args: Any, **kw: Any) -> int:
        seen["thread"] = threading.get_ident()
        return original(*args, **kw)

    fake.kernel32.on("ReadFile", note_thread)
    host.feed(read_h, b"x")
    assert await _HandleStream(read_h, write=False).read() == b"x"
    assert seen["thread"] != threading.get_ident()


@windows_only
@pytest.mark.asyncio
async def test_drain_is_a_no_op_that_still_awaits(wired: Any) -> None:
    _host, fake = wired
    stream = _HandleStream(0x100, write=True)
    assert await stream.drain() is None
    assert len(fake.log) == 0


@windows_only
def test_closing_a_stream_twice_closes_the_handle_once(wired: Any) -> None:
    # A second CloseHandle on a recycled handle number closes somebody else's
    # file. Idempotence here is a safety property, not a nicety.
    host, _fake = wired
    _read, write_h = _new_pipe(host)
    stream = _HandleStream(write_h, write=True)
    stream.close()
    stream.close()
    assert host.closed.count(write_h) == 1
    assert host.double_closed == []


@windows_only
def test_a_failing_closehandle_is_swallowed_and_the_stream_stays_closed(wired: Any) -> None:
    host, fake = wired
    _read, write_h = _new_pipe(host)
    fake.kernel32.function("CloseHandle").set_error(OSError("bad handle"))
    stream = _HandleStream(write_h, write=True)
    stream.close()  # must not escape: it runs inside process teardown
    stream.write(b"x")
    assert host.written(write_h) == b""


# ---------------------------------------------------------------------------
# _ConPTYProcess
# ---------------------------------------------------------------------------


@windows_only
def test_a_new_process_looks_like_an_asyncio_subprocess(wired: Any) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)

    assert proc.pid == host.pid
    assert proc.returncode is None, "a live process has no exit code yet"
    assert proc.stderr is None, "a console merges stderr into stdout"
    assert isinstance(proc.stdin, _HandleStream)
    assert isinstance(proc.stdout, _HandleStream)
    assert proc.stdin._write is True
    assert proc.stdout._write is False
    assert proc.stdin._handle == handles["stdin"]
    assert proc.stdout._handle == handles["stdout"]


@windows_only
@pytest.mark.parametrize("method", ["kill", "terminate"])
def test_killing_the_process_terminates_it_and_releases_everything(
    wired: Any, method: str
) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)

    getattr(proc, method)()

    assert host.terminated == [(handles["process"], 1)]
    assert proc.returncode == 1
    assert host.is_closed(handles["stdin"])
    assert host.is_closed(handles["stdout"])
    assert host.closed_pseudoconsoles == [handles["pc"]]
    assert host.is_closed(handles["process"])


@windows_only
def test_killing_twice_does_not_close_anything_twice(wired: Any) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)

    proc.kill()
    proc.kill()

    assert host.double_closed == [], "a recycled handle number would be closed blind"
    assert host.closed_pseudoconsoles == [handles["pc"]]
    assert host.terminated == [(handles["process"], 1), (0, 1)]
    assert proc.returncode == 1


@windows_only
def test_a_terminated_process_stops_accepting_input(wired: Any) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)
    host.feed(handles["stdout"], b"still buffered")

    proc.terminate()
    proc.stdin.write(b"echo\r\n")

    assert host.written(handles["stdin"]) == b""


@windows_only
@pytest.mark.asyncio
async def test_reading_from_a_terminated_process_returns_end_of_stream(wired: Any) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)
    host.feed(handles["stdout"], b"still buffered")
    proc.terminate()
    assert await proc.stdout.read() == b""


@windows_only
def test_a_terminateprocess_that_fails_still_tears_the_session_down(wired: Any) -> None:
    # The child may already be gone; that must not leave the console open.
    host, fake = wired
    proc, handles = _open_process(host)
    fake.kernel32.function("TerminateProcess").set_error(OSError("access denied"))

    proc.kill()

    assert proc.returncode == 1
    assert host.is_closed(handles["stdin"])
    assert host.closed_pseudoconsoles == [handles["pc"]]


@windows_only
def test_a_process_without_a_pseudoconsole_closes_only_what_it_owns(wired: Any) -> None:
    host, _fake = wired
    _read, stdin_h = _new_pipe(host)
    stdout_h, _w = _new_pipe(host)
    host.CreateProcessW()
    proc = _ConPTYProcess(
        pid=1,
        process_handle=host.process_handle,
        stdin_handle=stdin_h,
        stdout_handle=stdout_h,
        pc_handle=0,
    )

    proc.kill()

    assert host.closed_pseudoconsoles == [], "ClosePseudoConsole(0) would be a wild call"
    assert host.is_closed(host.process_handle)


@windows_only
def test_a_process_with_no_handles_at_all_terminates_without_a_wild_close(wired: Any) -> None:
    host, _fake = wired
    proc = _ConPTYProcess(
        pid=0, process_handle=0, stdin_handle=0, stdout_handle=0, pc_handle=0
    )
    proc.terminate()
    assert host.closed_pseudoconsoles == []
    assert proc.returncode == 1


@windows_only
def test_a_failing_pseudoconsole_close_still_closes_the_process_handle(
    wired: Any,
) -> None:
    """``_close`` used to close the pseudoconsole and the process handle inside
    one ``with suppress(Exception)``. A throwing ClosePseudoConsole skipped
    CloseHandle — and the field was zeroed below regardless, so nothing could
    ever close it: one leaked kernel object per failed teardown.
    """
    host, fake = wired
    proc, handles = _open_process(host)
    fake.kernel32.function("ClosePseudoConsole").set_error(OSError("already gone"))

    proc.kill()

    assert proc.returncode == 1
    assert host.is_closed(handles["process"]), "the process handle was still leaked"
    assert proc._ph == 0


@windows_only
def test_the_streams_are_closed_even_when_one_of_them_throws(wired: Any) -> None:
    host, _fake = wired
    proc, handles = _open_process(host)

    def boom() -> None:
        raise OSError("stdin close failed")

    proc.stdin.close = boom  # type: ignore[method-assign]
    proc.kill()

    assert host.is_closed(handles["stdout"]), "one bad stream must not strand the other"
    assert proc.returncode == 1


@windows_only
def test_terminating_never_touches_the_desktop(wired: Any) -> None:
    # The whole point of the double: a teardown that reached user32 would be
    # acting on the owner's session.
    host, fake = wired
    proc, _handles = _open_process(host)
    fake.log.clear()
    proc.kill()
    assert all(rec.dll == "kernel32" for rec in fake.log)


@windows_only
@pytest.mark.asyncio
async def test_a_spawned_session_round_trips_a_command_through_the_double() -> None:
    """End to end on the doubles: spawn, write a command, read the reply, kill.

    Uses the module's own override hook, which is how the session layer is meant
    to be tested without a real console.
    """
    host = FakeConsoleHost()
    with install_fake_win32(console=host):
        _pty_in, stdin_h = _new_pipe(host)
        stdout_h, _pty_out = _new_pipe(host)
        host.CreateProcessW()
        proc = _ConPTYProcess(
            pid=host.pid,
            process_handle=host.process_handle,
            stdin_handle=stdin_h,
            stdout_handle=stdout_h,
            pc_handle=0,
        )

        async def override(argv: list[str], *, cwd: Any = None, env: Any = None) -> Any:
            assert argv == ["cmd.exe"]
            return proc

        spawn_conpty._override = override  # type: ignore[attr-defined]
        got = await spawn_conpty(["cmd.exe"])
        assert got is proc

        got.stdin.write(b"ver\r\n")
        await got.stdin.drain()
        host.feed(stdout_h, b"Microsoft Windows [Version 10.0]\r\n")
        assert await got.stdout.read() == b"Microsoft Windows [Version 10.0]\r\n"

        got.kill()

    assert host.written(stdin_h) == b"ver\r\n"
    assert host.terminated == [(host.process_handle, 1)]
    assert host.double_closed == []


def test_ctypes_is_imported_lazily_so_the_module_loads_anywhere() -> None:
    # Every ctypes/wintypes import sits inside a function. A module-level one
    # would make `import remedy.execution.host` fail outright on Linux and macOS,
    # where the pipe-based session is the supported path.
    assert not hasattr(conpty, "ctypes")
    assert not hasattr(conpty, "wintypes")


@pytest.mark.asyncio
async def test_creating_the_coroutine_does_not_yet_touch_windows() -> None:
    # The guards live in the coroutine body, so merely building it must be inert
    # — the session builds and discards these on the pipe fallback path.
    windll = FakeWinDLL()
    with install_fake_win32(windll=windll, platform="linux"):
        coro = spawn_conpty(["x"])
        assert asyncio.iscoroutine(coro)
        assert len(windll.log) == 0
        coro.close()
