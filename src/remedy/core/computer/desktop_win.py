"""Windows desktop — thin wrappers over ``host_binding`` (no ctypes).

Shared SoM / keys / shot / paste / hold / snapshot policy: :mod:`desktop_common`.
Launch: :mod:`desktop_launch`. UAC/dialog/webview: :mod:`guidance`.
:class:`HostError` propagates (fail closed).
"""

from __future__ import annotations

import contextlib
import functools
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from remedy.core.computer import desktop_common as C
from remedy.core.computer import guidance as G
from remedy.core.computer import host_binding as H
from remedy.core.computer.desktop_launch import (
    _open_app_is_protocol_or_url,
    is_text_document_path,
    open_app,
    open_url,
    refuse_os_open_text_document,
)

PASTE_THRESHOLD = C.PASTE_THRESHOLD
_draw_marks_on_bgr = C.draw_marks_on_bgr
purge_old_shots = C.purge_old_shots
resolve_key_combo = C.resolve_key_combo
_F = TypeVar("_F", bound=Callable[..., Any])


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Desktop computer use requires Windows")


def _win(fn: _F) -> _F:
    @functools.wraps(fn)
    def wrap(*args: Any, **kwargs: Any) -> Any:
        _require_windows()
        return fn(*args, **kwargs)

    return wrap  # type: ignore[return-value]


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


def _write_png_bgr(
    path: Path, width: int, height: int, raw: bytes | bytearray | memoryview, stride: int, *, bytes_per_pixel: int = 3
) -> None:
    C.write_png_bgr(path, width, height, raw, stride, bytes_per_pixel=bytes_per_pixel)


def detect_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cands = C.pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)
    for c in cands:
        c.pop("source", None)
    return cands


def screenshot_png(path: Path | None = None, *, marks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    raw, stride, width, height, left, top = _capture_virtual_screen()
    return C.finalize_shot(
        raw, stride, width, height, path=path, prefix="desk", origin_x=left, origin_y=top, marks=marks
    )


@_win
def screenshot_region_png(
    x: int, y: int, width: int, height: int, *, path: Path | None = None, scale: float = 1.0
) -> dict[str, Any]:
    origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
    bx, by, rw, rh, sc = C.clip_region_to_virtual(
        x, y, width, height, scale=scale, origin_x=origin_x, origin_y=origin_y, full_w=full_w, full_h=full_h
    )
    crop = H.capture_region(origin_x + bx, origin_y + by, rw, rh, 3)
    return C.finalize_shot(
        crop.pixels, crop.stride, rw, rh, path=path, prefix="region",
        origin_x=origin_x + bx, origin_y=origin_y + by, purge=False,
        extra={"requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc}},
    )


@_win
def move_mouse(x: int, y: int) -> None:
    H.mouse_move(int(x), int(y))


@_win
def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    code = C.MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))


@_win
def drag(x1: int, y1: int, x2: int, y2: int, *, steps: int = 12) -> None:
    H.mouse_drag(int(x1), int(y1), int(x2), int(y2), max(2, int(steps)))


@_win
def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    H.mouse_scroll(int(x), int(y), int(dx or 0), int(dy or 0))


@_win
def type_text(
    text: str, *, abort_check: Callable[[], bool] | None = None, chars_typed: list[int] | None = None
) -> int:
    return C.type_text_chars(text, abort_check=abort_check, chars_typed=chars_typed)


@_win
def press_key(key: str) -> None:
    vks = resolve_key_combo(key)
    if vks:
        H.key_combo(vks)


@_win
def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    return H.list_windows(max(1, int(limit)))


def find_remedy_desktop_hwnd() -> int | None:
    hit = C.find_title_hwnd(list_windows(limit=80), exact="Remedy Desktop", prefix="Remedy Desktop")
    return hit[0] if hit else None


def _window_class(hwnd: int) -> str:
    with contextlib.suppress(Exception):
        return H.window_class(int(hwnd))
    return ""


def detect_system_prompt() -> dict[str, Any]:
    return G.detect_system_prompt(foreground_info=foreground_window_info, window_class=_window_class)


def find_dialog_window() -> dict[str, Any] | None:
    return G.find_dialog_window(
        foreground_info=foreground_window_info, window_class=_window_class, list_windows=list_windows
    )


