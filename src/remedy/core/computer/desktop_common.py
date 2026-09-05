"""Shared desktop policy + POSIX desktop facade over ``host_binding``.

Pure SoM / keys / shot / pixel policy lives here so Windows siblings do not twin
it. On non-Windows, :func:`host_binding.native` returns this module — AT-SPI
detect, capture/input wrappers, and launch stay fail-closed over Zig.
"""

from __future__ import annotations

import contextlib
import functools
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from remedy.core.computer import host_binding as H
from remedy.home import default_home
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

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


def merge_ui_candidates(
    primary: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    cap: int,
    *,
    cell: int = 12,
) -> list[dict[str, Any]]:
    """Dedup UI targets by grid cell (default ~12px); primary wins order."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    step = max(1, int(cell or 1))
    for c in list(primary) + list(extra):
        if not isinstance(c, dict):
            continue
        try:
            x, y = int(c["x"]), int(c["y"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (x // step, y // step)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= cap:
            break
    return out


def ocr_words_from_bgr(
    raw: bytes, stride: int, width: int, height: int
) -> list[dict[str, Any]]:
    """Temp PNG → OCR words; empty on tiny frames or backend failure."""
    import contextlib

    if width < 32 or height < 32 or not raw:
        return []
    path = default_shot_path("ocr-detect")
    try:
        write_png_bgr(path, width, height, raw, stride)
        from remedy.core.computer.ocr import read_screenshot_ocr

        result = read_screenshot_ocr(path)
        words = result.get("words") if isinstance(result, dict) else None
        return list(words or [])
    except Exception:
        return []
    finally:
        with contextlib.suppress(OSError):
            path.unlink(missing_ok=True)


def ocr_word_candidates(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
    words_from: Callable[..., list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """OCR word boxes as clickable candidates (center + size)."""
    cap = max(1, int(max_marks or 20))
    fetch = words_from or ocr_words_from_bgr
    out: list[dict[str, Any]] = []
    for w in fetch(raw, stride, width, height):
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


def clip_region_to_virtual(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    scale: float,
    origin_x: int,
    origin_y: int,
    full_w: int,
    full_h: int,
) -> tuple[int, int, int, int, float]:
    """Map requested region into virtual-screen crop box (bx,by,rw,rh,sc)."""
    sc = float(scale) if scale and scale > 0 else 1.0
    rx, ry = int(round(int(x) * sc)), int(round(int(y) * sc))
    rw, rh = max(1, int(round(int(width) * sc))), max(1, int(round(int(height) * sc)))
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
    return bx, by, rw, rh, sc


def apply_marks_offset(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    marks: list[Any] | None,
    *,
    origin_left: int,
    origin_top: int,
    require_dict: bool = False,
) -> bytes:
    """Draw SoM marks shifted from screen coords into buffer-local coords."""
    if not marks:
        return raw
    buf = bytearray(raw)
    drawn: list[dict[str, Any]] = []
    for mk in marks:
        if require_dict and not isinstance(mk, dict):
            continue
        if not isinstance(mk, dict):
            continue
        drawn.append(
            {
                "n": mk.get("n"),
                "x": int(mk.get("x", 0)) - origin_left,
                "y": int(mk.get("y", 0)) - origin_top,
            }
        )
    if drawn:
        draw_marks_on_bgr(buf, stride, width, height, drawn)
    return bytes(buf)


def finalize_shot(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    path: Path | None,
    prefix: str,
    origin_x: int,
    origin_y: int,
    marks: list[Any] | None = None,
    require_dict_marks: bool = False,
    extra: dict[str, Any] | None = None,
    purge: bool = True,
) -> dict[str, Any]:
    """Write PNG (optional SoM), purge aged shots, return standard shot dict."""
    import contextlib

    pixels = apply_marks_offset(
        raw,
        stride,
        width,
        height,
        marks,
        origin_left=origin_x,
        origin_top=origin_y,
        require_dict=require_dict_marks,
    )
    out = Path(path) if path is not None else default_shot_path(prefix)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png_bgr(out, width, height, pixels, stride)
    if purge:
        with contextlib.suppress(Exception):
            purge_old_shots(max_age_s=900.0, home_dir=remedy_home())
    info: dict[str, Any] = {
        "path": str(out),
        "width": width,
        "height": height,
        "origin": {"x": origin_x, "y": origin_y},
    }
    if extra:
        info.update(extra)
    return info


def run_type_text_fast(
    text: str,
    *,
    type_text: Callable[..., int],
    get_clipboard: Callable[[], str],
    set_clipboard: Callable[[str], bool],
    press_key: Callable[[str], None],
    host_error: type[BaseException] = H.HostError,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
    paste_sleep_s: float = 0.15,
) -> dict[str, Any]:
    """Paste long text via clipboard; short / multiline stays keystrokes."""
    data = str(text or "")
    if len(data) <= PASTE_THRESHOLD or "\r" in data or "\n" in data:
        n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
        return {"chars": n, "method": "keystrokes"}
    try:
        saved = get_clipboard()
    except host_error:
        n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
        return {"chars": n, "method": "keystrokes"}
    try:
        try:
            set_clipboard(data)
        except host_error:
            n = type_text(data, abort_check=abort_check, chars_typed=chars_typed)
            return {"chars": n, "method": "keystrokes"}
        press_key("ctrl+v")
        time.sleep(paste_sleep_s)
        if chars_typed is not None:
            chars_typed[:] = [len(data)]
        return {"chars": len(data), "method": "paste"}
    finally:
        with contextlib.suppress(Exception):
            set_clipboard(saved)


def run_press_hold(
    x: int,
    y: int,
    *,
    mouse_move: Callable[[int, int], None],
    mouse_down: Callable[[], None],
    mouse_up: Callable[[], None],
    hold_ms: int = 2600,
    abort_check: Callable[[], bool] | None = None,
    pre_hold_sleep_s: float = 0.05,
) -> dict[str, Any]:
    """Move, press, hold with abort_check, release. Caller owns fail-closed."""
    mouse_move(int(x), int(y))
    time.sleep(pre_hold_sleep_s)
    mouse_down()
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
        mouse_up()
    return {"held_ms": int(min(held, total) * 1000), "x": x, "y": y}


def find_title_hwnd(
    windows: list[dict[str, Any]],
    *,
    exact: str | None = None,
    prefix: str | None = None,
    substr: str | None = None,
) -> tuple[int, str] | None:
    """First window matching exact / prefix / casefold substr."""
    needle = (substr or "").strip().lower()
    for w in windows:
        title = str(w.get("title") or "")
        stripped = title.strip()
        if exact is not None and stripped == exact:
            return int(w["hwnd"]), title
        if prefix is not None and stripped.startswith(prefix):
            return int(w["hwnd"]), title
        if needle and needle in title.lower():
            return int(w["hwnd"]), title
    return None


WINDOW_ACTIONS = {
    "minimize": H.WINDOW_MINIMIZE,
    "maximize": H.WINDOW_MAXIMIZE,
    "restore": H.WINDOW_RESTORE,
    "close": H.WINDOW_CLOSE,
    "move": H.WINDOW_MOVE_RESIZE,
    "resize": H.WINDOW_MOVE_RESIZE,
}


def window_action(verb: str) -> int | None:
    return WINDOW_ACTIONS.get((verb or "").strip().lower())


def manage_window_message(
    verb: str,
    hwnd: int,
    *,
    nx: int | None = None,
    ny: int | None = None,
    nw: int | None = None,
    nh: int | None = None,
) -> dict[str, Any]:
    v = (verb or "").strip().lower()
    if v == "close":
        return {
            "ok": True,
            "message": (
                f"Sent close to hwnd={hwnd} (the app may show a save prompt — "
                "snapshot to see it)"
            ),
        }
    if v in ("move", "resize") and None not in (nx, ny, nw, nh):
        return {"ok": True, "message": f"{v} hwnd={hwnd} → ({nx},{ny}) {nw}x{nh}"}
    return {"ok": True, "message": f"{v} hwnd={hwnd}"}


def screenshot_monitor_from_list(
    monitors: list[dict[str, Any]],
    index: int,
    *,
    path: Path | None,
    region_shot: Callable[..., dict[str, Any]],
    full_shot: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    if not monitors:
        return full_shot(path)
    idx = int(index)
    if idx < 0 or idx >= len(monitors):
        raise ValueError(f"monitor index {idx} out of range 0..{len(monitors) - 1}")
    m = monitors[idx]
    left = int(m.get("left", 0))
    top = int(m.get("top", 0))
    width = int(m.get("width") or max(0, int(m.get("right", 0)) - left))
    height = int(m.get("height") or max(0, int(m.get("bottom", 0)) - top))
    return region_shot(left, top, width, height, path=path, scale=1.0)


def a11y_as_elements(cands: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """AT-SPI / a11y clickables → snapshot element dicts."""
    out: list[dict[str, Any]] = []
    for i, c in enumerate(cands):
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
                "source": str(c.get("source") or "atspi"),
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


def compose_desktop_snapshot(
    *,
    limit: int = 40,
    mode: str = "auto",
    hwnd: int | None = None,
    list_windows_fn: Callable[..., list[dict[str, Any]]],
    controls_fn: Callable[[int | None, int], list[dict[str, Any]]] | None = None,
    foreground_hwnd_fn: Callable[[], int | None] | None = None,
    controls_modes: frozenset[str] | set[str] | None = None,
    prefer_controls_alone: bool = False,
    merge_cell: int = 1,
) -> list[dict[str, Any]]:
    """Windows/controls/auto snapshot merge used by both OS modules."""
    mode_s = (mode or "auto").strip().lower()
    cap = max(1, min(int(limit or 40), 100))
    deep = controls_modes or frozenset({"controls", "uia", "deep", "atspi"})
    wins = list_windows_fn(limit=min(cap, 80))
    win_els = windows_as_elements(wins, cap)
    if mode_s == "windows":
        return win_els[:cap]
    ctrl_els: list[dict[str, Any]] = []
    if controls_fn is not None:
        root = hwnd
        if root is None and wins:
            fg = None
            if foreground_hwnd_fn is not None:
                with __import__("contextlib").suppress(Exception):
                    fg = foreground_hwnd_fn()
            root = fg or wins[0].get("hwnd")
        try:
            ctrl_els = list(controls_fn(root, cap) or [])
        except Exception:
            ctrl_els = []
    if mode_s in deep:
        if prefer_controls_alone:
            return ctrl_els[:cap]
        return (ctrl_els or win_els)[:cap]
    if prefer_controls_alone:
        return ctrl_els[:cap] if ctrl_els else []
    return merge_ui_candidates(win_els, ctrl_els, cap, cell=merge_cell)


def annotate_monitors(
    monitors: list[dict[str, Any]],
    *,
    remedy_hwnd: int | None = None,
    window_rect_fn: Callable[[int], tuple[int, int, int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Mark primary fallback + which monitor hosts Remedy Desktop."""
    for m in monitors:
        m["remedy"] = False
    if monitors and not any(m.get("primary") for m in monitors):
        monitors[0]["primary"] = True
    if remedy_hwnd and window_rect_fn is not None:
        import contextlib

        with contextlib.suppress(Exception):
            left, top, right, bottom = window_rect_fn(int(remedy_hwnd))
            cx, cy = (left + right) // 2, (top + bottom) // 2
            for m in monitors:
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    m["remedy"] = True
                    break
    return monitors


