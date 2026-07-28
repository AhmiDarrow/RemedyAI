"""Windows desktop capture + input (in-process, no Tauri required)."""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

# Virtual-key codes (subset)
_VK = {
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "space": 0x20,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "meta": 0x5B,
    "cmd": 0x5B,
}


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Desktop computer use requires Windows")


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    """Return (bgr_bytes, stride, width, height, origin_x, origin_y)."""
    _require_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.SetProcessDPIAware()

    left = user32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
    top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
    width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
    height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
    if width <= 0 or height <= 0:
        width = user32.GetSystemMetrics(0)
        height = user32.GetSystemMetrics(1)
        left, top = 0, 0

    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    old = gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, width, height, hdc, left, top, 0x00CC0020)  # SRCCOPY

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    stride = (width * 3 + 3) & ~3
    buf = ctypes.create_string_buffer(stride * height)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 24
    bmi.bmiHeader.biCompression = 0

    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, hdc)
    return bytes(buf), stride, width, height, left, top


def _default_shot_path(prefix: str = "desk") -> Path:
    out_dir = Path.home() / ".remedy" / "computer" / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{prefix}_{int(time.time() * 1000)}.png"


def screenshot_png(path: Path | None = None) -> dict[str, Any]:
    """Capture the virtual screen to a PNG file. Returns path + size."""
    raw, stride, width, height, left, top = _capture_virtual_screen()
    out = Path(path) if path is not None else _default_shot_path("desk")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, width, height, raw, stride)
    return {
        "path": str(out),
        "width": width,
        "height": height,
        "origin": {"x": left, "y": top},
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
    """Capture a rectangle in **screen coordinates** (physical pixels after DPI aware).

    *x*/*y*/*width*/*height* may be CSS/logical pixels when *scale* is devicePixelRatio.
    """
    sc = float(scale) if scale and scale > 0 else 1.0
    rx = int(round(int(x) * sc))
    ry = int(round(int(y) * sc))
    rw = max(1, int(round(int(width) * sc)))
    rh = max(1, int(round(int(height) * sc)))

    raw, stride, full_w, full_h, origin_x, origin_y = _capture_virtual_screen()
    # Convert screen coords → bitmap coords
    bx = rx - origin_x
    by = ry - origin_y
    # Clamp to bitmap
    if bx < 0:
        rw += bx
        bx = 0
    if by < 0:
        rh += by
        by = 0
    if bx >= full_w or by >= full_h or rw <= 0 or rh <= 0:
        raise ValueError("region outside virtual screen")
    rw = min(rw, full_w - bx)
    rh = min(rh, full_h - by)

    crop_stride = (rw * 3 + 3) & ~3
    crop = bytearray(crop_stride * rh)
    for row in range(rh):
        src_off = (by + row) * stride + bx * 3
        dst_off = row * crop_stride
        crop[dst_off : dst_off + rw * 3] = raw[src_off : src_off + rw * 3]

    out = Path(path) if path is not None else _default_shot_path("region")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, rw, rh, bytes(crop), crop_stride)
    return {
        "path": str(out),
        "width": rw,
        "height": rh,
        "origin": {"x": origin_x + bx, "y": origin_y + by},
        "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
    }


def _write_png_bgr(path: Path, width: int, height: int, raw: bytes, stride: int) -> None:
    """Minimal PNG writer (RGB from BGR rows)."""
    import binascii
    import zlib

    rows = []
    for y in range(height):
        row = raw[y * stride : y * stride + width * 3]
        # BGR → RGB
        rgb = bytearray(width * 3)
        for i in range(width):
            b, g, r = row[i * 3], row[i * 3 + 1], row[i * 3 + 2]
            rgb[i * 3] = r
            rgb[i * 3 + 1] = g
            rgb[i * 3 + 2] = b
        rows.append(b"\x00" + bytes(rgb))
    compressed = zlib.compress(b"".join(rows), 6)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(
        b"IEND", b""
    )
    path.write_bytes(png)


# --- input ---

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _send_input(*inputs: INPUT) -> None:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = ctypes.windll.user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != n:
        raise RuntimeError(f"SendInput sent {sent}/{n}")


