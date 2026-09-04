"""Shared desktop policy: shot paths, Set-of-Mark, pixel detect, key maps.

OS modules stay thin host_binding wrappers; pure policy lives here so win/linux
do not twin it. Host failures are the caller's concern (fail closed).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remedy.core.computer import host_binding as H
from remedy.home import default_home

PASTE_THRESHOLD = 200
TYPE_DELAY_MS = 5

VK = {
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
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "prtscn": 0x2C,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "meta": 0x5B,
    "cmd": 0x5B,
    "super": 0x5B,
}
MODIFIER_VKS = (0x10, 0x11, 0x12, 0x5B)
MOUSE_BUTTONS = {
    "left": H.MOUSE_LEFT,
    "l": H.MOUSE_LEFT,
    "right": H.MOUSE_RIGHT,
    "r": H.MOUSE_RIGHT,
    "middle": H.MOUSE_MIDDLE,
    "mid": H.MOUSE_MIDDLE,
    "m": H.MOUSE_MIDDLE,
}

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


def remedy_home() -> Path:
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env).expanduser() if env else default_home()


def default_shot_path(prefix: str = "desk") -> Path:
    out_dir = remedy_home() / "computer" / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{prefix}_{int(time.time() * 1000)}.png"


def purge_old_shots(*, max_age_s: float = 900.0, home_dir: Path | str | None = None) -> int:
    """Delete aged screenshots under computer/shots (privacy + disk)."""
    roots: list[Path] = []
    if home_dir is not None and str(home_dir).strip():
        roots.append(Path(home_dir).expanduser() / "computer" / "shots")
    else:
        roots.append(remedy_home() / "computer" / "shots")
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


def write_png_bgr(
    path: Path,
    width: int,
    height: int,
    raw: bytes | bytearray | memoryview,
    stride: int,
    *,
    bytes_per_pixel: int = 3,
) -> None:
    path.write_bytes(H.encode_png(raw, width, height, stride, bytes_per_pixel))


def _set_px(buf: bytearray, stride: int, w: int, h: int, x: int, y: int, bgr: tuple) -> None:
    if 0 <= x < w and 0 <= y < h:
        o = y * stride + x * 3
        buf[o], buf[o + 1], buf[o + 2] = bgr


def draw_marks_on_bgr(
    buf: bytearray, stride: int, width: int, height: int, marks: list[dict[str, Any]]
) -> None:
    """Draw numbered Set-of-Mark labels onto the raw BGR buffer, in place."""
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


def pixel_ui_candidates(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
) -> list[dict[str, Any]]:
    """Likely UI targets from pixels alone (games / canvas / no a11y tree)."""
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


def resolve_key_combo(key: str, *, vk_scan: Callable[[int], int] | None = None) -> list[int]:
    """'ctrl+s' / '?' / 'shift+f6' → ordered VK list (modifiers first)."""
    parts = [p.strip().lower() for p in (key or "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        return []
    mods: list[int] = []
    mains: list[int] = []
    for p in parts:
        if p in VK:
            vk = VK[p]
            (mods if vk in MODIFIER_VKS else mains).append(vk)
        elif len(p) == 1:
            scan = vk_scan or H.vk_key_scan
            sc = int(scan(ord(p)))
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


def type_text_chars(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
    send: Callable[[str], None] | None = None,
) -> int:
    """Type unicode with abort_check; *send* defaults to host type_text."""
    emit = send or (lambda ch: H.type_text(ch, TYPE_DELAY_MS))
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
        emit(ch)
        n += 1
    if chars_typed is not None:
        chars_typed[:] = [n]
    return n


def windows_as_elements(wins: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, w in enumerate(wins):
        b = w.get("bounds") or {}
        left, top = int(b.get("left", 0)), int(b.get("top", 0))
        right, bottom = int(b.get("right", 0)), int(b.get("bottom", 0))
        out.append(
            {
                "ref": f"w{i + 1}",
                "tag": "window",
                "role": "window",
                "name": str(w.get("title") or "")[:120],
                "x": (left + right) // 2,
                "y": (top + bottom) // 2,
                "w": int(w.get("width") or max(0, right - left)),
                "h": int(w.get("height") or max(0, bottom - top)),
                "hwnd": w.get("hwnd"),
                "bounds": b,
            }
        )
        if len(out) >= cap:
            break
    return out
