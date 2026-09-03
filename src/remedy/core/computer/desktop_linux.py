"""Linux desktop capture + input over the ``remedy_core`` host.

X11/XTest input, capture, windows, clipboard, and AT-SPI snapshots are made by
``remedy_core`` through :mod:`remedy.core.computer.host_binding`. This module
keeps policy on top: screenshot files under the Remedy home, Set-of-Mark /
OCR / pixel candidates, key-name resolution, app and URL launch via xdg-open.
AT-SPI invoke / set_value / toggle are not exposed (same gap as the former
Python walker). Pointer and screenshot tools are not shelled out.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from remedy.core.computer import host_binding as H
from remedy.home import default_home
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

# Match Windows paste threshold so type_text_fast behaves the same under tests.
PASTE_THRESHOLD = 200

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
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "insert": 0x2D,
    "ins": 0x2D,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "meta": 0x5B,
    "super": 0x5B,
    "cmd": 0x5B,
}

_MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B)

_MOUSE_BUTTONS = {
    "left": H.MOUSE_LEFT,
    "l": H.MOUSE_LEFT,
    "right": H.MOUSE_RIGHT,
    "r": H.MOUSE_RIGHT,
    "middle": H.MOUSE_MIDDLE,
    "mid": H.MOUSE_MIDDLE,
    "m": H.MOUSE_MIDDLE,
}

_TYPE_DELAY_MS = 5

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
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env).expanduser() if env else default_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    out_dir = _remedy_home() / "computer" / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{prefix}_{int(time.time() * 1000)}.png"


def purge_old_shots(*, max_age_s: float = 900.0, home_dir: Path | str | None = None) -> int:
    """Delete aged screenshots under computer/shots (privacy + disk)."""
    roots: list[Path] = []
    if home_dir is not None and str(home_dir).strip():
        roots.append(Path(home_dir).expanduser() / "computer" / "shots")
    else:
        roots.append(_remedy_home() / "computer" / "shots")
    cutoff = time.time() - float(max_age_s)
    seen: set[str] = set()
    n = 0
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            continue
        seen.add(key)
        if not root.is_dir():
            continue
        for path in list(root.iterdir()):
            try:
                if (
                    path.is_file()
                    and path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
                    and path.stat().st_mtime < cutoff
                ):
                    path.unlink(missing_ok=True)
                    n += 1
            except OSError:
                continue
    return n


# 3x5 bitmap font for Set-of-Mark digit labels.
_DIGITS_3x5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def _set_px(buf: bytearray, stride: int, w: int, h: int, x: int, y: int, bgr: tuple) -> None:
    if 0 <= x < w and 0 <= y < h:
        o = y * stride + x * 3
        buf[o], buf[o + 1], buf[o + 2] = bgr


def _draw_marks_on_bgr(
    buf: bytearray, stride: int, width: int, height: int, marks: list[dict[str, Any]]
) -> None:
    magenta = (255, 0, 255)
    white = (255, 255, 255)
    scale = 2
    for m in marks:
        label = str(int(m.get("n", 0)))
        px = int(m.get("x", 0))
        py = int(m.get("y", 0))
        box_w = len(label) * (3 * scale + scale) + scale
        box_h = 5 * scale + 2 * scale
        bx = max(0, min(px, width - box_w - 1))
        by = max(0, min(py, height - box_h - 1))
        for yy in range(by, by + box_h):
            for xx in range(bx, bx + box_w):
                _set_px(buf, stride, width, height, xx, yy, magenta)
        cx = bx + scale
        for ch in label:
            glyph = _DIGITS_3x5.get(ch)
            if glyph:
                for gy, rowbits in enumerate(glyph):
                    for gx, bit in enumerate(rowbits):
                        if bit == "1":
                            for sy in range(scale):
                                for sx in range(scale):
                                    _set_px(
                                        buf,
                                        stride,
                                        width,
                                        height,
                                        cx + gx * scale + sx,
                                        by + scale + gy * scale + sy,
                                        white,
                                    )
            cx += 3 * scale + scale


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    """Return (bgr_bytes, stride, width, height, origin_x, origin_y)."""
    _require_linux()
    try:
        shot = H.capture_virtual_screen(3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        # Headless / no DISPLAY: tiny blank so Set-of-Mark callers stay stable.
        w = h = 10
        stride = (w * 3 + 3) & ~3
        _ = exc
        return b"\x00" * (stride * h), stride, w, h, 0, 0
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def _write_png_bgr(
    path: Path,
    width: int,
    height: int,
    raw: bytes | bytearray | memoryview,
    stride: int,
    *,
    bytes_per_pixel: int = 3,
) -> None:
    """Write BGR rows as an RGB PNG via ``remedy_core``."""
    path.write_bytes(H.encode_png(raw, width, height, stride, bytes_per_pixel))


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _read_png_bgr(path: Path) -> tuple[bytes, int, int, int] | None:
    """Decode 8-bit gray/RGB/RGBA PNG to BGR + stride. None if unreadable."""
    import zlib

    data = path.read_bytes()
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    width = height = 0
    bit_depth = 8
    color_type = 2
    idat = bytearray()
    while pos + 12 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR" and len(chunk) >= 13:
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk[:10])
        elif tag == b"IDAT":
            idat.extend(chunk)
        elif tag == b"IEND":
            break
    if width < 1 or height < 1 or bit_depth != 8 or color_type not in (0, 2, 6):
        return None
    raw = zlib.decompress(bytes(idat))
    bpp = {0: 1, 2: 3, 6: 4}[color_type]
    row_in = 1 + width * bpp
    if len(raw) < row_in * height:
        return None
    prev = bytearray(width * bpp)
    rows: list[bytearray] = []
    for y in range(height):
        filt = raw[y * row_in]
        cur = bytearray(raw[y * row_in + 1 : y * row_in + row_in])
        for i, val in enumerate(cur):
            left = cur[i - bpp] if i >= bpp else 0
            up = prev[i]
            ul = prev[i - bpp] if i >= bpp else 0
            if filt == 1:
                cur[i] = (val + left) & 255
            elif filt == 2:
                cur[i] = (val + up) & 255
            elif filt == 3:
                cur[i] = (val + ((left + up) // 2)) & 255
            elif filt == 4:
                cur[i] = (val + _paeth(left, up, ul)) & 255
            elif filt != 0:
                return None
        prev = cur
        rows.append(cur)
    out_stride = (width * 3 + 3) & ~3
    buf = bytearray(out_stride * height)
    for y, cur in enumerate(rows):
        dst = y * out_stride
        if color_type == 2:
            for x in range(width):
                s = x * 3
                buf[dst + s] = cur[s + 2]
                buf[dst + s + 1] = cur[s + 1]
                buf[dst + s + 2] = cur[s]
        elif color_type == 6:
            for x in range(width):
                s = x * 4
                o = dst + x * 3
                buf[o] = cur[s + 2]
                buf[o + 1] = cur[s + 1]
                buf[o + 2] = cur[s]
        else:
            for x in range(width):
                g = cur[x]
                o = dst + x * 3
                buf[o] = buf[o + 1] = buf[o + 2] = g
    return bytes(buf), out_stride, width, height


def screenshot_png(
    path: Path | None = None,
    *,
    marks: list[Any] | None = None,
) -> dict[str, Any]:
    """Full-desktop PNG via X11 root capture in ``remedy_core``."""
    raw, stride, width, height, left, top = _capture_virtual_screen()
    if width < 2 or height < 2:
        raise RuntimeError(f"Linux screenshot failed — {_LINUX_HANDS_HINT}")
    out = Path(path) if path is not None else _default_shot_path("desk")
    out.parent.mkdir(parents=True, exist_ok=True)
    if marks:
        buf = bytearray(raw)
        img_marks = [
            {
                "n": mk.get("n"),
                "x": int(mk.get("x", 0)) - left,
                "y": int(mk.get("y", 0)) - top,
            }
            for mk in marks
            if isinstance(mk, dict)
        ]
        _draw_marks_on_bgr(buf, stride, width, height, img_marks)
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
    rx = int(round(int(x) * sc))
    ry = int(round(int(y) * sc))
    rw = max(1, int(round(int(width) * sc)))
    rh = max(1, int(round(int(height) * sc)))
    try:
        origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("region screenshot", exc) from exc
    bx = rx - origin_x
    by = ry - origin_y
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
    """Linux has no UAC secure-desktop analogue we can detect yet."""
    return {"blocked": False, "kind": "", "message": ""}


def find_remedy_desktop_hwnd() -> int | None:
    with contextlib.suppress(Exception):
        for w in list_windows(limit=80):
            title = str(w.get("title") or "").strip()
            if title == "Remedy Desktop" or title.startswith("Remedy Desktop"):
                return int(w["hwnd"])
    return None


def _atspi_clickable_candidates(*, max_marks: int = 40) -> list[dict[str, Any]]:
    """Live AT-SPI walk via ``remedy_core``. Monkeypatch in tests."""
    try:
        return list(H.a11y_snapshot(max(1, int(max_marks or 40))))
    except (NativeRuntimeUnavailableError, H.HostError):
        return []


def _atspi_snapshot_elements(cap: int) -> list[dict[str, Any]]:
    """AT-SPI clickables in snapshot shape (refs c1…), never raises."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(_atspi_clickable_candidates(max_marks=cap)):
        w = int(c.get("w") or 0)
        h = int(c.get("h") or 0)
        x = int(c.get("x") or 0)
        y = int(c.get("y") or 0)
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
    return out


