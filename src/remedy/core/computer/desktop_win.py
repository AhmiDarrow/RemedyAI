"""Windows desktop — thin binding over ``host_binding`` / ``remedy_core``.

Shared SoM / keys / shot policy: :mod:`desktop_common` (via pixels/keys siblings).
Launch: :mod:`desktop_launch` (via :mod:`desktop_launch_win`). Capture / monitors:
:mod:`desktop_capture_win`. Snapshot / UAC / find: :mod:`desktop_win_policy`.
UIA soft helpers: :mod:`guidance`. :class:`HostError` propagates (fail closed).
"""

from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_common as C
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
    draw_marks_on_bgr as _draw_marks_on_bgr,
    purge_old_shots,
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
_WINDOW_ACTIONS = {
    "minimize": H.WINDOW_MINIMIZE,
    "maximize": H.WINDOW_MAXIMIZE,
    "restore": H.WINDOW_RESTORE,
}


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
    v = (verb or "").strip().lower()
    if not hwnd:
        return {"ok": False, "message": "hwnd required"}
    if v in _WINDOW_ACTIONS:
        H.manage_window(int(hwnd), _WINDOW_ACTIONS[v])
        return {"ok": True, "message": f"{v} hwnd={hwnd}"}
    if v == "close":
        H.manage_window(int(hwnd), H.WINDOW_CLOSE)
        return {
            "ok": True,
            "message": (
                f"Sent close to hwnd={hwnd} (the app may show a save prompt — "
                "snapshot to see it)"
            ),
        }
    if v in ("move", "resize"):
        left, top, right, bottom = H.window_rect(int(hwnd))
        nx = int(x) if x is not None else left
        ny = int(y) if y is not None else top
        nw = int(width) if width is not None else right - left
        nh = int(height) if height is not None else bottom - top
        H.manage_window(int(hwnd), H.WINDOW_MOVE_RESIZE, nx, ny, nw, nh)
        return {"ok": True, "message": f"{v} hwnd={hwnd} → ({nx},{ny}) {nw}x{nh}"}
    return {"ok": False, "message": f"Unknown window verb {verb!r}"}


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
    data = str(text or "")
    if len(data) <= PASTE_THRESHOLD or "\r" in data or "\n" in data:
        n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
        return {"chars": n, "method": "keystrokes"}
    try:
        saved = get_clipboard_text()
    except H.HostError:
        n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
        return {"chars": n, "method": "keystrokes"}
    try:
        try:
            set_clipboard_text(data)
        except H.HostError:
            n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
            return {"chars": n, "method": "keystrokes"}
        press_key("ctrl+v")
        time.sleep(0.15)
        if chars_typed is not None:
            chars_typed[:] = [len(data)]
        return {"chars": len(data), "method": "paste"}
    finally:
        with contextlib.suppress(Exception):
            set_clipboard_text(saved)


def press_hold(
    x: int,
    y: int,
    *,
    hold_ms: int = 2600,
    abort_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    _require_windows()
    H.mouse_move(int(x), int(y))
    time.sleep(0.05)
    H.mouse_button(H.MOUSE_LEFT, True)
    held = 0.0
    step = 0.1
    total = max(0.1, float(hold_ms) / 1000.0)
    try:
        while held < total:
            time.sleep(min(step, total - held))
            held += step
            if abort_check is not None and abort_check():
                break
    finally:
        H.mouse_button(H.MOUSE_LEFT, False)
    return {"held_ms": int(min(held, total) * 1000), "x": x, "y": y}
