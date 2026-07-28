"""Windows desktop capture + input (in-process, no Tauri required)."""

from __future__ import annotations

import ctypes
import struct
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

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


def screenshot_png(path: Path | None = None) -> dict[str, Any]:
    """Capture the virtual screen to a PNG file. Returns path + size."""
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

    out = path
    if out is None:
        out_dir = Path.home() / ".remedy" / "computer" / "shots"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"desk_{int(time.time() * 1000)}.png"
    else:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)

    _write_png_bgr(out, width, height, bytes(buf), stride)
    return {
        "path": str(out),
        "width": width,
        "height": height,
        "origin": {"x": left, "y": top},
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


def type_text(text: str) -> None:
    _require_windows()
    for ch in text or "":
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
            }
        )
        return True

    user32.EnumWindows(enum_proc, 0)
    return results


def focus_window(hwnd: int) -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
