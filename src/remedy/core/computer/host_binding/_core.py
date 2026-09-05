"""ctypes Status / free / prototypes for ``remedy_core`` (ABI 5).

Internal. Import the public surface from ``remedy.core.computer.host_binding``.
"""
from __future__ import annotations

import ctypes
import sys
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
    "remedy_core_looks_like_powershell": (
        [c_char_p, c_size_t, POINTER(c_uint8)],
        c_int32,
    ),
    "remedy_core_rewrite_posix_argv": (
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
    "remedy_core_process_exec_capture_authorized": (
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
            c_uint32,
            POINTER(c_uint32),
            POINTER(c_uint8),
            POINTER(_BytePtr),
            POINTER(c_size_t),
            POINTER(_BytePtr),
            POINTER(c_size_t),
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
