"""Windows desktop — thin binding over ``host_binding`` / ``remedy_core``.

Shared SoM / keys / shot policy: :mod:`desktop_policy` (via pixels/keys siblings).
Launch: :mod:`desktop_launch` (via :mod:`desktop_launch_win`). Capture / monitors:
:mod:`desktop_capture_win`. Snapshot / UAC / find: :mod:`desktop_win_policy`.
UIA soft helpers: :mod:`guidance`. :class:`HostError` propagates (fail closed).
"""

from __future__ import annotations

import contextlib
import sys
import time  # noqa: F401 — tests patch desktop_win.time.sleep
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_policy as C
from remedy.core.computer import host_binding as H
from remedy.core.computer.desktop_capture_win import (
    list_monitors,
    print_window_png,
    screenshot_monitor_png,
    screenshot_png,
    screenshot_region_png,
)
from remedy.core.computer.desktop_keys import resolve_key_combo
from remedy.core.computer.desktop_launch_win import (
    _open_app_is_protocol_or_url,
    is_text_document_path,
    open_app,
    open_url,
    refuse_os_open_text_document,
)
from remedy.core.computer.desktop_pixels import (
    detect_ui_candidates,
    purge_old_shots,
)
from remedy.core.computer.desktop_pixels import (
    draw_marks_on_bgr as _draw_marks_on_bgr,
)
from remedy.core.computer.desktop_pixels import (
    write_png_bgr as _write_png_bgr,
)
from remedy.core.computer.desktop_win_policy import (
    click_element,
    desktop_snapshot,
    detect_system_prompt,
    find_dialog_window,
    find_remedy_desktop_hwnd,
    find_webview_host_hwnd,
    focus_window_by_title,
)

PASTE_THRESHOLD = C.PASTE_THRESHOLD

__all__ = [
    "PASTE_THRESHOLD",
    "_capture_virtual_screen",
    "_default_shot_path",
    "_draw_marks_on_bgr",
    "_ensure_dpi_awareness",
    "_open_app_is_protocol_or_url",
    "_remedy_home",
    "_require_windows",
    "_write_png_bgr",
    "click",
    "click_element",
    "desktop_snapshot",
    "detect_system_prompt",
    "detect_ui_candidates",
    "drag",
    "find_child_hwnd",
    "find_dialog_window",
    "find_remedy_desktop_hwnd",
    "find_webview_host_hwnd",
    "focus_window",
    "focus_window_by_title",
    "foreground_window_info",
    "get_clipboard_text",
    "is_text_document_path",
    "list_monitors",
    "list_windows",
    "manage_window",
    "move_mouse",
    "open_app",
    "open_url",
    "press_hold",
    "press_key",
    "print_window_png",
    "purge_old_shots",
    "refuse_os_open_text_document",
    "screenshot_monitor_png",
    "screenshot_png",
    "screenshot_region_png",
    "scroll",
    "set_clipboard_text",
    "type_text",
    "type_text_fast",
]


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Desktop computer use requires Windows")


def _ensure_dpi_awareness() -> None:
    if sys.platform == "win32":
        H.dpi_awareness_enable()


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    _require_windows()
    shot = H.capture_virtual_screen(3)
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def _remedy_home() -> Path:
    return C.remedy_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    return C.default_shot_path(prefix)


def move_mouse(x: int, y: int) -> None:
    _require_windows()
    H.mouse_move(int(x), int(y))


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    _require_windows()
    code = C.MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))


def drag(x1: int, y1: int, x2: int, y2: int, *, steps: int = 12) -> None:
    _require_windows()
    H.mouse_drag(int(x1), int(y1), int(x2), int(y2), max(2, int(steps)))


def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    _require_windows()
    H.mouse_scroll(int(x), int(y), int(dx or 0), int(dy or 0))


def type_text(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> int:
    _require_windows()
    return C.type_text_chars(text, abort_check=abort_check, chars_typed=chars_typed)


def press_key(key: str) -> None:
    _require_windows()
    vks = resolve_key_combo(key)
    if vks:
        H.key_combo(vks)


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    _require_windows()
    return H.list_windows(max(1, int(limit)))


def find_child_hwnd(
    parent: int, *, class_name: str | None = None, title_substr: str | None = None
) -> int | None:
    _require_windows()
    found = H.find_child_hwnd(int(parent), class_name or "", title_substr or "")
    return found or None


def focus_window(hwnd: int) -> bool:
    _require_windows()
    return H.focus_window(int(hwnd))


def foreground_window_info() -> dict[str, Any]:
    out: dict[str, Any] = {"hwnd": 0, "title": ""}
    with contextlib.suppress(H.HostError, OSError, ValueError, TypeError):
        hwnd, title = H.foreground_window()
        if hwnd:
            out = {"hwnd": int(hwnd), "title": title}
    return out


def manage_window(
    hwnd: int,
    verb: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    _require_windows()
    return C.manage_window_dispatch(
        hwnd,
        verb,
        x=x,
        y=y,
        width=width,
        height=height,
        window_rect_fn=H.window_rect,
        manage_fn=H.manage_window,
    )


def get_clipboard_text() -> str:
    """Host failures raise HostError — never soft ``""``."""
    _require_windows()
    return H.clipboard_get_text()


def set_clipboard_text(text: str) -> bool:
    """Host failures raise HostError — never soft ``False``."""
    _require_windows()
    H.clipboard_set_text(str(text or ""))
    return True


def type_text_fast(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> dict[str, Any]:
    """Paste when long; on clipboard HostError fall back to keystrokes."""
    _require_windows()
    return C.run_type_text_fast(
        text,
        type_text=type_text,
        get_clipboard=get_clipboard_text,
        set_clipboard=set_clipboard_text,
        press_key=press_key,
        host_error=H.HostError,
        abort_check=abort_check,
        chars_typed=chars_typed,
    )


def press_hold(
    x: int,
    y: int,
    *,
    hold_ms: int = 2600,
    abort_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _require_windows()
    return C.run_press_hold(
        x,
        y,
        mouse_move=lambda mx, my: H.mouse_move(int(mx), int(my)),
        mouse_down=lambda: H.mouse_button(H.MOUSE_LEFT, True),
        mouse_up=lambda: H.mouse_button(H.MOUSE_LEFT, False),
        hold_ms=hold_ms,
        abort_check=abort_check,
    )
