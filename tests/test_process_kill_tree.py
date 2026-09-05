"""Process control through ``remedy_core``: hidden spawn, wait and kill-tree.

Every child here is the test's own (``cmd`` / ``python``) and is killed
before the test ends; nothing touches the owner's processes.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import time

import pytest

from remedy.execution import process as P


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_kill_process_tree_no_longer_shells_out():
    source = inspect.getsource(P)
    assert "taskkill" not in source
    assert "CREATE_NO_WINDOW" not in [name for name in dir(P) if name.isupper()]


def test_popen_hidden_fail_closed_on_host_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

    if sys.platform not in ("win32", "linux"):
        pytest.skip("soft pipe refuse is win32/linux")
    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        P.popen_hidden(["x"], creationflags=0x200, close_fds=True)
    assert raised.value.status == STATUS_UNSUPPORTED


def test_popen_hidden_fail_closed_off_host_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft CREATE_NO_WINDOW Popen is retired on every platform (Rule 5)."""
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

    monkeypatch.setattr(P, "require_process_host", lambda: None)
    with pytest.raises(HostError) as raised:
        P.popen_hidden(["x"], creationflags=0x200, close_fds=True)
    assert raised.value.status == STATUS_UNSUPPORTED
    assert raised.value.function == "popen_hidden"


def test_harness_soft_popen_merges_creationflags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test double may soft-merge flags; production process.py must not."""
    from tests.harness import process_soft as soft

    seen: dict = {}

    def fake_popen(args, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(soft.subprocess, "Popen", fake_popen)
    soft.soft_popen_hidden(["x"], creationflags=0x200, close_fds=True)
    assert seen["close_fds"] is True
    expected = 0x200 | (subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert seen["creationflags"] == expected


def test_hidden_flags_survive_a_win32_mock_without_windows_attrs(
    monkeypatch: pytest.MonkeyPatch,
):
    """POSIX interpreters lack CREATE_NO_WINDOW / STARTUPINFO; mocked win32 must not crash."""
    monkeypatch.setattr(P.sys, "platform", "win32")
    for name in ("CREATE_NO_WINDOW", "STARTUPINFO", "STARTF_USESHOWWINDOW", "SW_HIDE"):
        if hasattr(subprocess, name):
            monkeypatch.delattr(P.subprocess, name, raising=False)
    assert P.hidden_creationflags() == 0
    assert P.hidden_startupinfo() is None
    assert P.hidden_subprocess_kwargs() == {"creationflags": 0}


def test_kill_process_tree_kills_a_plain_child_everywhere():
    if sys.platform == "win32":
        from remedy.core.computer import host_binding as H
        from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

        try:
            H._lib()
        except (NativeRuntimeUnavailableError, OSError, AttributeError):
            pytest.skip("remedy_core required for Windows kill-tree")
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **P.hidden_subprocess_kwargs(),
    )
    try:
        assert proc.poll() is None
        P.kill_process_tree(proc)
        assert proc.wait(timeout=10) is not None
        # Idempotent on an exited process.
        P.kill_process_tree(proc)
        P.kill_process_tree(None)
    finally:
        if proc.poll() is None:
            proc.kill()


def _descendants(root: int) -> set[int]:
    """Descendant pids by toolhelp (Windows only); the assertions' own reader."""
    import ctypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
    entry = Entry()
    entry.dwSize = ctypes.sizeof(entry)
    parents: dict[int, int] = {}
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    found: set[int] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        for pid, ppid in parents.items():
            if ppid == parent and pid not in found and pid != root:
                found.add(pid)
                frontier.append(pid)
    return found


def _alive(pid: int) -> bool:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


windows_with_core = pytest.mark.skipif(
    sys.platform != "win32" or not __import__("remedy.core.computer.host_binding", fromlist=["available"]).available(),
    reason="remedy_core process host is Windows-only and needs the built DLL",
)


@windows_with_core
def test_kill_process_tree_reaches_grandchildren_of_spawn_hidden():
    child = P.spawn_hidden(["cmd", "/c", "cmd /c ping -n 40 127.0.0.1 > nul"])
    try:
        assert _wait_until(lambda: len(_descendants(child.pid)) >= 2)
        tree = _descendants(child.pid)
        P.kill_process_tree(child)
        assert child.wait(10.0) is not None
        assert _wait_until(lambda: not any(_alive(pid) for pid in tree))
    finally:
        with __import__("contextlib").suppress(Exception):
            child.close()


@windows_with_core
def test_spawn_hidden_wait_and_kill_tree_over_the_zig_exports():
    with P.spawn_hidden(["cmd", "/c", "cmd /c ping -n 40 127.0.0.1 > nul"]) as child:
        assert child.pid > 0 and child.handle
        assert child.poll() is None
        assert P.wait(child, 0.1) is None
        assert _wait_until(lambda: len(_descendants(child.pid)) >= 2)
        tree = _descendants(child.pid)
        child.kill_tree()
        assert child.wait(5.0) is not None
        assert child.returncode is not None
        assert _wait_until(lambda: not any(_alive(pid) for pid in tree))
        P.kill_process_tree(child)  # idempotent on a HiddenProcess
    assert child.handle == 0
    with pytest.raises(ValueError):
        P.wait(child, 0)


@windows_with_core
def test_spawn_hidden_returns_exit_codes_and_honours_env(tmp_path):
    with P.spawn_hidden(
        ["cmd", "/c", "exit %REMEDY_TEST_CODE%"],
        cwd=tmp_path,
        env={"REMEDY_TEST_CODE": "3", "SystemRoot": "C:\\Windows"},
    ) as child:
        assert child.wait(10.0) == 3
        assert child.returncode == 3


@windows_with_core
def test_leaving_the_with_block_ends_a_running_tree():
    with P.spawn_hidden(["cmd", "/c", "ping -n 40 127.0.0.1 > nul"]) as child:
        pid = child.pid
        assert _wait_until(lambda: len(_descendants(pid)) >= 1)
        tree = _descendants(pid)
    assert _wait_until(lambda: not _alive(pid) and not any(_alive(p) for p in tree))


@pytest.mark.skipif(sys.platform == "win32", reason="the unsupported path is for other hosts")
def test_spawn_hidden_reports_unsupported_off_windows():
    from remedy.core.computer import host_binding
    from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

    with pytest.raises((host_binding.HostError, NativeRuntimeUnavailableError)):
        P.spawn_hidden([sys.executable, "-c", "pass"])
