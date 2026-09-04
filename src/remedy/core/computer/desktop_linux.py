"""Linux desktop — thin Zig-backed wrappers over ``host_binding``.

Shared SoM / keys / shot / pixel / OCR / paste / hold / snapshot policy:
:mod:`desktop_common`. AT-SPI fetch stays here. Input/capture HostError →
RuntimeError via ``_host_fail`` (fail closed). Clipboard HostError propagates.
"""

from __future__ import annotations

import contextlib
import functools
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from remedy.core.computer import desktop_common as C
from remedy.core.computer import host_binding as H
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

PASTE_THRESHOLD = C.PASTE_THRESHOLD
_LINUX_HANDS_HINT = (
    "needs an X11 display with XTest (DISPLAY set; XWayland works). "
    "Pure Wayland without XWayland is not supported yet"
)
purge_old_shots = C.purge_old_shots
resolve_key_combo = C.resolve_key_combo
_F = TypeVar("_F", bound=Callable[..., Any])


def _require_linux() -> None:
    if sys.platform == "win32":
        raise RuntimeError("desktop_linux is for POSIX desktops")


def _host_fail(need: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(f"Linux {need} failed via remedy_core — {_LINUX_HANDS_HINT}: {exc}")


def _hands(need: str) -> Callable[[_F], _F]:
    def deco(fn: _F) -> _F:
        @functools.wraps(fn)
        def wrap(*args: Any, **kwargs: Any) -> Any:
            _require_linux()
            try:
                return fn(*args, **kwargs)
            except (NativeRuntimeUnavailableError, H.HostError) as exc:
                raise _host_fail(need, exc) from exc

        return wrap  # type: ignore[return-value]

    return deco


def _remedy_home() -> Path:
    return C.remedy_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    return C.default_shot_path(prefix)


def _write_png_bgr(
    path: Path, width: int, height: int, raw: bytes | bytearray | memoryview, stride: int, *, bytes_per_pixel: int = 3
) -> None:
    C.write_png_bgr(path, width, height, raw, stride, bytes_per_pixel=bytes_per_pixel)


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    _require_linux()
    try:
        shot = H.capture_virtual_screen(3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("capture", exc) from exc
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def screenshot_png(path: Path | None = None, *, marks: list[Any] | None = None) -> dict[str, Any]:
    raw, stride, width, height, left, top = _capture_virtual_screen()
    if width < 2 or height < 2:
        raise RuntimeError(f"Linux screenshot failed — {_LINUX_HANDS_HINT}")
    try:
        return C.finalize_shot(
            raw, stride, width, height, path=path, prefix="desk", origin_x=left, origin_y=top,
            marks=marks, require_dict_marks=True, extra={"method": "remedy_core"},
        )
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("screenshot", exc) from exc


@_hands("region screenshot")
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
        extra={"requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc}, "method": "remedy_core"},
    )


def screenshot_monitor_png(index: int, path: Path | None = None) -> dict[str, Any]:
    return C.screenshot_monitor_from_list(
        list_monitors(), index, path=path, region_shot=screenshot_region_png, full_shot=screenshot_png
    )


def print_window_png(hwnd: int | None = None, path: Path | None = None) -> dict[str, Any]:
    _require_linux()
    if not hwnd:
        return screenshot_png(path)
    try:
        shot = H.print_window(int(hwnd), 3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("window capture", exc) from exc
    return C.finalize_shot(
        shot.pixels, shot.stride, shot.width, shot.height, path=path, prefix="hwnd",
        origin_x=shot.left, origin_y=shot.top, purge=False, extra={"method": "remedy_core"},
    )


def find_webview_host_hwnd() -> int | None:
    return None


def detect_system_prompt() -> dict[str, Any]:
    return {"blocked": False, "kind": "", "message": ""}


def find_remedy_desktop_hwnd() -> int | None:
    with contextlib.suppress(Exception):
        hit = C.find_title_hwnd(list_windows(limit=80), exact="Remedy Desktop", prefix="Remedy Desktop")
        return hit[0] if hit else None
    return None


def _atspi_clickable_candidates(*, max_marks: int = 40) -> list[dict[str, Any]]:
    try:
        return list(H.a11y_snapshot(max(1, int(max_marks or 40))))
    except NativeRuntimeUnavailableError:
        return []


def _atspi_snapshot_elements(cap: int) -> list[dict[str, Any]]:
    return C.a11y_as_elements(_atspi_clickable_candidates(max_marks=cap), cap)


def _windows_as_elements(cap: int) -> list[dict[str, Any]]:
    return C.windows_as_elements(list_windows(limit=min(cap, 80)), cap)


def desktop_snapshot(limit: int = 40, mode: str = "auto", hwnd: int | None = None) -> list[dict[str, Any]]:
    return C.compose_desktop_snapshot(
        limit=limit, mode=mode, hwnd=hwnd, list_windows_fn=list_windows,
        controls_fn=lambda _root, cap: _atspi_snapshot_elements(cap), prefer_controls_alone=True,
        controls_modes=frozenset({"controls", "uia", "deep", "atspi"}),
    )


def _ocr_words_from_bgr(raw: bytes, stride: int, width: int, height: int) -> list[dict[str, Any]]:
    return C.ocr_words_from_bgr(raw, stride, width, height)


def _ocr_word_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    return C.ocr_word_candidates(
        raw, stride, width, height, max_marks=max_marks, words_from=_ocr_words_from_bgr
    )


def _pixel_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    return C.pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)


def _merge_candidates(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]], cap: int
) -> list[dict[str, Any]]:
    return C.merge_ui_candidates(primary, extra, cap)