def desktop_snapshot(limit: int = 40, *, mode: str = "auto", hwnd: int | None = None) -> list[dict[str, Any]]:
    def _controls(root: int | None, cap: int) -> list[dict[str, Any]]:
        return list(G.uia_control_snapshot(hwnd=root, max_elements=cap) or [])

    return C.compose_desktop_snapshot(
        limit=limit, mode=mode, hwnd=hwnd, list_windows_fn=list_windows, controls_fn=_controls,
        foreground_hwnd_fn=lambda: int(H.foreground_window()[0] or 0) or None,
        controls_modes=frozenset({"controls", "uia", "deep"}), merge_cell=1,
    )


@_win
def print_window_png(hwnd: int, path: Path | None = None) -> dict[str, Any]:
    shot = H.print_window(int(hwnd), 3)
    return C.finalize_shot(
        shot.pixels, shot.stride, shot.width, shot.height, path=path, prefix="hwnd",
        origin_x=shot.left, origin_y=shot.top, purge=False,
        extra={"hwnd": int(hwnd), "method": "PrintWindow"},
    )


@_win
def find_child_hwnd(
    parent: int, *, class_name: str | None = None, title_substr: str | None = None
) -> int | None:
    return H.find_child_hwnd(int(parent), class_name or "", title_substr or "") or None


def find_webview_host_hwnd() -> int | None:
    _require_windows()
    return G.find_webview_host_hwnd(list_windows=list_windows, find_child=find_child_hwnd)


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    hwnd = el.get("hwnd")
    if hwnd:
        with contextlib.suppress(Exception):
            focus_window(int(hwnd))
            time.sleep(0.05)
    click(int(el.get("x") or 0), int(el.get("y") or 0), button=button, clicks=clicks)


@_win
def focus_window(hwnd: int) -> bool:
    return H.focus_window(int(hwnd))


def foreground_window_info() -> dict[str, Any]:
    out: dict[str, Any] = {"hwnd": 0, "title": ""}
    with contextlib.suppress(Exception):
        hwnd, title = H.foreground_window()
        if hwnd:
            out = {"hwnd": int(hwnd), "title": title}
    return out


@_win
def manage_window(
    hwnd: int, verb: str, *, x: int | None = None, y: int | None = None,
    width: int | None = None, height: int | None = None,
) -> dict[str, Any]:
    return C.manage_window_dispatch(
        hwnd, verb, x=x, y=y, width=width, height=height,
        window_rect_fn=H.window_rect, manage_fn=H.manage_window,
    )


@_win
def get_clipboard_text() -> str:
    return H.clipboard_get_text()


@_win
def set_clipboard_text(text: str) -> bool:
    H.clipboard_set_text(str(text or ""))
    return True


def type_text_fast(
    text: str, *, abort_check: Callable[[], bool] | None = None, chars_typed: list[int] | None = None
) -> dict[str, Any]:
    return C.type_text_fast(
        text, type_text=type_text, get_clipboard=get_clipboard_text, set_clipboard=set_clipboard_text,
        press_key=press_key, host_error=H.HostError, abort_check=abort_check, chars_typed=chars_typed,
    )


@_win
def press_hold(
    x: int, y: int, *, hold_ms: int = 2600, abort_check: Callable[[], bool] | None = None
) -> dict[str, Any]:
    return C.press_hold(
        x, y, mouse_move=H.mouse_move,
        mouse_down=lambda: H.mouse_button(H.MOUSE_LEFT, True),
        mouse_up=lambda: H.mouse_button(H.MOUSE_LEFT, False),
        hold_ms=hold_ms, abort_check=abort_check,
    )


def focus_window_by_title(title_substr: str) -> dict[str, Any] | None:
    hit = C.find_title_hwnd(list_windows(limit=80), substr=title_substr)
    if not hit:
        return None
    hwnd, title = hit
    focus_window(hwnd)
    return {"hwnd": hwnd, "title": title}


@_win
def list_monitors() -> list[dict[str, Any]]:
    return C.annotate_monitors(
        H.list_monitors(), remedy_hwnd=find_remedy_desktop_hwnd(), window_rect_fn=H.window_rect
    )


def screenshot_monitor_png(monitor_index: int = 0, *, path: Path | None = None) -> dict[str, Any]:
    return C.screenshot_monitor_from_list(
        list_monitors(), monitor_index, path=path, region_shot=screenshot_region_png, full_shot=screenshot_png
    )
