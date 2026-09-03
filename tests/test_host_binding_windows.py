"""The ``remedy_core`` host surface, exercised through its Python binding.

Reads only: these tests enumerate windows and monitors, capture the screen,
encode PNGs and spawn/kill their own child processes. Nothing here injects
input, writes the clipboard, moves focus or opens an application.

Skipped off Windows and when the DLL is not built (``zig build
-Doptimize=ReleaseSafe`` in ``native/zig``, or ``REMEDY_NATIVE_CORE_LIB``).
"""

from __future__ import annotations

import ctypes
import struct
import sys
import time
import zlib
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as H

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not H.available(),
    reason="remedy_core host surface is Windows-only and needs the built DLL",
)


@pytest.fixture(autouse=True)
def _sandbox_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))


def _png_dimensions(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    width, height, depth, colour = struct.unpack(">IIBB", png[16:26])
    assert (depth, colour) == (8, 2), "8-bit truecolour"
    return width, height


def _png_inflated_len(png: bytes) -> int:
    offset = 8
    idat = b""
    saw_iend = False
    while offset + 12 <= len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        tag = png[offset + 4 : offset + 8]
        data = png[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])[0]
        assert zlib.crc32(tag + data) & 0xFFFFFFFF == crc
        if tag == b"IDAT":
            idat += data
        if tag == b"IEND":
            saw_iend = True
        offset += 12 + length
    assert saw_iend and offset == len(png)
    return len(zlib.decompress(idat))


# --- capture and png ----------------------------------------------------------


def test_virtual_screen_capture_encodes_to_a_valid_png_at_the_reported_size():
    shot = H.capture_virtual_screen(3)
    left, top, width, height = H.virtual_screen_rect()
    assert (shot.width, shot.height, shot.left, shot.top) == (width, height, left, top)
    assert shot.stride == (width * 3 + 3) & ~3
    assert len(shot.pixels) == shot.stride * height
    png = H.encode_png(shot.pixels, shot.width, shot.height, shot.stride, 3)
    assert _png_dimensions(png) == (width, height)
    assert _png_inflated_len(png) == height * (1 + width * 3)


def test_bgra_capture_and_region_share_the_same_geometry():
    bgra = H.capture_virtual_screen(4)
    assert bgra.stride == bgra.width * 4
    assert len(bgra.pixels) == bgra.stride * bgra.height
    png = H.encode_png(bgra.pixels, bgra.width, bgra.height, bgra.stride, 4)
    assert _png_dimensions(png) == (bgra.width, bgra.height)
    region = H.capture_region(bgra.left, bgra.top, 24, 10, 3)
    assert region.stride == 72 and len(region.pixels) == 720
    # Pixels agree between the full capture and the region capture of its corner.
    full_row = bgra.pixels[: 24 * 4]
    region_row = region.pixels[: 24 * 3]
    assert [full_row[i * 4] for i in range(24)] == [region_row[i * 3] for i in range(24)]


def test_encode_png_accepts_bytearray_and_swaps_bgr_to_rgb():
    raw = bytearray([0, 0, 255, 0, 255, 0, 0, 0])  # red, green + 2 pad bytes
    png = H.encode_png(raw, 2, 1, 8, 3)
    offset = png.index(b"IDAT")
    length = struct.unpack(">I", png[offset - 4 : offset])[0]
    scanline = zlib.decompress(png[offset + 4 : offset + 4 + length])
    assert list(scanline) == [0, 255, 0, 0, 0, 255, 0]


def test_encode_png_rejects_a_buffer_that_is_too_small():
    with pytest.raises(H.HostError) as info:
        H.encode_png(b"\x00" * 5, 2, 1, 6, 3)
    assert info.value.status == H.STATUS_INVALID_ARGUMENT


# --- monitors and windows -------------------------------------------------------


def test_list_monitors_reports_scale_and_one_primary():
    monitors = H.list_monitors()
    assert monitors
    keys = {"index", "left", "top", "right", "bottom", "width", "height", "primary", "scale"}
    for monitor in monitors:
        assert keys <= set(monitor)
        assert monitor["width"] > 0 and monitor["height"] > 0
        assert 1.0 <= monitor["scale"] <= 4.0
    assert sum(1 for m in monitors if m["primary"]) == 1


def test_list_windows_returns_the_documented_shape():
    windows = H.list_windows(limit=50)
    assert isinstance(windows, list) and len(windows) <= 50
    keys = {"hwnd", "title", "class", "pid", "bounds", "width", "height", "visible", "minimized"}
    for window in windows:
        assert keys <= set(window)
        assert window["hwnd"] > 0 and window["pid"] > 0
        assert window["title"].strip() == window["title"] and window["title"]
        assert set(window["bounds"]) == {"left", "top", "right", "bottom"}
        assert window["width"] >= 8 and window["height"] >= 8
        assert window["visible"] is True
        assert H.window_class(window["hwnd"]) == window["class"]
        assert H.window_rect(window["hwnd"]) == tuple(
            window["bounds"][k] for k in ("left", "top", "right", "bottom")
        )
    if len(windows) > 1:
        assert len(H.list_windows(limit=1)) == 1