def _abs_coords(x: int, y: int) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    left = user32.GetSystemMetrics(76)
    top = user32.GetSystemMetrics(77)
    width = user32.GetSystemMetrics(78) or 1
    height = user32.GetSystemMetrics(79) or 1
    # Map virtual-screen pixels → 0..65535 absolute
    ax = int((x - left) * 65535 / max(width - 1, 1))
    ay = int((y - top) * 65535 / max(height - 1, 1))
    return ax, ay


def move_mouse(x: int, y: int) -> None:
    _require_windows()
    ax, ay = _abs_coords(int(x), int(y))
    mi = MOUSEINPUT(ax, ay, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    _send_input(INPUT(INPUT_MOUSE, INPUT_UNION(mi=mi)))


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    _require_windows()
    move_mouse(x, y)
    time.sleep(0.02)
    btn = (button or "left").lower()
    down, up = MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP
    if btn in ("right", "r"):
        down, up = MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    elif btn in ("middle", "mid", "m"):
        down, up = MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    for _ in range(max(1, int(clicks or 1))):
        _send_input(
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, down, 0, None))),
            INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, up, 0, None))),
        )
        time.sleep(0.04)


def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    _require_windows()
    move_mouse(x1, y1)
    time.sleep(0.02)
    _send_input(INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None))))
    time.sleep(0.02)
    move_mouse(x2, y2)
    time.sleep(0.02)
    _send_input(INPUT(INPUT_MOUSE, INPUT_UNION(mi=MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None))))


def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    _require_windows()
    move_mouse(x, y)
    time.sleep(0.02)
    # wheel: +120 per notch up
    if dy:
        data = int(dy) * 120
        _send_input(
            INPUT(
                INPUT_MOUSE,
                INPUT_UNION(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, None)),
            )
        )
    _ = dx  # horizontal wheel later if needed


def type_text(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
) -> None:
    """Type unicode text. *abort_check* callable → stop mid-string when true."""
    _require_windows()
    for i, ch in enumerate(text or ""):
        if abort_check is not None and i > 0 and i % 8 == 0:
            try:
                if abort_check():
                    raise RuntimeError("Aborted by user during type")
            except RuntimeError:
                raise
            except Exception:
                pass
        code = ord(ch)
        down = KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, None)
        up = KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, None)
        _send_input(
            INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=down)),
            INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=up)),
        )
        time.sleep(0.005)


