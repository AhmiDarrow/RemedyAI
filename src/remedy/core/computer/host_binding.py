"""ctypes prototypes for the ``remedy_core`` host surface (ABI 5).

This is the one place Python describes the C ABI declared in
``native/zig/include/remedy_core.h``. Every function here is a thin call into
the library: it marshals arguments, checks the status, frees buffers the
library allocated and returns plain Python values. Production process and
ConPTY spawns use the authorized ABI (policy + capability tokens + write-jail
/ workdir roots); the unsigned spawn exports remain for low-level tests only.

Windows: host + UIA + ConPTY. Linux: host (X11/XTest) + AT-SPI a11y snapshot.
``host_op_prepare`` (structured ops + command-string prepare),
``translate_posix_to_host``, and diagnose/dialect/stretch are portable.
``native()`` selects ``desktop_win`` / ``desktop_linux`` (no ``desktop_os`` twin).
Other platforms: host/UIA/a11y/ConPTY calls report
:data:`STATUS_UNSUPPORTED` (:class:`HostError`).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from ctypes import (
    POINTER,
    c_char_p,
    c_int32,
    c_size_t,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
    c_void_p,
)
from pathlib import Path
from types import ModuleType
from typing import Any, NamedTuple

from remedy.runtime.native_runtime import NativeRuntimeUnavailableError, core_library

STATUS_OK = 0
STATUS_INVALID_ARGUMENT = 1
STATUS_ACCESS_DENIED = 2
STATUS_OPERATION_FAILED = 3
STATUS_UNSUPPORTED = 4

_STATUS_NAMES = {
    STATUS_OK: "ok",
    STATUS_INVALID_ARGUMENT: "invalid argument",
    STATUS_ACCESS_DENIED: "access denied",
    STATUS_OPERATION_FAILED: "operation failed",
    STATUS_UNSUPPORTED: "unsupported on this platform",
}

MOUSE_LEFT = 0
MOUSE_RIGHT = 1
MOUSE_MIDDLE = 2

WINDOW_MINIMIZE = 0
WINDOW_MAXIMIZE = 1
WINDOW_RESTORE = 2
WINDOW_CLOSE = 3
WINDOW_MOVE_RESIZE = 4

#: ``remedy_core_process_wait`` timeout meaning "forever".
WAIT_FOREVER = 0xFFFFFFFF

_BytePtr = POINTER(c_uint8)


class HostError(RuntimeError):
    """A host call did not return ``REMEDY_CORE_OK``."""

    def __init__(self, function: str, status: int, os_error: int = 0) -> None:
        self.function = function
        self.status = status
        self.os_error = os_error
        detail = _STATUS_NAMES.get(status, f"status {status}")
        suffix = f" (Win32 error {os_error})" if os_error else ""
        super().__init__(f"{function}: {detail}{suffix}")


class Capture(NamedTuple):
    """Raw pixel rows from a capture call; ``pixels`` is BGR or BGRA."""

    pixels: bytes
    stride: int
    width: int
    height: int
    left: int
    top: int


_PROTOTYPES: dict[str, tuple[list[Any], Any]] = {
    "remedy_core_free": ([_BytePtr, c_size_t], None),
    "remedy_core_last_os_error": ([], c_uint32),
    "remedy_core_dpi_awareness_enable": ([], c_int32),
    "remedy_core_virtual_screen_rect": (
        [POINTER(c_int32), POINTER(c_int32), POINTER(c_int32), POINTER(c_int32)],
        c_int32,
    ),
    "remedy_core_list_monitors": ([POINTER(_BytePtr), POINTER(c_size_t)], c_int32),
    "remedy_core_capture_virtual_screen": (
        [
            c_uint32,
            POINTER(_BytePtr),
            POINTER(c_size_t),
            POINTER(c_int32),
            POINTER(c_int32),
            POINTER(c_size_t),
            POINTER(c_int32),
            POINTER(c_int32),
        ],
        c_int32,
    ),
    "remedy_core_capture_region": (
        [
            c_int32,
            c_int32,
            c_int32,
            c_int32,
            c_uint32,
            POINTER(_BytePtr),
            POINTER(c_size_t),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_print_window": (
        [
            c_uint64,
            c_uint32,
            POINTER(_BytePtr),
            POINTER(c_size_t),
            POINTER(c_int32),
            POINTER(c_int32),
            POINTER(c_size_t),
            POINTER(c_int32),
            POINTER(c_int32),
        ],
        c_int32,
    ),
    "remedy_core_encode_png": (
        [
            c_void_p,
            c_size_t,
            c_int32,
            c_int32,
            c_size_t,
            c_uint32,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_mouse_move": ([c_int32, c_int32], c_int32),
    "remedy_core_mouse_click": ([c_int32, c_int32, c_uint32, c_uint32], c_int32),
    "remedy_core_mouse_button": ([c_uint32, c_uint8], c_int32),
    "remedy_core_mouse_drag": ([c_int32, c_int32, c_int32, c_int32, c_uint32], c_int32),
    "remedy_core_mouse_scroll": ([c_int32, c_int32, c_int32, c_int32], c_int32),
    "remedy_core_type_text": ([c_char_p, c_size_t, c_uint32], c_int32),
    "remedy_core_key_combo": ([POINTER(c_uint16), c_size_t], c_int32),
    "remedy_core_key_hold": ([c_uint16, c_uint32], c_int32),
    "remedy_core_vk_key_scan": ([c_uint32, POINTER(c_int32)], c_int32),
    "remedy_core_list_windows": ([c_uint32, POINTER(_BytePtr), POINTER(c_size_t)], c_int32),
    "remedy_core_window_class": ([c_uint64, POINTER(_BytePtr), POINTER(c_size_t)], c_int32),
    "remedy_core_window_rect": (
        [c_uint64, POINTER(c_int32), POINTER(c_int32), POINTER(c_int32), POINTER(c_int32)],
        c_int32,
    ),
    "remedy_core_foreground_window": (
        [POINTER(c_uint64), POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_focus_window": ([c_uint64, POINTER(c_uint8)], c_int32),
    "remedy_core_manage_window": (
        [c_uint64, c_uint32, c_int32, c_int32, c_int32, c_int32],
        c_int32,
    ),
    "remedy_core_find_child_hwnd": (
        [c_uint64, c_char_p, c_size_t, c_char_p, c_size_t, POINTER(c_uint64)],
        c_int32,
    ),
    "remedy_core_clipboard_get_text": ([POINTER(_BytePtr), POINTER(c_size_t)], c_int32),
    "remedy_core_clipboard_set_text": ([c_char_p, c_size_t], c_int32),
    "remedy_core_process_spawn_hidden": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(c_uint32),
            POINTER(c_uint64),
        ],
        c_int32,
    ),
    "remedy_core_process_wait": (
        [c_uint64, c_uint32, POINTER(c_uint8), POINTER(c_uint32)],
        c_int32,
    ),
    "remedy_core_process_kill_tree": ([c_uint32], c_int32),
    "remedy_core_process_close": ([c_uint64], c_int32),
    "remedy_core_uia_available": ([POINTER(c_uint8)], c_int32),
    "remedy_core_uia_control_snapshot": (
        [c_uint64, c_uint32, c_uint8, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_uia_read_window_text": (
        [c_uint64, c_uint32, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_uia_focused_element": ([POINTER(_BytePtr), POINTER(c_size_t)], c_int32),
    "remedy_core_uia_element_action": (
        [
            c_uint64,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_a11y_snapshot": (
        [c_uint32, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_host_op_prepare": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_translate_posix_to_host": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_conpty_available": ([POINTER(c_uint8)], c_int32),
    "remedy_core_conpty_spawn": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_uint16,
            c_uint16,
            POINTER(c_uint32),
            POINTER(c_uint64),
        ],
        c_int32,
    ),
    "remedy_core_conpty_write": (
        [c_uint64, c_char_p, c_size_t, POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_conpty_read": (
        [c_uint64, _BytePtr, c_size_t, POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_conpty_poll": (
        [c_uint64, POINTER(c_uint8), POINTER(c_uint32)],
        c_int32,
    ),
    "remedy_core_conpty_kill": ([c_uint64], c_int32),
    "remedy_core_conpty_close_pipe": ([c_uint64, c_uint32], c_int32),
    "remedy_core_conpty_close": ([c_uint64], c_int32),
    "remedy_core_security_set_signing_key": ([c_void_p, c_size_t], c_int32),
    "remedy_core_security_clear_signing_key": ([], c_int32),
    "remedy_core_policy_hash_argv": (
        [c_char_p, c_size_t, c_void_p, c_size_t],
        c_int32,
    ),
    "remedy_core_capability_issue": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_void_p,
            c_size_t,
            c_uint64,
            c_uint64,
            c_uint64,
            c_void_p,
            c_size_t,
            c_void_p,
            c_size_t,
        ],
        c_int32,
    ),
    "remedy_core_process_spawn_authorized": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_void_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_uint8,
            c_uint64,
            POINTER(c_uint32),
            POINTER(c_uint64),
        ],
        c_int32,
    ),
    "remedy_core_conpty_spawn_authorized": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_uint16,
            c_uint16,
            c_void_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_uint8,
            c_uint64,
            POINTER(c_uint32),
            POINTER(c_uint64),
        ],
        c_int32,
    ),
    "remedy_core_write_jail_set_roots": ([c_char_p, c_size_t], c_int32),
    "remedy_core_write_jail_clear": ([], c_int32),
    "remedy_core_write_jail_check_path": (
        [c_char_p, c_size_t, c_char_p, c_size_t],
        c_int32,
    ),
    "remedy_core_write_jail_check_spawn": (
        [c_char_p, c_size_t, c_char_p, c_size_t],
        c_int32,
    ),
    "remedy_core_shell_chain_expand": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_shell_chain_execute": (
        [
            c_char_p,
            c_size_t,
            POINTER(c_uint8),
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_host_session_argv": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_host_session_open": (
        [c_char_p, c_size_t, POINTER(c_uint64)],
        c_int32,
    ),
    "remedy_core_host_session_open_authorized": (
        [
            c_char_p,
            c_size_t,
            c_void_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_uint8,
            c_uint64,
            POINTER(c_uint64),
        ],
        c_int32,
    ),
    "remedy_core_host_session_run": (
        [
            c_uint64,
            c_char_p,
            c_size_t,
            c_uint32,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_host_session_cwd": (
        [c_uint64, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_host_session_close": ([c_uint64], c_int32),
    "remedy_core_host_session_wrap": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_host_session_split": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_diagnose_host_failure": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_dialect_probe": (
        [c_char_p, c_size_t, c_uint8, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_dialect_load": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_dialect_record_success": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_dialect_format_line": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_stretch_home": (
        [c_char_p, c_size_t, c_uint8, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_stretch_load": (
        [c_char_p, c_size_t, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_stretch_needs": (
        [c_char_p, c_size_t, c_int32, POINTER(_BytePtr), POINTER(c_size_t)],
        c_int32,
    ),
    "remedy_core_stretch_format_line": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
    "remedy_core_stretch_format_whoami": (
        [
            c_char_p,
            c_size_t,
            c_char_p,
            c_size_t,
            POINTER(_BytePtr),
            POINTER(c_size_t),
        ],
        c_int32,
    ),
}


_bound: Any = None


def _lib() -> Any:
    """The library with every prototype declared, bound once per load."""
    global _bound
    library = core_library()
    if _bound is not library:
        for name, (argtypes, restype) in _PROTOTYPES.items():
            function = getattr(library, name)
            function.argtypes = argtypes
            function.restype = restype
        _bound = library
    return library


def available() -> bool:
    """True when ``remedy_core`` loads and this platform implements the host."""
    if sys.platform != "win32":
        return False
    try:
        _lib()
    except (NativeRuntimeUnavailableError, OSError, AttributeError):
        return False
    return True


def _check(library: Any, function: str, status: int) -> None:
    if status == STATUS_OK:
        return
    os_error = 0
    if status == STATUS_OPERATION_FAILED:
        os_error = int(library.remedy_core_last_os_error())
    raise HostError(function, status, os_error)


def _take(library: Any, ptr: Any, length: Any) -> bytes:
    """Copy a library-allocated buffer into Python and free it."""
    size = int(length.value)
    address = ctypes.cast(ptr, c_void_p).value if ptr else None
    if address is None or size == 0:
        if ptr and size:
            library.remedy_core_free(ptr, size)
        return b""
    try:
        return ctypes.string_at(address, size)
    finally:
        library.remedy_core_free(ptr, size)


def _utf8(text: str) -> bytes:
    return text.encode("utf-8", "surrogatepass")


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogatepass")


def _readable(data: bytes | bytearray | memoryview) -> tuple[Any, int]:
    """A ctypes view over *data* without copying it."""
    if isinstance(data, bytes):
        return ctypes.cast(c_char_p(data), c_void_p), len(data)
    view = memoryview(data)
    if not view.c_contiguous:
        view = memoryview(view.tobytes())
    length = view.nbytes
    buffer = (c_uint8 * length).from_buffer(view) if not view.readonly else (
        c_uint8 * length
    ).from_buffer_copy(view)
    return ctypes.cast(buffer, c_void_p), length


# --- DPI and monitors --------------------------------------------------------


def dpi_awareness_enable() -> None:
    library = _lib()
    _check(library, "dpi_awareness_enable", library.remedy_core_dpi_awareness_enable())


def virtual_screen_rect() -> tuple[int, int, int, int]:
    """``(left, top, width, height)`` of the virtual screen in physical pixels."""
    library = _lib()
    left, top, width, height = c_int32(), c_int32(), c_int32(), c_int32()
    _check(
        library,
        "virtual_screen_rect",
        library.remedy_core_virtual_screen_rect(
            ctypes.byref(left), ctypes.byref(top), ctypes.byref(width), ctypes.byref(height)
        ),
    )
    return left.value, top.value, width.value, height.value


def list_monitors() -> list[dict[str, Any]]:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "list_monitors",
        library.remedy_core_list_monitors(ctypes.byref(ptr), ctypes.byref(length)),
    )
    result: list[dict[str, Any]] = json.loads(_take(library, ptr, length) or b"[]")
    return result


# --- capture -----------------------------------------------------------------


def capture_virtual_screen(bytes_per_pixel: int = 3) -> Capture:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    width, height, stride = c_int32(), c_int32(), c_size_t()
    left, top = c_int32(), c_int32()
    _check(
        library,
        "capture_virtual_screen",
        library.remedy_core_capture_virtual_screen(
            bytes_per_pixel,
            ctypes.byref(ptr),
            ctypes.byref(length),
            ctypes.byref(width),
            ctypes.byref(height),
            ctypes.byref(stride),
            ctypes.byref(left),
            ctypes.byref(top),
        ),
    )
    return Capture(
        _take(library, ptr, length), stride.value, width.value, height.value, left.value, top.value
    )


def capture_region(
    left: int, top: int, width: int, height: int, bytes_per_pixel: int = 3
) -> Capture:
    library = _lib()
    ptr, length, stride = _BytePtr(), c_size_t(), c_size_t()
    _check(
        library,
        "capture_region",
        library.remedy_core_capture_region(
            left,
            top,
            width,
            height,
            bytes_per_pixel,
            ctypes.byref(ptr),
            ctypes.byref(length),
            ctypes.byref(stride),
        ),
    )
    return Capture(_take(library, ptr, length), stride.value, width, height, left, top)


def print_window(hwnd: int, bytes_per_pixel: int = 3) -> Capture:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    width, height, stride = c_int32(), c_int32(), c_size_t()
    left, top = c_int32(), c_int32()
    _check(
        library,
        "print_window",
        library.remedy_core_print_window(
            hwnd,
            bytes_per_pixel,
            ctypes.byref(ptr),
            ctypes.byref(length),
            ctypes.byref(width),
            ctypes.byref(height),
            ctypes.byref(stride),
            ctypes.byref(left),
            ctypes.byref(top),
        ),
    )
    return Capture(
        _take(library, ptr, length), stride.value, width.value, height.value, left.value, top.value
    )


def encode_png(
    pixels: bytes | bytearray | memoryview,
    width: int,
    height: int,
    stride: int,
    bytes_per_pixel: int = 3,
) -> bytes:
    """8-bit RGB PNG from BGR (3) or BGRA (4) rows. Portable (works off Windows)."""
    library = _lib()
    source, source_len = _readable(pixels)
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "encode_png",
        library.remedy_core_encode_png(
            source,
            source_len,
            width,
            height,
            stride,
            bytes_per_pixel,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length)


# --- input -------------------------------------------------------------------


def mouse_move(x: int, y: int) -> None:
    library = _lib()
    _check(library, "mouse_move", library.remedy_core_mouse_move(x, y))


def mouse_click(x: int, y: int, button: int = MOUSE_LEFT, clicks: int = 1) -> None:
    library = _lib()
    _check(library, "mouse_click", library.remedy_core_mouse_click(x, y, button, clicks))


def mouse_button(button: int, pressed: bool) -> None:
    library = _lib()
    _check(library, "mouse_button", library.remedy_core_mouse_button(button, 1 if pressed else 0))


def mouse_drag(x1: int, y1: int, x2: int, y2: int, steps: int = 12) -> None:
    library = _lib()
    _check(library, "mouse_drag", library.remedy_core_mouse_drag(x1, y1, x2, y2, steps))


def mouse_scroll(x: int, y: int, dx: int = 0, dy: int = 0) -> None:
    library = _lib()
    _check(library, "mouse_scroll", library.remedy_core_mouse_scroll(x, y, dx, dy))


def type_text(text: str, per_char_delay_ms: int = 5) -> None:
    library = _lib()
    raw = _utf8(text)
    _check(library, "type_text", library.remedy_core_type_text(raw, len(raw), per_char_delay_ms))


def key_combo(vks: Sequence[int]) -> None:
    library = _lib()
    array = (c_uint16 * len(vks))(*[int(vk) & 0xFFFF for vk in vks])
    _check(library, "key_combo", library.remedy_core_key_combo(array, len(vks)))


def key_hold(vk: int, hold_ms: int) -> None:
    library = _lib()
    _check(library, "key_hold", library.remedy_core_key_hold(vk & 0xFFFF, hold_ms))


def vk_key_scan(codepoint: int) -> int:
    """``VkKeyScanW`` result: VK in the low byte, shift state in the high byte, -1 if none."""
    library = _lib()
    scan = c_int32()
    _check(library, "vk_key_scan", library.remedy_core_vk_key_scan(codepoint, ctypes.byref(scan)))
    return scan.value


# --- windows -----------------------------------------------------------------


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "list_windows",
        library.remedy_core_list_windows(max(1, limit), ctypes.byref(ptr), ctypes.byref(length)),
    )
    result: list[dict[str, Any]] = json.loads(_take(library, ptr, length) or b"[]")
    return result


def window_class(hwnd: int) -> str:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "window_class",
        library.remedy_core_window_class(hwnd, ctypes.byref(ptr), ctypes.byref(length)),
    )
    return _text(_take(library, ptr, length))


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` from ``GetWindowRect``."""
    library = _lib()
    left, top, right, bottom = c_int32(), c_int32(), c_int32(), c_int32()
    _check(
        library,
        "window_rect",
        library.remedy_core_window_rect(
            hwnd, ctypes.byref(left), ctypes.byref(top), ctypes.byref(right), ctypes.byref(bottom)
        ),
    )
    return left.value, top.value, right.value, bottom.value


