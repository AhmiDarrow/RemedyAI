"""uia C ABI surface for ``remedy_core``.

Internal. Public imports go through ``host_binding``.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import (
    c_size_t,
    c_uint8,
)
from typing import Any

from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

from ._core import (
    STATUS_UNSUPPORTED,
    _BytePtr,
    _check,
    _lib,
    _utf8,
)
from ._json import take_json

# --- UI Automation (ABI 3) ---------------------------------------------------



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
    result = take_json(library, ptr, length)
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
    result = take_json(library, ptr, length)
    return result if isinstance(result, dict) else None


def uia_focused_element() -> dict[str, Any] | None:
    library = _lib()
    ptr, length = _BytePtr(), c_size_t()
    _check(
        library,
        "uia_focused_element",
        library.remedy_core_uia_focused_element(ctypes.byref(ptr), ctypes.byref(length)),
    )
    result = take_json(library, ptr, length)
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
    result = take_json(library, ptr, length)
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
    result = take_json(library, ptr, length)
    if not isinstance(result, list):
        return []
    return result