def detect_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cap = max(1, int(max_marks or 20))
    atspi = _atspi_clickable_candidates(max_marks=cap)
    ocr = (
        _ocr_word_candidates(raw, stride, width, height, max_marks=cap)
        if width >= 32 and height >= 32 and raw
        else []
    )
    merged = _merge_candidates(atspi, ocr, cap)
    return merged or _pixel_ui_candidates(raw, stride, width, height, max_marks=cap)


@_hands("hover")
def move_mouse(x: int, y: int) -> None:
    H.mouse_move(int(x), int(y))


@_hands("click")
def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    code = C.MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    click(
        int(el.get("x") or el.get("cx") or 0),
        int(el.get("y") or el.get("cy") or 0),
        button=button,
        clicks=clicks,
    )


@_hands("drag")
def drag(x1: int, y1: int, x2: int, y2: int, *, steps: int = 12) -> None:
    H.mouse_drag(int(x1), int(y1), int(x2), int(y2), max(2, int(steps)))


@_hands("scroll")
def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    H.mouse_scroll(int(x), int(y), int(dx or 0), int(dy or 0))


def type_text(
    text: str,
    *,
    per_char: bool = False,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> int:
    _ = per_char
    _require_linux()

    def _send(ch: str) -> None:
        try:
            H.type_text(ch, C.TYPE_DELAY_MS)
        except (NativeRuntimeUnavailableError, H.HostError) as exc:
            raise _host_fail("type", exc) from exc

    return C.type_text_chars(text, abort_check=abort_check, chars_typed=chars_typed, send=_send)


@_hands("press_key")
def press_key(key: str) -> None:
    vks = resolve_key_combo(key)
    if vks:
        H.key_combo(vks)


def type_text_fast(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> dict[str, Any]:
    return C.type_text_fast(
        text,
        type_text=type_text,
        get_clipboard=get_clipboard_text,
        set_clipboard=set_clipboard_text,
        press_key=press_key,
        host_error=H.HostError,
        abort_check=abort_check,
        chars_typed=chars_typed,
    )


@_hands("press_hold")
def press_hold(
    x: int, y: int, *, hold_ms: int = 2600, abort_check: Callable[[], bool] | None = None
) -> dict[str, Any]:
    def _up() -> None:
        with contextlib.suppress(NativeRuntimeUnavailableError, H.HostError):
            H.mouse_button(H.MOUSE_LEFT, False)

    return C.press_hold(
        x, y,
        mouse_move=lambda mx, my: H.mouse_move(int(mx), int(my)),
        mouse_down=lambda: H.mouse_button(H.MOUSE_LEFT, True),
        mouse_up=_up,
        hold_ms=hold_ms,
        abort_check=abort_check,
    )


def foreground_window_info() -> dict[str, Any]:
    try:
        hwnd, title = H.foreground_window()
        return {"hwnd": int(hwnd), "title": str(title or "")[:200]}
    except (NativeRuntimeUnavailableError, H.HostError):
        return {"hwnd": 0, "title": ""}


def focus_window(hwnd: int) -> bool:
    _require_linux()
    if not hwnd:
        return False
    try:
        return bool(H.focus_window(int(hwnd)))
    except (NativeRuntimeUnavailableError, H.HostError):
        return False


def manage_window(
    hwnd: int, verb: str, *, x: int | None = None, y: int | None = None,
    width: int | None = None, height: int | None = None,
) -> dict[str, Any]:
    _require_linux()
    return C.manage_window_dispatch(
        hwnd, verb, x=x, y=y, width=width, height=height,
        window_rect_fn=H.window_rect, manage_fn=H.manage_window,
        fail_soft=True, require_partial_args=True,
    )


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    return H.list_windows(max(1, int(limit)))


def list_monitors() -> list[dict[str, Any]]:
    return H.list_monitors()


def get_clipboard_text() -> str:
    return H.clipboard_get_text()


def set_clipboard_text(text: str) -> bool:
    H.clipboard_set_text(str(text or ""))
    return True


def _which(*names: str) -> str | None:
    from remedy.core.computer.desktop_launch import which as _w
    return _w(*names)


def open_app(name: str, search_dirs: list[str] | None = None) -> dict[str, Any]:
    from remedy.core.computer.desktop_launch import open_app_linux
    return open_app_linux(name, search_dirs=search_dirs)


def open_url(url: str) -> dict[str, Any]:
    from remedy.core.computer.desktop_launch import open_url_linux
    return open_url_linux(url)
