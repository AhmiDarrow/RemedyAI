"""ctypes prototypes for the ``remedy_core`` host surface (ABI 4).

This is the one place Python describes the C ABI declared in
``native/zig/include/remedy_core.h``. Every function here is a thin call into
the library: it marshals arguments, checks the status, frees buffers the
library allocated and returns plain Python values. Policy lives in the
callers (``desktop_win``, ``desktop_uia``, ``desktop_linux``,
``execution.process``).

Windows: host + UIA. Linux: host (X11/XTest) + AT-SPI a11y snapshot.
``host_op_prepare`` and ``translate_posix_to_host`` are portable. Other
platforms: host/UIA/a11y calls report :data:`STATUS_UNSUPPORTED`
(:class:`HostError`).
"""

from __future__ import annotations

import ctypes
import json
import sys
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
    except NativeRuntimeUnavailableError:
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
    (``{op, scratch_dir?, project_path?}`` or a bare HostOp).
    :func:`remedy.execution.host.runner.prepare_host_op` routes structured ops
    through this binding (``raw`` still uses Python ``prepare_host_command``).
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
