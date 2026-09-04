"""Linux desktop — thin Zig-backed wrappers over ``host_binding``.

Shared SoM / keys / shot / pixel policy: :mod:`desktop_common`. AT-SPI + OCR
detect orchestration stays here. Input/capture HostError → RuntimeError via
``_host_fail`` (fail closed, no blank frames). Clipboard HostError propagates.
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
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

PASTE_THRESHOLD = C.PASTE_THRESHOLD
_LINUX_HANDS_HINT = (
    "needs an X11 display with XTest (DISPLAY set; XWayland works). "
    "Pure Wayland without XWayland is not supported yet"
)


def _require_linux() -> None:
    if sys.platform == "win32":
        raise RuntimeError("desktop_linux is for POSIX desktops")


def _host_fail(need: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(f"Linux {need} failed via remedy_core — {_LINUX_HANDS_HINT}: {exc}")


def _remedy_home() -> Path:
    return C.remedy_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    return C.default_shot_path(prefix)


def purge_old_shots(*, max_age_s: float = 900.0, home_dir: Path | str | None = None) -> int:
    return C.purge_old_shots(max_age_s=max_age_s, home_dir=home_dir)


def _write_png_bgr(
    path: Path,
    width: int,
    height: int,
    raw: bytes | bytearray | memoryview,
    stride: int,
    *,
    bytes_per_pixel: int = 3,
) -> None:
    C.write_png_bgr(path, width, height, raw, stride, bytes_per_pixel=bytes_per_pixel)


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    _require_linux()
    try:
        shot = H.capture_virtual_screen(3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("capture", exc) from exc
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def screenshot_png(
    path: Path | None = None, *, marks: list[Any] | None = None
) -> dict[str, Any]:
    raw, stride, width, height, left, top = _capture_virtual_screen()
    if width < 2 or height < 2:
        raise RuntimeError(f"Linux screenshot failed — {_LINUX_HANDS_HINT}")
    out = Path(path) if path is not None else _default_shot_path("desk")
    out.parent.mkdir(parents=True, exist_ok=True)
    if marks:
        buf = bytearray(raw)
        C.draw_marks_on_bgr(
            buf,
            stride,
            width,
            height,
            [
                {
                    "n": mk.get("n"),
                    "x": int(mk.get("x", 0)) - left,
                    "y": int(mk.get("y", 0)) - top,
                }
                for mk in marks
                if isinstance(mk, dict)
            ],
        )
        raw = bytes(buf)
    try:
        _write_png_bgr(out, width, height, raw, stride)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("screenshot", exc) from exc
    with contextlib.suppress(Exception):
        purge_old_shots(max_age_s=900.0, home_dir=_remedy_home())
    return {
        "path": str(out),
        "width": width,
        "height": height,
        "origin": {"x": left, "y": top},
        "method": "remedy_core",
    }


def screenshot_region_png(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    path: Path | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    _require_linux()
    sc = float(scale) if scale and scale > 0 else 1.0
    rx, ry = int(round(int(x) * sc)), int(round(int(y) * sc))
    rw, rh = max(1, int(round(int(width) * sc))), max(1, int(round(int(height) * sc)))
    try:
        origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("region screenshot", exc) from exc
    bx, by = rx - origin_x, ry - origin_y
    if bx < 0:
        rw += bx
        bx = 0
    if by < 0:
        rh += by
        by = 0
    if bx >= full_w or by >= full_h or rw <= 0 or rh <= 0:
        raise ValueError("region outside virtual screen")
    rw, rh = min(rw, full_w - bx), min(rh, full_h - by)
    try:
        crop = H.capture_region(origin_x + bx, origin_y + by, rw, rh, 3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("region screenshot", exc) from exc
    out = Path(path) if path is not None else _default_shot_path("region")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, rw, rh, crop.pixels, crop.stride)
    return {
        "path": str(out),
        "width": rw,
        "height": rh,
        "origin": {"x": origin_x + bx, "y": origin_y + by},
        "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
        "method": "remedy_core",
    }


def screenshot_monitor_png(index: int, path: Path | None = None) -> dict[str, Any]:
    mons = list_monitors()
    if not mons:
        return screenshot_png(path)
    idx = int(index)
    if idx < 0 or idx >= len(mons):
        raise ValueError(f"monitor index {idx} out of range 0..{len(mons) - 1}")
    m = mons[idx]
    return screenshot_region_png(
        int(m.get("left", 0)),
        int(m.get("top", 0)),
        int(m.get("width") or max(0, int(m.get("right", 0)) - int(m.get("left", 0)))),
        int(m.get("height") or max(0, int(m.get("bottom", 0)) - int(m.get("top", 0)))),
        path=path,
        scale=1.0,
    )


def print_window_png(hwnd: int | None = None, path: Path | None = None) -> dict[str, Any]:
    _require_linux()
    if not hwnd:
        return screenshot_png(path)
    try:
        shot = H.print_window(int(hwnd), 3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("window capture", exc) from exc
    out = Path(path) if path is not None else _default_shot_path("hwnd")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, shot.width, shot.height, shot.pixels, shot.stride)
    return {
        "path": str(out),
        "width": shot.width,
        "height": shot.height,
        "origin": {"x": shot.left, "y": shot.top},
        "method": "remedy_core",
    }


def find_webview_host_hwnd() -> int | None:
    return None


def detect_system_prompt() -> dict[str, Any]:
    return {"blocked": False, "kind": "", "message": ""}


def find_remedy_desktop_hwnd() -> int | None:
    with contextlib.suppress(Exception):
        for w in list_windows(limit=80):
            title = str(w.get("title") or "").strip()
            if title == "Remedy Desktop" or title.startswith("Remedy Desktop"):
                return int(w["hwnd"])
    return None


def _atspi_clickable_candidates(*, max_marks: int = 40) -> list[dict[str, Any]]:
    try:
        return list(H.a11y_snapshot(max(1, int(max_marks or 40))))
    except NativeRuntimeUnavailableError:
        return []


def _atspi_snapshot_elements(cap: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, c in enumerate(_atspi_clickable_candidates(max_marks=cap)):
        w, h = int(c.get("w") or 0), int(c.get("h") or 0)
        x, y = int(c.get("x") or 0), int(c.get("y") or 0)
        name = str(c.get("name") or c.get("role") or "widget")[:120]
        role = str(c.get("role") or "widget")
        out.append(
            {
                "ref": f"c{i + 1}",
                "tag": role,
                "role": role,
                "name": name,
                "x": x,
                "y": y,
                "w": w,
                "h": h,
                "source": "atspi",
                "bounds": {
                    "left": x - w // 2,
                    "top": y - h // 2,
                    "right": x - w // 2 + w,
                    "bottom": y - h // 2 + h,
                },
            }
        )
        if len(out) >= cap:
            break
    return out


def _windows_as_elements(cap: int) -> list[dict[str, Any]]:
    return C.windows_as_elements(list_windows(limit=min(cap, 80)), cap)


def desktop_snapshot(
    limit: int = 40, mode: str = "auto", hwnd: int | None = None
) -> list[dict[str, Any]]:
    _ = hwnd
    cap = max(1, min(int(limit or 40), 100))
    mode_s = (mode or "auto").strip().lower()
    if mode_s == "windows":
        return _windows_as_elements(cap)
    atspi = _atspi_snapshot_elements(cap)
    if mode_s in ("controls", "uia", "deep", "atspi"):
        return atspi[:cap]
    if atspi:
        return atspi[:cap]
    return []


def _ocr_words_from_bgr(
    raw: bytes, stride: int, width: int, height: int
) -> list[dict[str, Any]]:
    if width < 32 or height < 32 or not raw:
        return []
    path = _default_shot_path("ocr-detect")
    try:
        _write_png_bgr(path, width, height, raw, stride)
        from remedy.core.computer.ocr import read_screenshot_ocr

        result = read_screenshot_ocr(path)
        words = result.get("words") if isinstance(result, dict) else None
        return list(words or [])
    except Exception:
        return []
    finally:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def _ocr_word_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cap = max(1, int(max_marks or 20))
    out: list[dict[str, Any]] = []
    for w in _ocr_words_from_bgr(raw, stride, width, height):
        if not isinstance(w, dict):
            continue
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        try:
            ix, iy = float(w.get("x") or 0), float(w.get("y") or 0)
            iw, ih = float(w.get("w") or 0), float(w.get("h") or 0)
        except (TypeError, ValueError):
            continue
        if iw < 2 or ih < 2:
            continue
        bw, bh = int(round(iw)), int(round(ih))
        out.append(
            {
                "x": int(round(ix + iw / 2.0)),
                "y": int(round(iy + ih / 2.0)),
                "w": bw,
                "h": bh,
                "area": bw * bh,
                "name": text[:80],
                "role": "text",
                "source": "ocr",
            }
        )
        if len(out) >= cap:
            break
    return out[:cap]


def _pixel_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    return C.pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)


def _merge_candidates(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]], cap: int
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for c in list(primary) + list(extra):
        if not isinstance(c, dict):
            continue
        try:
            x, y = int(c["x"]), int(c["y"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (x // 12, y // 12)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= cap:
            break
    return out


def detect_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cap = max(1, int(max_marks or 20))
    atspi = _atspi_clickable_candidates(max_marks=cap)
    ocr: list[dict[str, Any]] = []
    if width >= 32 and height >= 32 and raw:
        ocr = _ocr_word_candidates(raw, stride, width, height, max_marks=cap)
    merged = _merge_candidates(atspi, ocr, cap)
    if merged:
        return merged
    return _pixel_ui_candidates(raw, stride, width, height, max_marks=cap)


def move_mouse(x: int, y: int) -> None:
    _require_linux()
    try:
        H.mouse_move(int(x), int(y))
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("hover", exc) from exc


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    _require_linux()
    code = C.MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    try:
        H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("click", exc) from exc


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    click(
        int(el.get("x") or el.get("cx") or 0),
        int(el.get("y") or el.get("cy") or 0),
        button=button,
        clicks=clicks,
    )


def drag(x1: int, y1: int, x2: int, y2: int, *, steps: int = 12) -> None:
    _require_linux()
    try:
        H.mouse_drag(int(x1), int(y1), int(x2), int(y2), max(2, int(steps)))
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("drag", exc) from exc


def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    _require_linux()
    try:
        H.mouse_scroll(int(x), int(y), int(dx or 0), int(dy or 0))
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("scroll", exc) from exc


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

    return C.type_text_chars(
        text, abort_check=abort_check, chars_typed=chars_typed, send=_send
    )


def resolve_key_combo(key: str, *, vk_scan=None) -> list[int]:
    return C.resolve_key_combo(key, vk_scan=vk_scan)


def press_key(key: str) -> None:
    _require_linux()
    try:
        vks = resolve_key_combo(key)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("press_key", exc) from exc
    if not vks:
        return
    try:
        H.key_combo(vks)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("press_key", exc) from exc


def type_text_fast(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> dict[str, Any]:
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
    _require_linux()
    try:
        H.mouse_move(int(x), int(y))
        time.sleep(0.05)
        H.mouse_button(H.MOUSE_LEFT, True)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("press_hold", exc) from exc
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
        with contextlib.suppress(NativeRuntimeUnavailableError, H.HostError):
            H.mouse_button(H.MOUSE_LEFT, False)
    return {"held_ms": int(min(held, total) * 1000), "x": x, "y": y}


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
    hwnd: int,
    verb: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    _require_linux()
    v = (verb or "").strip().lower()
    if not hwnd:
        return {"ok": False, "message": "hwnd required"}
    action_map = {
        "minimize": H.WINDOW_MINIMIZE,
        "maximize": H.WINDOW_MAXIMIZE,
        "restore": H.WINDOW_RESTORE,
        "close": H.WINDOW_CLOSE,
        "move": H.WINDOW_MOVE_RESIZE,
        "resize": H.WINDOW_MOVE_RESIZE,
    }
    if v not in action_map:
        return {"ok": False, "message": f"Unknown window verb {verb!r}"}
    nx = int(x) if x is not None else 0
    ny = int(y) if y is not None else 0
    nw = int(width) if width is not None else 0
    nh = int(height) if height is not None else 0
    try:
        if v in ("move", "resize"):
            if v == "move" and (x is None or y is None):
                return {"ok": False, "message": "move requires x and y"}
            if v == "resize" and (width is None or height is None):
                return {"ok": False, "message": "resize requires width and height"}
            if v == "move" or width is None or height is None or x is None or y is None:
                with contextlib.suppress(NativeRuntimeUnavailableError, H.HostError):
                    left, top, right, bottom = H.window_rect(int(hwnd))
                    if x is None:
                        nx = left
                    if y is None:
                        ny = top
                    if width is None:
                        nw = max(0, right - left)
                    if height is None:
                        nh = max(0, bottom - top)
        H.manage_window(int(hwnd), action_map[v], nx, ny, nw, nh)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        return {"ok": False, "message": f"{v} hwnd={hwnd} failed: {exc}"}
    if v == "close":
        return {
            "ok": True,
            "message": (
                f"Sent close to hwnd={hwnd} (the app may show a save prompt — "
                "snapshot to see it)"
            ),
        }
    if v in ("move", "resize"):
        return {"ok": True, "message": f"{v} hwnd={hwnd} → ({nx},{ny}) {nw}x{nh}"}
    return {"ok": True, "message": f"{v} hwnd={hwnd}"}


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
