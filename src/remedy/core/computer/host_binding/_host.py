"""host C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import json
from collections.abc import Mapping, Sequence
from ctypes import (
    c_int32,
    c_size_t,
    c_uint8,
    c_uint16,
    c_uint32,
    c_uint64,
)
from typing import Any

from ._core import (
    MOUSE_LEFT,
    WAIT_FOREVER,
    Capture,
    _BytePtr,
    _check,
    _lib,
    _readable,
    _take,
    _text,
    _utf8,
)

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


def clipboard_get_files() -> list[str]:
    """CF_HDROP paths; empty list when the format is absent."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "clipboard_get_files",
        library.remedy_core_clipboard_get_files(ctypes.byref(ptr), ctypes.byref(length)),
    )
    result: list[Any] = json.loads(_take(library, ptr, length) or b"[]")
    return [str(item) for item in result]


def clipboard_get_image_png() -> bytes:
    """CF_DIB as PNG bytes; empty when absent or unsupported."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "clipboard_get_image_png",
        library.remedy_core_clipboard_get_image_png(
            ctypes.byref(ptr), ctypes.byref(length)
        ),
    )
    return _take(library, ptr, length) or b""


def foreground_detail() -> dict[str, Any]:
    """``{hwnd, title, pid, exe}`` for the foreground window."""
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "foreground_detail",
        library.remedy_core_foreground_detail(ctypes.byref(ptr), ctypes.byref(length)),
    )
    result: dict[str, Any] = json.loads(_take(library, ptr, length) or b"{}")
    return result


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