def test_foreground_and_child_lookups_do_not_raise():
    hwnd, title = H.foreground_window()
    assert isinstance(hwnd, int) and isinstance(title, str)
    if hwnd:
        # A class substring that no window uses finds nothing; an empty one matches any child.
        assert H.find_child_hwnd(hwnd, "no-such-class-remedy-test") == 0
    with pytest.raises(H.HostError) as info:
        H.window_rect(0)
    assert info.value.status == H.STATUS_INVALID_ARGUMENT


def test_print_window_yields_a_png_of_a_real_window(tmp_path: Path):
    console = int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    candidates = [console] if console else []
    candidates += [w["hwnd"] for w in H.list_windows(limit=20) if not w["minimized"]]
    if not candidates:
        pytest.skip("no capturable window on this desktop")
    for hwnd in candidates[:6]:
        try:
            shot = H.print_window(hwnd, 3)
        except H.HostError:
            continue
        png = H.encode_png(shot.pixels, shot.width, shot.height, shot.stride, 3)
        assert _png_dimensions(png) == (shot.width, shot.height)
        assert _png_inflated_len(png) == shot.height * (1 + shot.width * 3)
        (tmp_path / "window.png").write_bytes(png)
        return
    pytest.fail("no window accepted PrintWindow")


# --- clipboard (read only) ------------------------------------------------------


def test_clipboard_read_does_not_raise():
    text = H.clipboard_get_text()
    assert isinstance(text, str)


# --- processes --------------------------------------------------------------------


class _Toolhelp:
    """Minimal toolhelp reader for the assertions (the binding is the code under test)."""

    class _Entry(ctypes.Structure):
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

    @classmethod
    def parents(cls) -> dict[int, int]:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
        entry = cls._Entry()
        entry.dwSize = ctypes.sizeof(entry)
        out: dict[int, int] = {}
        try:
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return out

    @classmethod
    def descendants(cls, root: int) -> set[int]:
        parents = cls.parents()
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
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) != 0
    finally:
        kernel32.CloseHandle(handle)


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_spawn_hidden_then_kill_tree_leaves_no_descendant():
    # ``timeout`` refuses to run without console stdin (a hidden process has
    # none), so ``ping -n`` is the wait; the tree shape cmd -> cmd -> child is
    # what the plan's ``cmd /c "cmd /c ..."`` describes.
    pid, handle = H.process_spawn_hidden(["cmd", "/c", "cmd /c ping -n 40 127.0.0.1 > nul"])
    try:
        assert _wait_until(lambda: len(_Toolhelp.descendants(pid)) >= 2)
        tree = _Toolhelp.descendants(pid)
        assert H.process_wait(handle, 0) is None, "the tree is still running"

        H.process_kill_tree(pid)

        assert H.process_wait(handle, 5000) is not None
        assert _wait_until(lambda: not any(_alive(p) for p in tree))
        assert not _alive(pid)
        # Killing an already-dead tree is not an error.
        H.process_kill_tree(pid)
    finally:
        H.process_close(handle)


def test_closing_the_handle_ends_a_running_tree_through_the_job():
    pid, handle = H.process_spawn_hidden(["cmd", "/c", "ping -n 40 127.0.0.1 > nul"])
    assert _wait_until(lambda: len(_Toolhelp.descendants(pid)) >= 1)
    tree = _Toolhelp.descendants(pid)
    H.process_close(handle)
    assert _wait_until(lambda: not _alive(pid) and not any(_alive(p) for p in tree))


def test_spawn_hidden_honours_cwd_env_and_exit_codes(tmp_path: Path):
    pid, handle = H.process_spawn_hidden(
        ["cmd", "/c", "exit %REMEDY_TEST_CODE%"],
        cwd=str(tmp_path),
        env={"REMEDY_TEST_CODE": "9", "SystemRoot": "C:\\Windows"},
    )
    try:
        assert pid > 0
        assert H.process_wait(handle, 10000) == 9
    finally:
        H.process_close(handle)


def test_spawn_hidden_rejects_an_empty_argv_and_kill_tree_rejects_pid_zero():
    with pytest.raises(H.HostError) as spawn:
        H.process_spawn_hidden([])
    assert spawn.value.status == H.STATUS_INVALID_ARGUMENT
    with pytest.raises(H.HostError) as kill:
        H.process_kill_tree(0)
    assert kill.value.status == H.STATUS_INVALID_ARGUMENT


def test_host_error_carries_the_win32_code_for_a_failed_call():
    with pytest.raises(H.HostError) as info:
        H.print_window(0xFFFF_FFF1, 3)  # not a window
    assert info.value.status == H.STATUS_OPERATION_FAILED
    assert info.value.os_error == 1400  # ERROR_INVALID_WINDOW_HANDLE
    assert "1400" in str(info.value)