def press_key(key: str) -> None:
    """Press a key or combo like 'ctrl+s', 'enter', 'a'."""
    _require_windows()
    parts = [p.strip().lower() for p in (key or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return
    vks: list[int] = []
    for p in parts:
        if p in _VK:
            vks.append(_VK[p])
        elif len(p) == 1:
            # VkKeyScanW for character
            sc = ctypes.windll.user32.VkKeyScanW(ord(p))
            vks.append(sc & 0xFF)
        else:
            raise ValueError(f"Unknown key: {p}")
    for vk in vks:
        _send_input(INPUT(INPUT_KEYBOARD, INPUT_UNION(ki=KEYBDINPUT(vk, 0, 0, 0, None))))
    for vk in reversed(vks):
        _send_input(
            INPUT(
                INPUT_KEYBOARD,
                INPUT_UNION(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP, 0, None)),
            )
        )


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    _require_windows()
    user32 = ctypes.windll.user32
    results: list[dict[str, Any]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if len(results) >= max(1, limit):
            return False
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w = int(rect.right - rect.left)
        h = int(rect.bottom - rect.top)
        if w < 8 or h < 8:
            return True
        results.append(
            {
                "hwnd": int(hwnd),
                "title": title[:200],
                "bounds": {
                    "left": int(rect.left),
                    "top": int(rect.top),
                    "right": int(rect.right),
                    "bottom": int(rect.bottom),
                },
                "width": w,
                "height": h,
            }
        )
        return True

    user32.EnumWindows(enum_proc, 0)
    return results


def desktop_snapshot(
    limit: int = 40,
    *,
    mode: str = "auto",
    hwnd: int | None = None,
) -> list[dict[str, Any]]:
    """Desktop interactive snapshot.

    *mode*:
      - ``windows`` — top-level windows only (refs w1…)
      - ``controls`` — UIA control tree when available (refs c1…), else windows
      - ``auto`` — windows + UIA controls (merged, caps at *limit*)
    *hwnd*: optional root window for UIA walk (focused app).
    """
    mode_s = (mode or "auto").strip().lower()
    cap = max(1, min(int(limit or 40), 100))
    wins = list_windows(limit=min(cap, 80))
    win_els: list[dict[str, Any]] = []
    for i, w in enumerate(wins):
        b = w.get("bounds") or {}
        left, top = int(b.get("left", 0)), int(b.get("top", 0))
        right, bottom = int(b.get("right", 0)), int(b.get("bottom", 0))
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        ref = f"w{i + 1}"
        win_els.append(
            {
                "ref": ref,
                "tag": "window",
                "role": "window",
                "name": str(w.get("title") or "")[:120],
                "x": cx,
                "y": cy,
                "w": int(w.get("width") or max(0, right - left)),
                "h": int(w.get("height") or max(0, bottom - top)),
                "hwnd": w.get("hwnd"),
                "bounds": b,
            }
        )

    if mode_s == "windows":
        return win_els[:cap]

    ctrl_els: list[dict[str, Any]] = []
    try:
        from remedy.core.computer.desktop_uia import uia_control_snapshot

        root_hwnd = hwnd
        if root_hwnd is None and wins:
            # Prefer foreground window for control walk
            try:
                fg = int(ctypes.windll.user32.GetForegroundWindow() or 0)
                if fg:
                    root_hwnd = fg
            except Exception:
                root_hwnd = wins[0].get("hwnd")
        raw = uia_control_snapshot(hwnd=root_hwnd, max_elements=cap)
        if raw:
            ctrl_els = raw
    except Exception:
        ctrl_els = []

    if mode_s in ("controls", "uia", "deep"):
        return (ctrl_els or win_els)[:cap]

    # auto: windows first, then controls that aren't huge window frames
    out = list(win_els)
    seen_xy: set[tuple[int, int]] = {(int(e["x"]), int(e["y"])) for e in out}
    for c in ctrl_els:
        if len(out) >= cap:
            break
        key = (int(c.get("x") or 0), int(c.get("y") or 0))
        if key in seen_xy:
            continue
        seen_xy.add(key)
        out.append(c)
    return out[:cap]


def print_window_png(hwnd: int, path: Path | None = None) -> dict[str, Any]:
    """Capture a single HWND via PrintWindow (better for layered/WebView hosts)."""
    _require_windows()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.SetProcessDPIAware()
    rect = wintypes.RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        raise RuntimeError("GetWindowRect failed")
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width < 2 or height < 2:
        raise RuntimeError("window too small")

    hwnd_dc = user32.GetWindowDC(int(hwnd))
    memdc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(memdc, bmp)
    # PW_RENDERFULLCONTENT = 2 (Win8.1+) — captures DirectComposition/WebView better
    ok = user32.PrintWindow(int(hwnd), memdc, 2)
    if not ok:
        ok = user32.PrintWindow(int(hwnd), memdc, 0)
    if not ok:
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(int(hwnd), hwnd_dc)
        raise RuntimeError("PrintWindow failed")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    stride = (width * 3 + 3) & ~3
    buf = ctypes.create_string_buffer(stride * height)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = width
    bmi.bmiHeader.biHeight = -height
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 24
    bmi.bmiHeader.biCompression = 0
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(bmi), 0)

    gdi32.SelectObject(memdc, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(int(hwnd), hwnd_dc)

    out = Path(path) if path is not None else _default_shot_path("hwnd")
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_bgr(out, width, height, bytes(buf), stride)
    return {
        "path": str(out),
        "width": width,
        "height": height,
        "origin": {"x": int(rect.left), "y": int(rect.top)},
        "hwnd": int(hwnd),
        "method": "PrintWindow",
    }


def find_child_hwnd(
    parent: int,
    *,
    class_name: str | None = None,
    title_substr: str | None = None,
) -> int | None:
    """Find first child HWND matching class and/or title substring."""
    _require_windows()
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_child(hwnd, _lp):  # type: ignore[no-untyped-def]
        if found:
            return False
        if class_name:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if class_name.lower() not in buf.value.lower():
                return True
        if title_substr:
            length = user32.GetWindowTextLengthW(hwnd)
            tbuf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, tbuf, length + 1)
            if title_substr.lower() not in tbuf.value.lower():
                return True
        found.append(int(hwnd))
        return False

    user32.EnumChildWindows(int(parent), enum_child, 0)
    return found[0] if found else None


def find_webview_host_hwnd() -> int | None:
    """Best-effort: locate a WebView2 / Chromium host under a Remedy-titled window."""
    _require_windows()
    user32 = ctypes.windll.user32
    candidates: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_top(hwnd, _lp):  # type: ignore[no-untyped-def]
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.lower()
        if "remedy" not in title and "tauri" not in title:
            return True
        # Prefer Chromium / WebView2 child
        for cls in (
            "Chrome_WidgetWin_1",
            "Chrome_RenderWidgetHostHWND",
            "WebView2",
            "Intermediate D3D Window",
        ):
            child = find_child_hwnd(int(hwnd), class_name=cls)
            if child:
                candidates.append(child)
                return False
        candidates.append(int(hwnd))
        return False

    user32.EnumWindows(enum_top, 0)
    return candidates[0] if candidates else None


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    """Focus window if hwnd present, then click element center."""
    hwnd = el.get("hwnd")
    if hwnd:
        try:
            focus_window(int(hwnd))
            time.sleep(0.05)
        except Exception:
            pass
    x = int(el.get("x") or 0)
    y = int(el.get("y") or 0)
    click(x, y, button=button, clicks=clicks)


def focus_window(hwnd: int) -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)


