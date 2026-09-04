"""Windows desktop — thin wrappers over ``host_binding`` (no ctypes).

Shared SoM / keys / shot policy: :mod:`desktop_common`. Launch policy:
:mod:`desktop_launch`. UIA soft helpers: :mod:`guidance`.
:class:`HostError` propagates (fail closed).
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
from remedy.core.computer.desktop_launch import (
    _open_app_is_protocol_or_url,
    is_text_document_path,
    open_app,
    open_url,
    refuse_os_open_text_document,
)

PASTE_THRESHOLD = C.PASTE_THRESHOLD
_draw_marks_on_bgr = C.draw_marks_on_bgr
_SECURE_TITLES = ("user account control", "windows security")
_DIALOG_CLASS = "#32770"
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

def detect_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    cands = C.pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)
    for c in cands:
        c.pop("source", None)
    return cands

def screenshot_png(
    path: Path | None = None, *, marks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    raw, stride, width, height, left, top = _capture_virtual_screen()
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
            ],
        )
        raw = bytes(buf)
    _write_png_bgr(out, width, height, raw, stride)
    with contextlib.suppress(Exception):
        purge_old_shots(max_age_s=900.0, home_dir=_remedy_home())
    return {"path": str(out), "width": width, "height": height, "origin": {"x": left, "y": top}}

def screenshot_region_png(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    path: Path | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    _require_windows()
    sc = float(scale) if scale and scale > 0 else 1.0
    rx, ry = int(round(int(x) * sc)), int(round(int(y) * sc))
    rw, rh = max(1, int(round(int(width) * sc))), max(1, int(round(int(height) * sc)))
    origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
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
    crop = H.capture_region(origin_x + bx, origin_y + by, rw, rh, 3)
    out = Path(path) if path is not None else _default_shot_path("region")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, rw, rh, crop.pixels, crop.stride)
    return {
        "path": str(out),
        "width": rw,
        "height": rh,
        "origin": {"x": origin_x + bx, "y": origin_y + by},
        "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
    }

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

def resolve_key_combo(key: str, *, vk_scan=None) -> list[int]:
    return C.resolve_key_combo(key, vk_scan=vk_scan)

def press_key(key: str) -> None:
    _require_windows()
    vks = resolve_key_combo(key)
    if vks:
        H.key_combo(vks)

def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    _require_windows()
    return H.list_windows(max(1, int(limit)))

def find_remedy_desktop_hwnd() -> int | None:
    for w in list_windows(limit=80):
        title = str(w.get("title") or "").strip()
        if title == "Remedy Desktop" or title.startswith("Remedy Desktop"):
            return int(w["hwnd"])
    return None

def _window_class(hwnd: int) -> str:
    with contextlib.suppress(Exception):
        return H.window_class(int(hwnd))
    return ""

def detect_system_prompt() -> dict[str, Any]:
    with contextlib.suppress(Exception):
        fg = foreground_window_info()
        title = str(fg.get("title") or "").lower()
        cls = _window_class(int(fg.get("hwnd") or 0)).lower()
        if any(t in title for t in _SECURE_TITLES) or cls in (
            "credential dialog xaml host",
            "#32770",
        ):
            if any(t in title for t in _SECURE_TITLES):
                return {
                    "blocked": True,
                    "kind": "uac",
                    "message": (
                        "A Windows security / UAC prompt is on the secure desktop. "
                        "I can't click it — Windows blocks all automated input there "
                        "by design. Please approve or dismiss it yourself, then say "
                        "continue."
                    ),
                }
    return {"blocked": False, "kind": "", "message": ""}

def find_dialog_window() -> dict[str, Any] | None:
    with contextlib.suppress(Exception):
        fg = foreground_window_info()
        hwnd = int(fg.get("hwnd") or 0)
        if hwnd and _window_class(hwnd).lower() == _DIALOG_CLASS.lower():
            return {"hwnd": hwnd, "title": str(fg.get("title") or "")}
        for w in list_windows(limit=30):
            wh = int(w.get("hwnd") or 0)
            if wh and str(w.get("class") or "").lower() == _DIALOG_CLASS.lower():
                return {"hwnd": wh, "title": str(w.get("title") or "")}
    return None

def desktop_snapshot(
    limit: int = 40, *, mode: str = "auto", hwnd: int | None = None
) -> list[dict[str, Any]]:
    mode_s = (mode or "auto").strip().lower()
    cap = max(1, min(int(limit or 40), 100))
    wins = list_windows(limit=min(cap, 80))
    win_els = C.windows_as_elements(wins, cap)
    if mode_s == "windows":
        return win_els[:cap]
    ctrl_els: list[dict[str, Any]] = []
    try:
        root_hwnd = hwnd
        if root_hwnd is None and wins:
            try:
                fg = int(H.foreground_window()[0] or 0)
                root_hwnd = fg or wins[0].get("hwnd")
            except Exception:
                root_hwnd = wins[0].get("hwnd")
        from remedy.core.computer.guidance import uia_control_snapshot
        raw = uia_control_snapshot(hwnd=root_hwnd, max_elements=cap)
        if raw:
            ctrl_els = raw
    except Exception:
        ctrl_els = []
    if mode_s in ("controls", "uia", "deep"):
        return (ctrl_els or win_els)[:cap]
    out = list(win_els)
    seen = {(int(e["x"]), int(e["y"])) for e in out}
    for c in ctrl_els:
        if len(out) >= cap:
            break
        key = (int(c.get("x") or 0), int(c.get("y") or 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out[:cap]

def print_window_png(hwnd: int, path: Path | None = None) -> dict[str, Any]:
    _require_windows()
    shot = H.print_window(int(hwnd), 3)
    out = Path(path) if path is not None else _default_shot_path("hwnd")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, shot.width, shot.height, shot.pixels, shot.stride)
    return {
        "path": str(out),
        "width": shot.width,
        "height": shot.height,
        "origin": {"x": shot.left, "y": shot.top},
        "hwnd": int(hwnd),
        "method": "PrintWindow",
    }

def find_child_hwnd(
    parent: int, *, class_name: str | None = None, title_substr: str | None = None
) -> int | None:
    _require_windows()
    found = H.find_child_hwnd(int(parent), class_name or "", title_substr or "")
    return found or None

def find_webview_host_hwnd() -> int | None:
    _require_windows()
    for w in list_windows(limit=200):
        title = str(w.get("title") or "").lower()
        if "remedy" not in title and "tauri" not in title:
            continue
        hwnd = int(w["hwnd"])
        for cls in (
            "Chrome_WidgetWin_1",
            "Chrome_RenderWidgetHostHWND",
            "WebView2",
            "Intermediate D3D Window",
        ):
            child = find_child_hwnd(hwnd, class_name=cls)
            if child:
                return child
        return hwnd
    return None

def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    hwnd = el.get("hwnd")
    if hwnd:
        with contextlib.suppress(Exception):
            focus_window(int(hwnd))
            time.sleep(0.05)
    click(int(el.get("x") or 0), int(el.get("y") or 0), button=button, clicks=clicks)

def focus_window(hwnd: int) -> bool:
    _require_windows()
    return H.focus_window(int(hwnd))

def foreground_window_info() -> dict[str, Any]:
    out: dict[str, Any] = {"hwnd": 0, "title": ""}
    with contextlib.suppress(Exception):
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
    _require_windows()
    return H.clipboard_get_text()

def set_clipboard_text(text: str) -> bool:
    _require_windows()
    H.clipboard_set_text(str(text or ""))
    return True

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

def focus_window_by_title(title_substr: str) -> dict[str, Any] | None:
    needle = (title_substr or "").strip().lower()
    if not needle:
        return None
    for w in list_windows(limit=80):
        title = str(w.get("title") or "")
        if needle in title.lower():
            hwnd = int(w["hwnd"])
            focus_window(hwnd)
            return {"hwnd": hwnd, "title": title}
    return None

def list_monitors() -> list[dict[str, Any]]:
    _require_windows()
    monitors = H.list_monitors()
    for m in monitors:
        m["remedy"] = False
    if monitors and not any(m["primary"] for m in monitors):
        monitors[0]["primary"] = True
    hwnd = find_remedy_desktop_hwnd()
    if hwnd:
        with contextlib.suppress(H.HostError):
            left, top, right, bottom = H.window_rect(hwnd)
            cx, cy = (left + right) // 2, (top + bottom) // 2
            for m in monitors:
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    m["remedy"] = True
                    break
    return monitors

def screenshot_monitor_png(
    monitor_index: int = 0, *, path: Path | None = None
) -> dict[str, Any]:
    mons = list_monitors()
    if not mons:
        return screenshot_png(path)
    idx = int(monitor_index)
    if idx < 0 or idx >= len(mons):
        raise ValueError(f"monitor index {idx} out of range 0..{len(mons)-1}")
    m = mons[idx]
    return screenshot_region_png(
        m["left"], m["top"], m["width"], m["height"], path=path, scale=1.0
    )