def _windows_as_elements(cap: int) -> list[dict[str, Any]]:
    wins = list_windows(limit=min(cap, 80))
    out: list[dict[str, Any]] = []
    for i, w in enumerate(wins):
        b = w.get("bounds") or {}
        left, top = int(b.get("left", 0)), int(b.get("top", 0))
        right, bottom = int(b.get("right", 0)), int(b.get("bottom", 0))
        width = int(w.get("width") or max(0, right - left))
        height = int(w.get("height") or max(0, bottom - top))
        out.append(
            {
                "ref": f"w{i + 1}",
                "tag": "window",
                "role": "window",
                "name": str(w.get("title") or "")[:120],
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
                "w": width,
                "h": height,
                "hwnd": w.get("hwnd"),
                "bounds": b,
            }
        )
        if len(out) >= cap:
            break
    return out


def desktop_snapshot(
    limit: int = 40,
    mode: str = "auto",
    hwnd: int | None = None,
) -> list[dict[str, Any]]:
    """Linux interactive snapshot — AT-SPI controls (UIA analogue), else windows.

    *mode*:
      - ``windows`` — top-level windows only (refs w1…)
      - ``controls`` / ``atspi`` — AT-SPI clickable widgets (refs c1…)
      - ``auto`` — AT-SPI clickables when present, else empty (SoM/OCR fallback)
    """
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
    raw: bytes,
    stride: int,
    width: int,
    height: int,
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
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
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
            ix = float(w.get("x") or 0)
            iy = float(w.get("y") or 0)
            iw = float(w.get("w") or 0)
            ih = float(w.get("h") or 0)
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
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
) -> list[dict[str, Any]]:
    if width < 32 or height < 32 or not raw:
        return []
    step = max(2, min(width, height) // 480 * 2) or 4
    gw = max(2, width // step)
    gh = max(2, height // step)
    luma = [[0] * gw for _ in range(gh)]
    for gy in range(gh):
        base = (gy * step) * stride
        row = raw[base : base + width * 3]
        lr = luma[gy]
        for gx in range(gw):
            o = gx * step * 3
            if o + 2 >= len(row):
                break
            lr[gx] = (row[o] + (row[o + 1] << 1) + row[o + 2]) >> 2
    th = 24
    edges = [[False] * gw for _ in range(gh)]
    for gy in range(1, gh - 1):
        lp, lc, ln = luma[gy - 1], luma[gy], luma[gy + 1]
        er = edges[gy]
        for gx in range(1, gw - 1):
            c = lc[gx]
            if (
                abs(c - lc[gx - 1]) > th
                or abs(c - lc[gx + 1]) > th
                or abs(c - lp[gx]) > th
                or abs(c - ln[gx]) > th
            ):
                er[gx] = True
    boxes: list[list[int]] = []
    for gy in range(gh):
        er = edges[gy]
        gx = 0
        while gx < gw:
            if not er[gx]:
                gx += 1
                continue
            x0 = gx
            while gx < gw and er[gx]:
                gx += 1
            run = [x0, gy, gx - 1, gy]
            merged = False
            for b in boxes:
                if b[1] <= gy <= b[3] + 1 and not (run[2] < b[0] - 1 or run[0] > b[2] + 1):
                    b[0] = min(b[0], run[0])
                    b[1] = min(b[1], run[1])
                    b[2] = max(b[2], run[2])
                    b[3] = max(b[3], run[3])
                    merged = True
                    break
            if not merged:
                boxes.append(run)
    out: list[dict[str, Any]] = []
    for b in boxes:
        x0, y0, x1, y1 = (
            b[0] * step,
            b[1] * step,
            min((b[2] + 1) * step, width - 1),
            min((b[3] + 1) * step, height - 1),
        )
        w = x1 - x0
        h = y1 - y0
        if w < 12 or h < 8 or w > width * 0.9 or h > height * 0.9:
            continue
        out.append(
            {
                "x": x0 + w // 2,
                "y": y0 + h // 2,
                "w": w,
                "h": h,
                "area": w * h,
                "source": "pixels",
            }
        )
    out.sort(key=lambda c: -int(c["area"]))
    return out[: max(1, int(max_marks))]


def _merge_candidates(
    primary: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    cap: int,
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
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
) -> list[dict[str, Any]]:
    """Clickable Linux targets: AT-SPI, then OCR word boxes, then pixels."""
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
    code = _MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    try:
        H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise _host_fail("click", exc) from exc


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    x = int(el.get("x") or el.get("cx") or 0)
    y = int(el.get("y") or el.get("cy") or 0)
    click(x, y, button=button, clicks=clicks)


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
    """Type unicode text. Mirrors Windows abort_check / chars_typed contract."""
    _ = per_char
    _require_linux()
    n = 0
    raw = text or ""
    for i, ch in enumerate(raw):
        if abort_check is not None and i > 0 and i % 2 == 0:
            try:
                if abort_check():
                    if chars_typed is not None:
                        chars_typed[:] = [n]
                    raise RuntimeError("Aborted by user during type")
            except RuntimeError:
                raise
            except Exception:
                pass
        if ch == "\r" and i + 1 < len(raw) and raw[i + 1] == "\n":
            n += 1
            continue
        try:
            H.type_text(ch, _TYPE_DELAY_MS)
        except (NativeRuntimeUnavailableError, H.HostError) as exc:
            raise _host_fail("type", exc) from exc
        n += 1
    if chars_typed is not None:
        chars_typed[:] = [n]
    return n


def resolve_key_combo(key: str, *, vk_scan=None) -> list[int]:
    """'ctrl+s' / '?' / 'shift+f6' → ordered VK list (modifiers first)."""
    parts = [p.strip().lower() for p in (key or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return []
    mods: list[int] = []
    mains: list[int] = []
    for p in parts:
        if p in _VK:
            vk = _VK[p]
            (mods if vk in _MODIFIER_VKS else mains).append(vk)
        elif len(p) == 1:
            if vk_scan is None:
                vk_scan = H.vk_key_scan
            sc = int(vk_scan(ord(p)))
            if sc == -1:
                raise ValueError(f"Key has no VK mapping on this layout: {p!r}")
            shift_state = (sc >> 8) & 0xFF
            if shift_state & 1 and 0x10 not in mods:
                mods.append(0x10)
            if shift_state & 2 and 0x11 not in mods:
                mods.append(0x11)
            if shift_state & 4 and 0x12 not in mods:
                mods.append(0x12)
            mains.append(sc & 0xFF)
        else:
            raise ValueError(f"Unknown key: {p}")
    return mods + mains


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
    saved = get_clipboard_text()
    try:
        if not set_clipboard_text(data):
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
            # Preserve unspecified axis from current geometry when only move or resize.
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
    try:
        return H.list_windows(max(1, int(limit)))
    except (NativeRuntimeUnavailableError, H.HostError):
        return []


def list_monitors() -> list[dict[str, Any]]:
    try:
        return H.list_monitors()
    except (NativeRuntimeUnavailableError, H.HostError):
        return [{"index": 0, "primary": True}]


def get_clipboard_text() -> str:
    try:
        return H.clipboard_get_text()
    except (NativeRuntimeUnavailableError, H.HostError):
        return ""


def set_clipboard_text(text: str) -> bool:
    try:
        H.clipboard_set_text(str(text or ""))
    except (NativeRuntimeUnavailableError, H.HostError):
        return False
    return True


def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def open_app(name: str, search_dirs: list[str] | None = None) -> dict[str, Any]:
    """Launch via PATH / xdg-open / gtk-launch. Does not replace Windows open_app."""
    _require_linux()
    raw = (name or "").strip()
    if not raw:
        return {"ok": False, "message": "app name required"}
    if search_dirs:
        rel = Path(raw)
        if not rel.is_absolute() and ".." in rel.parts:
            raise ValueError("open_app refuses parent-directory traversal")
    aliases = {
        "files": "xdg-open",
        "file manager": "xdg-open",
        "browser": "xdg-open",
        "terminal": "x-terminal-emulator",
        "calculator": "gnome-calculator",
    }
    target = aliases.get(raw.lower(), raw)
    bin_path = shutil.which(target) if "/" not in target else target
    gtk = _which("gtk-launch")
    xdg = _which("xdg-open")
    cmd: list[str]
    if bin_path:
        cmd = [bin_path]
    elif gtk and not target.endswith(".desktop"):
        cmd = [gtk, target]
    elif xdg:
        cmd = [xdg, target]
    else:
        return {"ok": False, "message": f"no launcher for {raw!r} (install xdg-utils)"}
    try:
        subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": f"Launched {target}", "cmd": cmd}


def open_url(url: str) -> dict[str, Any]:
    """Open http(s) URL in the default system browser. Same refuse rules as Windows."""
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError(
            f"open_url refuses non-http(s) URL (got scheme/prefix {u[:32]!r})"
        )
    if any(c in u for c in ("\n", "\r", "\x00")):
        raise ValueError("open_url refuses URL with control characters")
    try:
        parsed = urlparse(u)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "open_url refuses URL with user:password@ credentials (userinfo)"
            )
    except ValueError:
        raise
    except Exception:
        if "@" in u.split("://", 1)[-1].split("/", 1)[0]:
            raise ValueError(
                "open_url refuses URL with userinfo credentials"
            ) from None
    xdg = _which("xdg-open")
    if not xdg:
        raise RuntimeError("xdg-open not found")
    subprocess.Popen(  # noqa: S603
        [xdg, u],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"url": u, "method": "xdg-open"}