def open_url(url: str) -> dict[str, Any]:
    """Open http(s) URL in the default system browser (Windows-reliable)."""
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    if sys.platform == "win32":
        # os.startfile is more reliable than webbrowser on Windows (default app).
        try:
            os.startfile(u)  # type: ignore[attr-defined]
            return {"url": u, "method": "os.startfile"}
        except OSError:
            import subprocess

            # Empty title arg after start is required for URLs with & 
            subprocess.Popen(
                ["cmd", "/c", "start", "", u],
                shell=False,
                close_fds=True,
            )
            return {"url": u, "method": "cmd start"}
    import webbrowser

    webbrowser.open(u)
    return {"url": u, "method": "webbrowser"}


def list_monitors() -> list[dict[str, Any]]:
    """Enumerate display monitors (physical pixels, DPI-aware)."""
    _require_windows()
    user32 = ctypes.windll.user32
    user32.SetProcessDPIAware()
    monitors: list[dict[str, Any]] = []

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    def _callback(hmon, _hdc, lprect, _lparam):  # type: ignore[no-untyped-def]
        r = lprect.contents
        idx = len(monitors)
        monitors.append(
            {
                "index": idx,
                "left": int(r.left),
                "top": int(r.top),
                "right": int(r.right),
                "bottom": int(r.bottom),
                "width": int(r.right - r.left),
                "height": int(r.bottom - r.top),
                "primary": idx == 0,  # refined below
            }
        )
        return True

    user32.EnumDisplayMonitors(0, 0, MonitorEnumProc(_callback), 0)
    # Mark primary via GetSystemMetrics origin (0,0) usually on primary
    for m in monitors:
        m["primary"] = m["left"] == 0 and m["top"] == 0
    if monitors and not any(m["primary"] for m in monitors):
        monitors[0]["primary"] = True
    return monitors


def screenshot_monitor_png(
    monitor_index: int = 0,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Capture one monitor by index (from list_monitors)."""
    mons = list_monitors()
    if not mons:
        return screenshot_png(path)
    idx = int(monitor_index)
    if idx < 0 or idx >= len(mons):
        raise ValueError(f"monitor index {idx} out of range 0..{len(mons)-1}")
    m = mons[idx]
    return screenshot_region_png(
        m["left"],
        m["top"],
        m["width"],
        m["height"],
        path=path,
        scale=1.0,
    )