def manage_window_dispatch(
    hwnd: int,
    verb: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
    window_rect_fn: Callable[[int], tuple[int, int, int, int]],
    manage_fn: Callable[..., None],
    fail_soft: bool = False,
    require_partial_args: bool = False,
) -> dict[str, Any]:
    """Shared window verb dispatch; fail_soft returns ok:False instead of raise."""
    v = (verb or "").strip().lower()
    if not hwnd:
        return {"ok": False, "message": "hwnd required"}
    action = window_action(v)
    if action is None:
        return {"ok": False, "message": f"Unknown window verb {verb!r}"}
    nx = int(x) if x is not None else 0
    ny = int(y) if y is not None else 0
    nw = int(width) if width is not None else 0
    nh = int(height) if height is not None else 0
    try:
        if v in ("move", "resize"):
            if require_partial_args:
                if v == "move" and (x is None or y is None):
                    return {"ok": False, "message": "move requires x and y"}
                if v == "resize" and (width is None or height is None):
                    return {"ok": False, "message": "resize requires width and height"}
            if v == "move" or width is None or height is None or x is None or y is None:
                import contextlib

                with contextlib.suppress(Exception):
                    left, top, right, bottom = window_rect_fn(int(hwnd))
                    if x is None:
                        nx = left
                    if y is None:
                        ny = top
                    if width is None:
                        nw = max(0, right - left)
                    if height is None:
                        nh = max(0, bottom - top)
            manage_fn(int(hwnd), action, nx, ny, nw, nh)
            return manage_window_message(v, hwnd, nx=nx, ny=ny, nw=nw, nh=nh)
        manage_fn(int(hwnd), action, nx, ny, nw, nh)
    except Exception as exc:
        if fail_soft:
            return {"ok": False, "message": f"{v} hwnd={hwnd} failed: {exc}"}
        raise
    return manage_window_message(v, hwnd, nx=nx, ny=ny, nw=nw, nh=nh)