def foreground_window() -> tuple[int, str]:
    """``(hwnd, title)``; hwnd is 0 when no window is foreground."""
    library = _lib()
    hwnd, ptr, length = c_uint64(), _BytePtr(), c_size_t()
    _check(
        library,
        "foreground_window",
        library.remedy_core_foreground_window(
            ctypes.byref(hwnd), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return hwnd.value, _text(_take(library, ptr, length))


def focus_window(hwnd: int) -> bool:
    library = _lib()
    focused = c_uint8()
    _check(library, "focus_window", library.remedy_core_focus_window(hwnd, ctypes.byref(focused)))
    return bool(focused.value)


def manage_window(
    hwnd: int, action: int, x: int = 0, y: int = 0, width: int = 0, height: int = 0
) -> None:
    library = _lib()
    _check(
        library,
        "manage_window",
        library.remedy_core_manage_window(hwnd, action, x, y, width, height),
    )


def find_child_hwnd(parent: int, class_substr: str = "", title_substr: str = "") -> int:
    """First matching descendant handle, or 0."""
    library = _lib()
    class_raw, title_raw = _utf8(class_substr), _utf8(title_substr)
    found = c_uint64()
    _check(
        library,
        "find_child_hwnd",
        library.remedy_core_find_child_hwnd(
            parent, class_raw, len(class_raw), title_raw, len(title_raw), ctypes.byref(found)
        ),
    )
    return found.value


# --- clipboard ---------------------------------------------------------------


def clipboard_get_text() -> str:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "clipboard_get_text",
        library.remedy_core_clipboard_get_text(ctypes.byref(ptr), ctypes.byref(length)),
    )
    return _text(_take(library, ptr, length))


def clipboard_set_text(text: str) -> None:
    library = _lib()
    raw = _utf8(text)
    _check(library, "clipboard_set_text", library.remedy_core_clipboard_set_text(raw, len(raw)))


# --- processes ---------------------------------------------------------------


def process_spawn_hidden(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    """``(pid, handle)`` of a hidden, job-bound process. Close the handle with
    :func:`process_close`; closing it ends the whole tree."""
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = _utf8(json.dumps({str(k): str(v) for k, v in env.items()})) if env is not None else b""
    pid, handle = c_uint32(), c_uint64()
    _check(
        library,
        "process_spawn_hidden",
        library.remedy_core_process_spawn_hidden(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return pid.value, handle.value


def process_wait(handle: int, timeout_ms: int = WAIT_FOREVER) -> int | None:
    """Exit code once the process ends, None when the timeout elapsed."""
    library = _lib()
    exited, code = c_uint8(), c_uint32()
    _check(
        library,
        "process_wait",
        library.remedy_core_process_wait(
            handle, timeout_ms, ctypes.byref(exited), ctypes.byref(code)
        ),
    )
    return code.value if exited.value else None


def process_kill_tree(pid: int) -> None:
    library = _lib()
    _check(library, "process_kill_tree", library.remedy_core_process_kill_tree(pid))


def process_close(handle: int) -> None:
    library = _lib()
    _check(library, "process_close", library.remedy_core_process_close(handle))


# --- UI Automation (ABI 3) ---------------------------------------------------


def _take_json(library: Any, ptr: Any, length: Any) -> Any:
    """Parse a library JSON buffer; ``null`` becomes ``None``."""
    raw = _take(library, ptr, length)
    if not raw or raw == b"null":
        return None
    return json.loads(raw)


def uia_available() -> bool:
    """True when ``CoCreateInstance(CUIAutomation)`` succeeds on this thread."""
    if sys.platform != "win32":
        return False
    try:
        library = _lib()
    except NativeRuntimeUnavailableError:
        return False
    flag = c_uint8()
    status = library.remedy_core_uia_available(ctypes.byref(flag))
    if status == STATUS_UNSUPPORTED:
        return False
    _check(library, "uia_available", status)
    return bool(flag.value)


def uia_control_snapshot(
    hwnd: int = 0,
    max_elements: int = 80,
    preferred_only: bool = True,
) -> list[dict[str, Any]] | None:
    """Control tree as a list of dicts, or ``None`` when UIA found nothing."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "uia_control_snapshot",
        library.remedy_core_uia_control_snapshot(
            hwnd,
            max_elements,
            1 if preferred_only else 0,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    result = _take_json(library, ptr, length)
    if result is None:
        return None
    if not isinstance(result, list):
        return None
    return result or None


def uia_read_window_text(hwnd: int, max_chars: int = 12000) -> dict[str, Any] | None:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "uia_read_window_text",
        library.remedy_core_uia_read_window_text(
            hwnd, max_chars, ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = _take_json(library, ptr, length)
    return result if isinstance(result, dict) else None


def uia_focused_element() -> dict[str, Any] | None:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "uia_focused_element",
        library.remedy_core_uia_focused_element(ctypes.byref(ptr), ctypes.byref(length)),
    )
    result = _take_json(library, ptr, length)
    return result if isinstance(result, dict) else None


def uia_element_action(
    hwnd: int,
    name: str,
    *,
    role: str = "",
    action: str = "invoke",
    text: str = "",
) -> dict[str, Any]:
    library = _lib()
    name_raw, role_raw = _utf8(name), _utf8(role)
    action_raw, text_raw = _utf8(action), _utf8(text)
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "uia_element_action",
        library.remedy_core_uia_element_action(
            hwnd,
            name_raw,
            len(name_raw),
            role_raw,
            len(role_raw),
            action_raw,
            len(action_raw),
            text_raw,
            len(text_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    result = _take_json(library, ptr, length)
    if isinstance(result, dict):
        return result
    return {"ok": False, "message": f"UIA element {name!r} not found in hwnd={hwnd} (re-snapshot?)"}


def a11y_snapshot(limit: int = 40) -> list[dict[str, Any]]:
    """Linux AT-SPI clickables as dicts; empty list when unavailable / non-Linux."""
    try:
        library = _lib()
    except NativeRuntimeUnavailableError:
        return []
    ptr, length = _BytePtr(), c_size_t()
    status = library.remedy_core_a11y_snapshot(
        max(1, int(limit)), ctypes.byref(ptr), ctypes.byref(length)
    )
    if status == STATUS_UNSUPPORTED:
        return []
    _check(library, "a11y_snapshot", status)
    result = _take_json(library, ptr, length)
    if not isinstance(result, list):
        return []
    return result


# --- Host Command IR prepare (ABI 4) -----------------------------------------


def host_op_prepare(
    op: Mapping[str, Any] | None = None,
    *,
    scratch_dir: str | None = None,
    project_path: str | None = None,
    raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_host_op_prepare``; return a PreparedCommand dict.

    Pass either a HostOp mapping as *op*, or a full request object as *raw*
    (``{op, scratch_dir?, project_path?}``, ``{command, host?, ...}``, or a
    bare HostOp). :func:`remedy.execution.host.runner.prepare_host_op` and
    :func:`remedy.execution.host.runner.prepare_host_command` both route here.
    """
    if raw is not None:
        payload: dict[str, Any] = dict(raw)
    else:
        if not isinstance(op, Mapping):
            raise TypeError("host_op_prepare requires op= or raw=")
        payload = {"op": dict(op)}
        if scratch_dir:
            payload["scratch_dir"] = scratch_dir
        if project_path:
            payload["project_path"] = project_path
    encoded = _utf8(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_op_prepare",
        library.remedy_core_host_op_prepare(
            encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = _take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("host_op_prepare", STATUS_OPERATION_FAILED)
    return result


def translate_posix_to_host(
    command: str,
    *,
    host: str | None = None,
    rg_path: str | None = None,
    python_exe: str | None = None,
    pwsh_exe: str | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_translate_posix_to_host``; return a TranslateResult dict."""
    payload: dict[str, Any] = {"command": command or ""}
    if host is not None:
        payload["host"] = host
    if rg_path:
        payload["rg_path"] = rg_path
    if python_exe:
        payload["python_exe"] = python_exe
    if pwsh_exe:
        payload["pwsh_exe"] = pwsh_exe
    encoded = _utf8(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "translate_posix_to_host",
        library.remedy_core_translate_posix_to_host(
            encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    result = _take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("translate_posix_to_host", STATUS_OPERATION_FAILED)
    return result


# --- ConPTY (ABI 5) ----------------------------------------------------------

CONPTY_PIPE_STDIN = 0
CONPTY_PIPE_STDOUT = 1


def conpty_available() -> bool:
    """True when ``CreatePseudoConsole`` is exported on this Windows host."""
    if sys.platform != "win32":
        return False
    try:
        library = _lib()
    except NativeRuntimeUnavailableError:
        return False
    flag = c_uint8()
    status = library.remedy_core_conpty_available(ctypes.byref(flag))
    if status == STATUS_UNSUPPORTED:
        return False
    _check(library, "conpty_available", status)
    return bool(flag.value)


def conpty_spawn(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    cols: int = 120,
    rows: int = 40,
) -> tuple[int, int]:
    """``(pid, handle)`` for a ConPTY-attached child. Close with :func:`conpty_close`."""
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    pid, handle = c_uint32(), c_uint64()
    _check(
        library,
        "conpty_spawn",
        library.remedy_core_conpty_spawn(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            int(cols) & 0xFFFF,
            int(rows) & 0xFFFF,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return int(pid.value), int(handle.value)


def conpty_write(handle: int, data: bytes | bytearray | memoryview) -> int:
    """Write bytes to the ConPTY stdin pipe; return the count written."""
    library = _lib()
    raw = bytes(data)
    written = c_size_t(0)
    _check(
        library,
        "conpty_write",
        library.remedy_core_conpty_write(handle, raw, len(raw), ctypes.byref(written)),
    )
    return int(written.value)


def conpty_read(handle: int, max_len: int = 4096) -> bytes:
    """Read up to *max_len* bytes from the ConPTY stdout pipe (empty at EOF)."""
    library = _lib()
    size = max(0, int(max_len))
    if size == 0:
        return b""
    buf = (c_uint8 * size)()
    got = c_size_t(0)
    _check(
        library,
        "conpty_read",
        library.remedy_core_conpty_read(
            handle, ctypes.cast(buf, _BytePtr), size, ctypes.byref(got)
        ),
    )
    n = int(got.value)
    if n <= 0:
        return b""
    return bytes(buf[:n])


def conpty_poll(handle: int) -> int | None:
    """Exit code once the child has left STILL_ACTIVE; otherwise ``None``."""
    library = _lib()
    exited, code = c_uint8(), c_uint32()
    _check(
        library,
        "conpty_poll",
        library.remedy_core_conpty_poll(handle, ctypes.byref(exited), ctypes.byref(code)),
    )
    return int(code.value) if exited.value else None


def conpty_kill(handle: int) -> None:
    library = _lib()
    _check(library, "conpty_kill", library.remedy_core_conpty_kill(handle))


def conpty_close_pipe(handle: int, which: int) -> None:
    """Close stdin (0) or stdout (1) pipe end. Idempotent."""
    library = _lib()
    _check(
        library,
        "conpty_close_pipe",
        library.remedy_core_conpty_close_pipe(handle, int(which) & 0xFFFFFFFF),
    )


def conpty_close(handle: int) -> None:
    """Release pipes, pseudoconsole and process handle; invalidates *handle*."""
    library = _lib()
    _check(library, "conpty_close", library.remedy_core_conpty_close(handle))


# --- ABI 5 additive: policy + capability tokens ------------------------------

CAPABILITY_TOKEN_SIZE = 169
PROCESS_SPAWN_RIGHT = 1 << 2
OWNER_CHECKPOINT_RIGHT = 1 << 5
DEFAULT_SPAWN_SUBJECT = "agent:remedy"
DEFAULT_SPAWN_SCOPE = "workspace:local"
_TOKEN_LIFETIME_MS = 60_000

_signing_key_ready = False
_HOST_SIGNING_FILENAME = "host_signing_key"
_HOST_SIGNING_POSIX = "host_signing_key.posix"


def security_set_signing_key(key: bytes | bytearray | memoryview) -> None:
    """Install the HMAC signing key (first 32 bytes). Test or secret-store material only."""
    global _signing_key_ready
    raw = bytes(key)
    if len(raw) < 32:
        raise ValueError("signing key must be at least 32 bytes")
    library = _lib()
    buf = (c_uint8 * 32).from_buffer_copy(raw[:32])
    _check(
        library,
        "security_set_signing_key",
        library.remedy_core_security_set_signing_key(buf, 32),
    )
    _signing_key_ready = True


def security_clear_signing_key() -> None:
    global _signing_key_ready
    library = _lib()
    _check(library, "security_clear_signing_key", library.remedy_core_security_clear_signing_key())
    _signing_key_ready = False


def _remedy_auth_dir(home: str | os.PathLike[str] | None = None) -> Path:
    from remedy.home import default_home

    root = Path(home) if home is not None and str(home).strip() else default_home()
    return root / "auth"


def _encode_host_signing_key(raw: bytes) -> bytes:
    import base64
    import json

    key = bytes(raw)[:32]
    if sys.platform == "win32":
        from remedy.interfaces.secret_store import _dpapi_available, _dpapi_protect

        if not _dpapi_available():
            raise RuntimeError("DPAPI required to persist host signing key on Windows")
        sealed = _dpapi_protect(key)
        envelope = {
            "v": 2,
            "kind": "host_signing_key",
            "dpapi": base64.b64encode(sealed).decode("ascii"),
        }
        return (json.dumps(envelope, indent=2) + "\n").encode("utf-8")
    return (base64.b64encode(key).decode("ascii") + "\n").encode("utf-8")


def _decode_host_signing_key(data: bytes) -> bytes | None:
    import base64
    import json

    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            outer = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(outer, dict):
            return None
        kind = outer.get("kind")
        if kind not in (None, "host_signing_key"):
            return None
        b64 = outer.get("dpapi") or ""
        try:
            from remedy.interfaces.secret_store import _dpapi_unprotect

            plain = _dpapi_unprotect(base64.b64decode(str(b64)))
            return plain[:32] if len(plain) >= 32 else None
        except Exception:
            return None
    try:
        plain = base64.b64decode(text)
    except Exception:
        return None
    return plain[:32] if len(plain) >= 32 else None


def load_or_create_host_signing_key(
    home: str | os.PathLike[str] | None = None,
) -> bytes:
    """Load the durable Zig HMAC key from the secret store, or mint + persist one.

    Never logs key material. Env ``REMEDY_SPAWN_SIGNING_KEY`` (hex or raw) wins
    for tests/operators and is not written back unless persistence is desired.
    """
    from remedy.core.atomic_json import write_bytes_atomic

    auth = _remedy_auth_dir(home)
    primary = auth / _HOST_SIGNING_FILENAME
    posix = auth / _HOST_SIGNING_POSIX
    for path in (primary, posix):
        try:
            if path.is_file():
                key = _decode_host_signing_key(path.read_bytes())
                if key is not None:
                    return key
        except OSError:
            continue
    import base64

    key = os.urandom(32)
    try:
        auth.mkdir(parents=True, exist_ok=True)
        write_bytes_atomic(primary, _encode_host_signing_key(key), mode=0o600)
        from remedy.interfaces.secret_store import _harden_path

        _harden_path(primary, is_dir=False)
        # Posix sidecar (base64) so WSL can read a Windows-minted key.
        write_bytes_atomic(
            posix,
            (base64.b64encode(key).decode("ascii") + "\n").encode("utf-8"),
            mode=0o600,
        )
        _harden_path(posix, is_dir=False)
    except OSError as exc:
        raise RuntimeError(f"unable to persist host signing key under {auth}") from exc
    return key


def ensure_spawn_signing_key() -> None:
    """Ensure the Zig HMAC signing key is installed from the secret store."""
    global _signing_key_ready
    if _signing_key_ready:
        return
    env_key = os.environ.get("REMEDY_SPAWN_SIGNING_KEY", "")
    if env_key:
        raw = (
            bytes.fromhex(env_key)
            if all(c in "0123456789abcdefABCDEF" for c in env_key) and len(env_key) >= 64
            else env_key.encode("utf-8")
        )
    else:
        raw = load_or_create_host_signing_key()
    security_set_signing_key(raw)


def policy_hash_argv(argv: Sequence[str]) -> bytes:
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    out = (c_uint8 * 32)()
    _check(
        library,
        "policy_hash_argv",
        library.remedy_core_policy_hash_argv(argv_raw, len(argv_raw), out, 32),
    )
    return bytes(out)


def capability_issue(
    *,
    operation_hash: bytes,
    rights_bits: int = PROCESS_SPAWN_RIGHT,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    issued_at_ms: int | None = None,
    expires_at_ms: int | None = None,
    nonce: bytes | None = None,
) -> bytes:
    """Issue a v2 capability token bound to *operation_hash* (32 bytes)."""
    ensure_spawn_signing_key()
    if len(operation_hash) != 32:
        raise ValueError("operation_hash must be 32 bytes")
    now = int(time.time() * 1000) if issued_at_ms is None else int(issued_at_ms)
    exp = now + _TOKEN_LIFETIME_MS if expires_at_ms is None else int(expires_at_ms)
    nonce_raw = os.urandom(16) if nonce is None else bytes(nonce)
    if len(nonce_raw) != 16:
        raise ValueError("nonce must be 16 bytes")
    library = _lib()
    op_buf = (c_uint8 * 32).from_buffer_copy(operation_hash)
    nonce_buf = (c_uint8 * 16).from_buffer_copy(nonce_raw)
    out = (c_uint8 * CAPABILITY_TOKEN_SIZE)()
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    _check(
        library,
        "capability_issue",
        library.remedy_core_capability_issue(
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            op_buf,
            32,
            int(rights_bits),
            now,
            exp,
            nonce_buf,
            16,
            out,
            CAPABILITY_TOKEN_SIZE,
        ),
    )
    return bytes(out)


def issue_process_spawn_token(
    argv: Sequence[str],
    *,
    owner_checkpoint: bool = False,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
) -> tuple[bytes, int]:
    """Return ``(token, now_ms)`` for an authorized spawn of *argv*."""
    digest = policy_hash_argv(argv)
    now = int(time.time() * 1000)
    rights = PROCESS_SPAWN_RIGHT
    if owner_checkpoint:
        rights |= OWNER_CHECKPOINT_RIGHT
    token = capability_issue(
        operation_hash=digest,
        rights_bits=rights,
        subject=subject,
        scope=scope,
        issued_at_ms=now,
        expires_at_ms=now + _TOKEN_LIFETIME_MS,
    )
    return token, now


def write_jail_set_roots(roots: Sequence[str] | None) -> None:
    """Install write roots for authorized spawn (empty / None = Full, no workdir jail)."""
    library = _lib()
    payload = _utf8(json.dumps([str(r) for r in (roots or [])]))
    _check(
        library,
        "write_jail_set_roots",
        library.remedy_core_write_jail_set_roots(payload, len(payload)),
    )


def write_jail_clear() -> None:
    library = _lib()
    _check(library, "write_jail_clear", library.remedy_core_write_jail_clear())


def write_jail_check_path(path: str, cwd: str | None = None) -> None:
    """Raise :class:`HostError` with ACCESS_DENIED when *path* escapes the jail."""
    library = _lib()
    path_raw = _utf8(str(path))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    _check(
        library,
        "write_jail_check_path",
        library.remedy_core_write_jail_check_path(
            path_raw, len(path_raw), cwd_raw, len(cwd_raw)
        ),
    )


def write_jail_check_spawn(argv: Sequence[str], cwd: str | None = None) -> None:
    """Raise :class:`HostError` when argv/cwd would be denied by the write jail."""
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    _check(
        library,
        "write_jail_check_spawn",
        library.remedy_core_write_jail_check_spawn(
            argv_raw, len(argv_raw), cwd_raw, len(cwd_raw)
        ),
    )


def shell_chain_expand(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Call ``remedy_core_shell_chain_expand``; return ``{hops: null|list}``.

    Missing symbol → :class:`HostError`.
    """
    encoded = _utf8(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
    try:
        library = _lib()
        fn = library.remedy_core_shell_chain_expand
    except (AttributeError, NativeRuntimeUnavailableError) as exc:
        raise HostError("shell_chain_expand", STATUS_UNSUPPORTED) from exc
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "shell_chain_expand",
        fn(encoded, len(encoded), ctypes.byref(ptr), ctypes.byref(length)),
    )
    result = _take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("shell_chain_expand", STATUS_OPERATION_FAILED)
    return result


def shell_chain_execute(
    payload: Mapping[str, Any],
    *,
    abort_flag: c_uint8 | None = None,
) -> dict[str, Any]:
    """Call ``remedy_core_shell_chain_execute``; return the execute result dict.

    *abort_flag* is an optional ``ctypes.c_uint8`` polled by Zig (non-zero aborts).
    Missing symbol → :class:`HostError`.
    """
    encoded = _utf8(json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")))
    try:
        library = _lib()
        fn = library.remedy_core_shell_chain_execute
    except (AttributeError, NativeRuntimeUnavailableError) as exc:
        raise HostError("shell_chain_execute", STATUS_UNSUPPORTED) from exc
    ptr, length = _BytePtr(), c_size_t()
    flag_arg = ctypes.byref(abort_flag) if abort_flag is not None else None
    _check(
        library,
        "shell_chain_execute",
        fn(
            encoded,
            len(encoded),
            flag_arg,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    result = _take_json(library, ptr, length)
    if not isinstance(result, dict):
        raise HostError("shell_chain_execute", STATUS_OPERATION_FAILED)
    return result


def process_spawn_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    write_roots: Sequence[str] | None = None,
) -> tuple[int, int]:
    """Authorized hidden spawn. *argv[0]* must be absolute. No unsigned fallback.

    When *write_roots* is not ``None``, installs those roots for the jail check
    on this spawn (empty sequence = Full / unbound). ``None`` leaves the
    previously installed roots unchanged.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = _utf8(json.dumps({str(k): str(v) for k, v in env.items()})) if env is not None else b""
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    pid, handle = c_uint32(), c_uint64()
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "process_spawn_authorized",
        library.remedy_core_process_spawn_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return pid.value, handle.value


def conpty_spawn_authorized(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    cols: int = 120,
    rows: int = 40,
    token: bytes,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
    owner_confirmed: bool = False,
    now_ms: int | None = None,
    write_roots: Sequence[str] | None = None,
) -> tuple[int, int]:
    """Authorized ConPTY spawn. *argv[0]* must be absolute. No unsigned fallback.

    *write_roots* semantics match :func:`process_spawn_authorized`.
    """
    if write_roots is not None:
        write_jail_set_roots(write_roots)
    library = _lib()
    argv_raw = _utf8(json.dumps([str(a) for a in argv]))
    cwd_raw = _utf8(str(cwd)) if cwd else b""
    env_raw = (
        _utf8(json.dumps({str(k): str(v) for k, v in env.items()}))
        if env is not None
        else b""
    )
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    token_raw = bytes(token)
    pid, handle = c_uint32(), c_uint64()
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "conpty_spawn_authorized",
        library.remedy_core_conpty_spawn_authorized(
            argv_raw,
            len(argv_raw),
            cwd_raw,
            len(cwd_raw),
            env_raw,
            len(env_raw),
            int(cols) & 0xFFFF,
            int(rows) & 0xFFFF,
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(pid),
            ctypes.byref(handle),
        ),
    )
    return int(pid.value), int(handle.value)


# ---- ABI 5 additive: HostSession -------------------------------------------


def host_session_wrap(*, host: str, command: str, sentinel: str) -> str:
    """Zig sentinel wrap — portable protocol helper."""
    payload = _utf8(
        json.dumps(
            {"host": host, "command": command, "sentinel": sentinel},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_wrap",
        library.remedy_core_host_session_wrap(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length)
    data = json.loads(raw.decode("utf-8"))
    return str(data.get("wrapped") or "")


def host_session_split(
    *,
    text: str,
    sentinel: str,
    command: str = "",
    conpty: bool = False,
) -> tuple[int, str]:
    """Zig sentinel split — portable protocol helper."""
    payload = _utf8(
        json.dumps(
            {
                "text": text,
                "sentinel": sentinel,
                "command": command,
                "conpty": bool(conpty),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_split",
        library.remedy_core_host_session_split(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length)
    data = json.loads(raw.decode("utf-8"))
    return int(data.get("exit_code", -1)), str(data.get("body") or "")


def host_session_argv(host: str | None = None) -> list[str]:
    """Argv Zig will authorize for :func:`host_session_open_authorized`."""
    host_name = str(host or ("cmd" if sys.platform == "win32" else "posix"))
    host_raw = _utf8(host_name)
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_argv",
        library.remedy_core_host_session_argv(
            host_raw, len(host_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    data = _take_json(library, ptr, length)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise HostError("host_session_argv", STATUS_OPERATION_FAILED)
    return [str(x) for x in data]


def issue_host_session_token(
    host: str | None = None,
    *,
    owner_checkpoint: bool = False,
    subject: str = DEFAULT_SPAWN_SUBJECT,
    scope: str = DEFAULT_SPAWN_SCOPE,
) -> tuple[bytes, int]:
    """Return ``(token, now_ms)`` for an authorized HostSession open of *host*."""
    return issue_process_spawn_token(
        host_session_argv(host),
        owner_checkpoint=owner_checkpoint,
        subject=subject,
        scope=scope,
    )


def host_session_open(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    use_conpty: bool = False,
) -> int:
    """Open a Zig HostSession (Windows). Returns an opaque handle."""
    body: dict[str, Any] = {"use_conpty": bool(use_conpty)}
    if host:
        body["host"] = host
    if cwd:
        body["cwd"] = cwd
    if env is not None:
        body["env"] = dict(env)
    payload = _utf8(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    handle = c_uint64()
    _check(
        library,
        "host_session_open",
        library.remedy_core_host_session_open(
            payload, len(payload), ctypes.byref(handle)
        ),
    )
    return int(handle.value)


def host_session_open_authorized(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    use_conpty: bool = False,
    token: bytes | bytearray | memoryview,
    subject: str = "agent:remedy",
    scope: str = "workspace:local",
    owner_confirmed: bool = False,
    now_ms: int | None = None,
) -> int:
    """Authorize then open a Zig HostSession (Windows)."""
    body: dict[str, Any] = {"use_conpty": bool(use_conpty)}
    if host:
        body["host"] = host
    if cwd:
        body["cwd"] = cwd
    if env is not None:
        body["env"] = dict(env)
    payload = _utf8(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    library = _lib()
    handle = c_uint64()
    token_raw = bytes(token)
    subject_raw = _utf8(subject)
    scope_raw = _utf8(scope)
    when = int(time.time() * 1000) if now_ms is None else int(now_ms)
    _check(
        library,
        "host_session_open_authorized",
        library.remedy_core_host_session_open_authorized(
            payload,
            len(payload),
            (c_uint8 * len(token_raw)).from_buffer_copy(token_raw),
            len(token_raw),
            subject_raw,
            len(subject_raw),
            scope_raw,
            len(scope_raw),
            1 if owner_confirmed else 0,
            when,
            ctypes.byref(handle),
        ),
    )
    return int(handle.value)


def host_session_run(
    handle: int, command: str, *, timeout_ms: int = 60_000
) -> dict[str, Any]:
    """Run one command in a Zig HostSession; returns a SessionResult dict."""
    library = _lib()
    cmd = _utf8(command)
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_run",
        library.remedy_core_host_session_run(
            c_uint64(handle),
            cmd,
            len(cmd),
            c_uint32(max(1, int(timeout_ms))),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    raw = _take(library, ptr, length)
    return dict(json.loads(raw.decode("utf-8")))


def host_session_cwd(handle: int) -> str:
    """Probe cwd for a Zig HostSession."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "host_session_cwd",
        library.remedy_core_host_session_cwd(
            c_uint64(handle), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def host_session_close(handle: int) -> None:
    """Close a Zig HostSession."""
    if not handle:
        return
    library = _lib()
    _check(
        library,
        "host_session_close",
        library.remedy_core_host_session_close(c_uint64(handle)),
    )


# ---- ABI 5 additive: diagnose / dialect / stretch --------------------------


def diagnose_host_failure(
    *,
    command: str,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 1,
    translated: str = "",
    timed_out: bool = False,
    host: str = "cmd",
) -> dict[str, Any]:
    """Zig host-failure classifier. Returns a diagnosis dict."""
    payload = _utf8(
        json.dumps(
            {
                "command": command,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": int(exit_code),
                "translated": translated,
                "timed_out": bool(timed_out),
                "host": host or "cmd",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "diagnose_host_failure",
        library.remedy_core_diagnose_host_failure(
            payload, len(payload), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_probe(home: str = "", *, persist: bool = False) -> dict[str, Any]:
    """Zig PATH dialect probe. Optional persist writes dialect.json."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_probe",
        library.remedy_core_dialect_probe(
            home_raw,
            len(home_raw),
            1 if persist else 0,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_load(home: str = "") -> dict[str, Any]:
    """Load dialect.json via Zig (heals empty/sidecar fields)."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_load",
        library.remedy_core_dialect_load(
            home_raw, len(home_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_record_success(
    command: str, *, home: str = "", note: str = ""
) -> dict[str, Any]:
    """Record a successful host command into dialect.json."""
    home_raw = _utf8(home or "")
    cmd_raw = _utf8(command or "")
    note_raw = _utf8(note or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_record_success",
        library.remedy_core_dialect_record_success(
            home_raw,
            len(home_raw),
            cmd_raw,
            len(cmd_raw),
            note_raw,
            len(note_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def dialect_format_line(home: str = "", dialect: Mapping[str, Any] | None = None) -> str:
    """One-line host inject from Zig."""
    home_raw = _utf8(home or "")
    dialect_raw = (
        _utf8(json.dumps(dict(dialect), ensure_ascii=False, separators=(",", ":")))
        if dialect is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "dialect_format_line",
        library.remedy_core_dialect_format_line(
            home_raw,
            len(home_raw),
            dialect_raw,
            len(dialect_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def stretch_home(home: str = "", *, force: bool = False) -> dict[str, Any]:
    """Zig first-home stretch; persists home.json (+ dialect)."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_home",
        library.remedy_core_stretch_home(
            home_raw,
            len(home_raw),
            1 if force else 0,
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return dict(json.loads(_take(library, ptr, length).decode("utf-8")))


def stretch_load(home: str = "") -> dict[str, Any] | None:
    """Load home.json via Zig; None when missing."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_load",
        library.remedy_core_stretch_load(
            home_raw, len(home_raw), ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    raw = _take(library, ptr, length).decode("utf-8")
    if raw.strip() == "null":
        return None
    data = json.loads(raw)
    return dict(data) if isinstance(data, dict) else None


def stretch_needs(home: str = "", *, stale_days: int = 14) -> bool:
    """True when census is missing or older than stale_days."""
    home_raw = _utf8(home or "")
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_needs",
        library.remedy_core_stretch_needs(
            home_raw,
            len(home_raw),
            int(stale_days),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8").strip() == "true"


def stretch_format_line(
    home: str = "", census: Mapping[str, Any] | None = None
) -> str:
    """Compact This-home inject line."""
    home_raw = _utf8(home or "")
    census_raw = (
        _utf8(json.dumps(dict(census), ensure_ascii=False, separators=(",", ":")))
        if census is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_format_line",
        library.remedy_core_stretch_format_line(
            home_raw,
            len(home_raw),
            census_raw,
            len(census_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def stretch_format_whoami(
    home: str = "", census: Mapping[str, Any] | None = None
) -> str:
    """Longer /whoami and /stretch block."""
    home_raw = _utf8(home or "")
    census_raw = (
        _utf8(json.dumps(dict(census), ensure_ascii=False, separators=(",", ":")))
        if census is not None
        else b""
    )
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "stretch_format_whoami",
        library.remedy_core_stretch_format_whoami(
            home_raw,
            len(home_raw),
            census_raw,
            len(census_raw),
            ctypes.byref(ptr),
            ctypes.byref(length),
        ),
    )
    return _take(library, ptr, length).decode("utf-8", errors="replace")


def native() -> ModuleType:
    """Return the desktop module for this OS (``desktop_win`` / ``desktop_linux``)."""
    if sys.platform == "win32":
        from remedy.core.computer import desktop_win as win

        return win
    from remedy.core.computer import desktop_linux as linux

    return linux