# --- POSIX desktop facade (host_binding.native on non-Windows) ----------------

_LINUX_HANDS_HINT = (
    "needs an X11 display with XTest (DISPLAY set; XWayland works). "
    "Pure Wayland without XWayland is not supported yet"
)
_F = TypeVar("_F", bound=Callable[..., Any])


def _require_linux() -> None:
    if sys.platform == "win32":
        raise RuntimeError("POSIX desktop computer use only")


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
    return remedy_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    return default_shot_path(prefix)


def _write_png_bgr(
    path: Path, width: int, height: int, raw: bytes | bytearray | memoryview, stride: int, *, bytes_per_pixel: int = 3
) -> None:
    write_png_bgr(path, width, height, raw, stride, bytes_per_pixel=bytes_per_pixel)


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
        return finalize_shot(
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
    bx, by, rw, rh, sc = clip_region_to_virtual(
        x, y, width, height, scale=scale, origin_x=origin_x, origin_y=origin_y, full_w=full_w, full_h=full_h
    )
    crop = H.capture_region(origin_x + bx, origin_y + by, rw, rh, 3)
    return finalize_shot(
        crop.pixels, crop.stride, rw, rh, path=path, prefix="region",
        origin_x=origin_x + bx, origin_y=origin_y + by, purge=False,
        extra={"requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc}, "method": "remedy_core"},
    )


def screenshot_monitor_png(index: int, path: Path | None = None) -> dict[str, Any]:
    return screenshot_monitor_from_list(
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
    return finalize_shot(
        shot.pixels, shot.stride, shot.width, shot.height, path=path, prefix="hwnd",
        origin_x=shot.left, origin_y=shot.top, purge=False, extra={"method": "remedy_core"},
    )


def find_webview_host_hwnd() -> int | None:
    return None


def detect_system_prompt() -> dict[str, Any]:
    return {"blocked": False, "kind": "", "message": ""}


def find_remedy_desktop_hwnd() -> int | None:
    with contextlib.suppress(Exception):
        hit = find_title_hwnd(list_windows(limit=80), exact="Remedy Desktop", prefix="Remedy Desktop")
        return hit[0] if hit else None
    return None


def _atspi_clickable_candidates(*, max_marks: int = 40) -> list[dict[str, Any]]:
    try:
        return list(H.a11y_snapshot(max(1, int(max_marks or 40))))
    except NativeRuntimeUnavailableError:
        return []


def _atspi_snapshot_elements(cap: int) -> list[dict[str, Any]]:
    return a11y_as_elements(_atspi_clickable_candidates(max_marks=cap), cap)


def _windows_as_elements(cap: int) -> list[dict[str, Any]]:
    return windows_as_elements(list_windows(limit=min(cap, 80)), cap)


def desktop_snapshot(limit: int = 40, mode: str = "auto", hwnd: int | None = None) -> list[dict[str, Any]]:
    return compose_desktop_snapshot(
        limit=limit, mode=mode, hwnd=hwnd, list_windows_fn=list_windows,
        controls_fn=lambda _root, cap: _atspi_snapshot_elements(cap), prefer_controls_alone=True,
        controls_modes=frozenset({"controls", "uia", "deep", "atspi"}),
    )


def _ocr_words_from_bgr(raw: bytes, stride: int, width: int, height: int) -> list[dict[str, Any]]:
    return ocr_words_from_bgr(raw, stride, width, height)


def _ocr_word_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    return ocr_word_candidates(
        raw, stride, width, height, max_marks=max_marks, words_from=_ocr_words_from_bgr
    )


def _pixel_ui_candidates(
    raw: bytes, stride: int, width: int, height: int, *, max_marks: int = 20
) -> list[dict[str, Any]]:
    return pixel_ui_candidates(raw, stride, width, height, max_marks=max_marks)


def _merge_candidates(
    primary: list[dict[str, Any]], extra: list[dict[str, Any]], cap: int
) -> list[dict[str, Any]]:
    return merge_ui_candidates(primary, extra, cap)


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
    code = MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
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
            H.type_text(ch, TYPE_DELAY_MS)
        except (NativeRuntimeUnavailableError, H.HostError) as exc:
            raise _host_fail("type", exc) from exc

    return type_text_chars(text, abort_check=abort_check, chars_typed=chars_typed, send=_send)


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
    return run_type_text_fast(
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

    return run_press_hold(
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
    return manage_window_dispatch(
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
