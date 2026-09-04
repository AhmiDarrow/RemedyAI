"""Windows desktop capture + input over the ``remedy_core`` host (no ctypes here).

Every Win32 call (capture, SendInput, window control, clipboard, DPI, PNG
encoding) is made by ``remedy_core`` through
:mod:`remedy.core.computer.host_binding`. This module keeps the policy that
sits on top: screenshot files under the Remedy home, Set-of-Mark labels,
pixel candidate detection, key-name resolution, snapshots, app and URL
launch rules.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remedy.core.computer import host_binding as H
from remedy.home import default_home

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

#: Delay between typed characters (ms); ~200 chars/s like the previous loop.
_TYPE_DELAY_MS = 5


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Desktop computer use requires Windows")


def _ensure_dpi_awareness() -> None:
    """Per-Monitor-DPI-aware v2 once, so captures and clicks use physical pixels."""
    if sys.platform != "win32":
        return
    H.dpi_awareness_enable()


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    """Return (bgr_bytes, stride, width, height, origin_x, origin_y)."""
    _require_windows()
    shot = H.capture_virtual_screen(3)
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def _remedy_home() -> Path:
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    return Path(env).expanduser() if env else default_home()


def _default_shot_path(prefix: str = "desk") -> Path:
    out_dir = _remedy_home() / "computer" / "shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{prefix}_{int(time.time() * 1000)}.png"


def purge_old_shots(*, max_age_s: float = 900.0, home_dir: Path | str | None = None) -> int:
    """Delete aged screenshots under computer/shots (privacy + disk).

    Lightweight FS sweep used after local OS captures. Host bridge
    ``purge_old`` also sweeps shots when browser jobs complete.
    """
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
                    and path.suffix.lower()
                    in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
                    and path.stat().st_mtime < cutoff
                ):
                    path.unlink(missing_ok=True)
                    n += 1
            except OSError:
                continue
    return n


# 3x5 bitmap font for Set-of-Mark digit labels (drawn on pixels, no font lib).
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
    """Draw numbered Set-of-Mark labels onto the raw BGR buffer, in place.

    Each mark: {"n": int, "x": int, "y": int} (image pixels). A magenta label
    box with white digits is placed at the point so a vision model can say
    "click mark 7" instead of estimating a pixel.
    """
    MAGENTA = (255, 0, 255)  # BGR
    WHITE = (255, 255, 255)
    scale = 2
    for m in marks:
        label = str(int(m.get("n", 0)))
        px = int(m.get("x", 0))
        py = int(m.get("y", 0))
        # label box just above/left of the point, clamped on-screen
        box_w = len(label) * (3 * scale + scale) + scale
        box_h = 5 * scale + 2 * scale
        bx = max(0, min(px, width - box_w - 1))
        by = max(0, min(py, height - box_h - 1))
        for yy in range(by, by + box_h):
            for xx in range(bx, bx + box_w):
                _set_px(buf, stride, width, height, xx, yy, MAGENTA)
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
                                        WHITE,
                                    )
            cx += 3 * scale + scale


def detect_ui_candidates(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
) -> list[dict[str, Any]]:
    """Find likely UI targets from PIXELS alone (games / canvas / no a11y tree).

    Pure-Python and fast enough to run per screenshot: sample the frame onto a
    coarse grid, mark cells with strong local contrast (edges = drawn UI), then
    merge neighbouring edge cells into candidate boxes. Returns candidate
    centers/bounds in image pixels, largest-first — the Set-of-Mark fallback
    when a snapshot has zero elements.
    """
    if width < 32 or height < 32 or not raw:
        return []
    # ~4px sampling: 4K → ~960x540 grid cells of luma.
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
            # fast integer luma (b+2g+r)/4
            lr[gx] = (row[o] + (row[o + 1] << 1) + row[o + 2]) >> 2
    # Edge cells: local gradient above threshold.
    TH = 24
    edges = [[False] * gw for _ in range(gh)]
    for gy in range(1, gh - 1):
        lp, lc, ln = luma[gy - 1], luma[gy], luma[gy + 1]
        er = edges[gy]
        for gx in range(1, gw - 1):
            c = lc[gx]
            if (
                abs(c - lc[gx - 1]) > TH
                or abs(c - lc[gx + 1]) > TH
                or abs(c - lp[gx]) > TH
                or abs(c - ln[gx]) > TH
            ):
                er[gx] = True
    # Merge edge cells into boxes via single-pass row-run + box union.
    boxes: list[list[int]] = []  # [x0,y0,x1,y1] in grid coords
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
                # touch/overlap vertically-adjacent boxes → union
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
        # UI-sized things only: skip specks and near-full-frame borders.
        if w < 12 or h < 8 or w > width * 0.9 or h > height * 0.9:
            continue
        out.append(
            {
                "x": x0 + w // 2,
                "y": y0 + h // 2,
                "w": w,
                "h": h,
                "area": w * h,
            }
        )
    out.sort(key=lambda c: -int(c["area"]))
    return out[: max(1, int(max_marks))]


def screenshot_png(
    path: Path | None = None, *, marks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Capture the virtual screen to a PNG file. Returns path + size.

    *marks*: optional Set-of-Mark labels (image-pixel coords) drawn onto the
    capture so a vision model can reference numbered targets.
    """
    raw, stride, width, height, left, top = _capture_virtual_screen()
    out = Path(path) if path is not None else _default_shot_path("desk")
    out.parent.mkdir(parents=True, exist_ok=True)
    if marks:
        buf = bytearray(raw)
        # marks are in SCREEN coords; convert to image (subtract origin)
        img_marks = [
            {"n": mk.get("n"), "x": int(mk.get("x", 0)) - left, "y": int(mk.get("y", 0)) - top}
            for mk in marks
        ]
        _draw_marks_on_bgr(buf, stride, width, height, img_marks)
        raw = bytes(buf)
    _write_png_bgr(out, width, height, raw, stride)
    # Opportunistic TTL so desktop captures do not accumulate forever.
    with contextlib.suppress(Exception):
        purge_old_shots(max_age_s=900.0, home_dir=_remedy_home())
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
    _require_windows()
    sc = float(scale) if scale and scale > 0 else 1.0
    rx = int(round(int(x) * sc))
    ry = int(round(int(y) * sc))
    rw = max(1, int(round(int(width) * sc)))
    rh = max(1, int(round(int(height) * sc)))

    origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
    # Convert screen coords → bitmap coords and clamp to the virtual screen
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


def _write_png_bgr(
    path: Path,
    width: int,
    height: int,
    raw: bytes | bytearray | memoryview,
    stride: int,
    *,
    bytes_per_pixel: int = 3,
) -> None:
    """Write BGR (or BGRA with ``bytes_per_pixel=4``) rows as an RGB PNG.

    Encoding (colour swap + zlib) runs in ``remedy_core``; this only names the
    file. Works on every platform the library builds for.
    """
    path.write_bytes(H.encode_png(raw, width, height, stride, bytes_per_pixel))


# --- input ---


def move_mouse(x: int, y: int) -> None:
    _require_windows()
    H.mouse_move(int(x), int(y))


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    _require_windows()
    code = _MOUSE_BUTTONS.get((button or "left").lower(), H.MOUSE_LEFT)
    H.mouse_click(int(x), int(y), code, max(1, int(clicks or 1)))


def drag(x1: int, y1: int, x2: int, y2: int, *, steps: int = 12) -> None:
    """Press, move in interpolated steps, pause, release.

    Explorer / list drag-drop ignores an instant start→end jump; real drags
    move through intermediate points and give the drop target a beat to
    register before button-up.
    """
    _require_windows()
    H.mouse_drag(int(x1), int(y1), int(x2), int(y2), max(2, int(steps)))


def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    _require_windows()
    H.mouse_scroll(int(x), int(y), int(dx or 0), int(dy or 0))


def _send_input(text: str) -> None:
    """Deliver one typed chunk (a character or line break) through the host."""
    H.type_text(text, _TYPE_DELAY_MS)


def type_text(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> int:
    """Type unicode text. *abort_check* callable → stop mid-string when true.

    Returns number of characters typed before completion or abort.
    Optional *chars_typed* single-element list is updated with the same count.
    """
    _require_windows()
    n = 0
    for i, ch in enumerate(text or ""):
        # Check often so Stop mid-type reacts within ~2 keystrokes.
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
        if ch == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
            # '\r\n' counts once; the '\n' sends the Return key.
            n += 1
            continue
        _send_input(ch)
        n += 1
    if chars_typed is not None:
        chars_typed[:] = [n]
    return n


def resolve_key_combo(key: str, *, vk_scan=None) -> list[int]:
    """'ctrl+s' / '?' / 'shift+f6' → ordered VK list (modifiers first).

    Honors the VkKeyScanW shift-state byte: '?', ':', '!' need Shift held —
    dropping the high byte sent the *unshifted* key ('/', ';', '1').
    *vk_scan* is injectable for tests on non-Windows.
    """
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
            # Prepend the modifiers the layout requires for this character
            if shift_state & 1 and 0x10 not in mods:
                mods.append(0x10)  # VK_SHIFT
            if shift_state & 2 and 0x11 not in mods:
                mods.append(0x11)  # VK_CONTROL
            if shift_state & 4 and 0x12 not in mods:
                mods.append(0x12)  # VK_MENU (Alt)
            mains.append(sc & 0xFF)
        else:
            raise ValueError(f"Unknown key: {p}")
    return mods + mains


def press_key(key: str) -> None:
    """Press a key or combo like 'ctrl+s', 'enter', '?', 'shift+f6'."""
    _require_windows()
    vks = resolve_key_combo(key)
    if not vks:
        return
    H.key_combo(vks)


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    """Visible, titled top-level windows (at least 8x8), most recent z-order first.

    Each entry: hwnd, title, class, pid, bounds{left,top,right,bottom},
    width, height, visible, minimized.
    """
    _require_windows()
    return H.list_windows(max(1, int(limit)))


def find_remedy_desktop_hwnd() -> int | None:
    """HWND of the running Remedy Desktop window, if any.

    Multi-monitor: her window is often *not* on monitor 0 (this machine parks
    it at negative Y). Callers that want a picture of Grove/Studio should
    PrintWindow this HWND instead of capturing a random display.
    """
    for w in list_windows(limit=80):
        title = str(w.get("title") or "").strip()
        if title == "Remedy Desktop" or title.startswith("Remedy Desktop"):
            return int(w["hwnd"])
    return None


def _window_class(hwnd: int) -> str:
    with contextlib.suppress(Exception):
        return H.window_class(int(hwnd))
    return ""


# UAC / consent runs on the SECURE DESKTOP — SendInput cannot reach it at all.
_SECURE_TITLES = ("user account control", "windows security")


def detect_system_prompt() -> dict[str, Any]:
    """Detect a foreground blocker Remedy cannot drive: a UAC / secure-desktop
    consent prompt.

    Returns {"blocked": bool, "kind": str, "message": str}. When blocked, the
    caller must return an honest "needs the owner at the keyboard" result rather
    than silently no-op'ing SendInput against the secure desktop.
    """
    with contextlib.suppress(Exception):
        fg = foreground_window_info()
        title = str(fg.get("title") or "").lower()
        cls = _window_class(int(fg.get("hwnd") or 0)).lower()
        if any(t in title for t in _SECURE_TITLES) or cls in (
            "credential dialog xaml host",
            "#32770",  # a #32770 titled like UAC — treat cautiously below
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


# Common Win32 dialog class (Save As / Open / Print / message box)
_DIALOG_CLASS = "#32770"


def find_dialog_window() -> dict[str, Any] | None:
    """The foreground common dialog (Save As / Open / message box), or None.

    These are separate top-level #32770 windows — Remedy drives their filename
    Edit and buttons by UIA just like any app; this just locates one after a
    Ctrl+S / Ctrl+O so she knows to look for it.
    """
    with contextlib.suppress(Exception):
        fg = foreground_window_info()
        hwnd = int(fg.get("hwnd") or 0)
        if hwnd and _window_class(hwnd).lower() == _DIALOG_CLASS.lower():
            return {"hwnd": hwnd, "title": str(fg.get("title") or "")}
        # Not foreground? scan visible windows for a dialog class.
        for w in list_windows(limit=30):
            wh = int(w.get("hwnd") or 0)
            if wh and str(w.get("class") or "").lower() == _DIALOG_CLASS.lower():
                return {"hwnd": wh, "title": str(w.get("title") or "")}
    return None


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
                fg = int(H.foreground_window()[0] or 0)
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
    """Capture a single HWND via PrintWindow (better for layered/WebView hosts).

    Raises RuntimeError when the window is too small or refuses PrintWindow.
    """
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
    parent: int,
    *,
    class_name: str | None = None,
    title_substr: str | None = None,
) -> int | None:
    """Find first child HWND matching class and/or title substring."""
    _require_windows()
    found = H.find_child_hwnd(int(parent), class_name or "", title_substr or "")
    return found or None


def find_webview_host_hwnd() -> int | None:
    """Best-effort: locate a WebView2 / Chromium host under a Remedy-titled window."""
    _require_windows()
    for w in list_windows(limit=200):
        title = str(w.get("title") or "").lower()
        if "remedy" not in title and "tauri" not in title:
            continue
        hwnd = int(w["hwnd"])
        # Prefer Chromium / WebView2 child
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


def focus_window(hwnd: int) -> bool:
    """Restore + foreground *hwnd*, VERIFIED.

    Windows' foreground lock can silently ignore SetForegroundWindow; the host
    checks GetForegroundWindow and falls back to AttachThreadInput and an ALT
    tap so input never lands in the wrong app. Returns True when hwnd (or a
    window sharing its root owner) is foreground.
    """
    _require_windows()
    return H.focus_window(int(hwnd))


def foreground_window_info() -> dict[str, Any]:
    """Title/hwnd of the current foreground window — act→verify evidence."""
    out: dict[str, Any] = {"hwnd": 0, "title": ""}
    with contextlib.suppress(Exception):
        hwnd, title = H.foreground_window()
        if hwnd:
            out = {"hwnd": int(hwnd), "title": title}
    return out


# --- window management verbs (minimize / maximize / restore / close / move) --

_WINDOW_ACTIONS = {
    "minimize": H.WINDOW_MINIMIZE,
    "maximize": H.WINDOW_MAXIMIZE,
    "restore": H.WINDOW_RESTORE,
}


def manage_window(
    hwnd: int,
    verb: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """minimize | maximize | restore | close | move | resize a top-level window.

    close sends WM_CLOSE (polite — the app may prompt to save; that prompt is a
    normal window Remedy can then drive). move/resize use SetWindowPos.
    """
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


# --- clipboard (as-the-user data transport + fast atomic paste) -------------


def get_clipboard_text() -> str:
    """Read CF_UNICODETEXT from the clipboard ('' when empty/non-text).

    Host failures raise :class:`host_binding.HostError` — never soft ``""``.
    """
    _require_windows()
    return H.clipboard_get_text()


def set_clipboard_text(text: str) -> bool:
    """Put *text* on the clipboard as CF_UNICODETEXT.

    Host failures raise :class:`host_binding.HostError` — never soft ``False``.
    """
    _require_windows()
    H.clipboard_set_text(str(text or ""))
    return True


# Long text via per-char SendInput is slow (~200 chars/s) and any focus change
# corrupts it mid-string. Above this length, paste atomically via clipboard.
PASTE_THRESHOLD = 200


def type_text_fast(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> dict[str, Any]:
    """Type text — atomically via clipboard-paste when long, per-char when short.

    Preserves the user's clipboard (saved and restored around the paste). Falls
    back to per-char typing when the clipboard path raises HostError, so
    behaviour stays a strict ability superset of type_text. Returns
    {"chars", "method"}.
    """
    data = str(text or "")
    if len(data) <= PASTE_THRESHOLD or "\r" in data or "\n" in data:
        # Short text (or text with newlines — apps treat pasted vs typed
        # newlines differently, e.g. chat boxes that send on Enter).
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
    """Trusted press-AND-HOLD at (x,y) — accessibility gesture for native apps."""
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
    """Focus first visible window whose title contains *title_substr* (case-insensitive)."""
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


# Docs Remedy can read itself — never ShellExecute / cmd start / Notepad.
# (Windows “Pick an app” on .md; the user does not need a window.)
_TEXT_OPEN_EXTS = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".toml",
        ".yml",
        ".yaml",
        ".json",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".rs",
        ".go",
        ".c",
        ".h",
        ".log",
        ".ini",
        ".cfg",
        ".env",
    }
)


def is_text_document_path(raw: str | Path) -> bool:
    try:
        return Path(str(raw)).suffix.lower() in _TEXT_OPEN_EXTS
    except Exception:
        return False


def refuse_os_open_text_document(path: str | Path) -> dict[str, Any]:
    """Do not launch an OS window for text Remedy can already read.

    ``start README.md`` / open_app on a .md pops Windows “Pick an app”.
    The agent should ``file_read`` instead — the user does not need Notepad.
    """
    target = Path(path).expanduser()
    raise ValueError(
        f"Do not open {target.name!r} in an OS app. Use file_read on "
        f"{str(target)[:200]} — Remedy already can read it."
    )


def _open_app_is_protocol_or_url(raw: str) -> bool:
    """True for URL/protocol-handler forms that must not hit cmd start / startfile.

    Windows drive paths (``C:\\…``) are not protocols. Single-letter schemes are
    treated as drive letters; multi-letter ``scheme:`` (javascript:, ms-msdt:,
    file:, http:, …) are refused. ``://`` always counts as a URL.
    """
    import re

    s = (raw or "").strip()
    if not s:
        return False
    if "://" in s or s.startswith("//"):
        return True
    m = re.match(r"(?i)^([a-z][a-z0-9+.-]*):", s)
    if not m:
        return False
    # One-letter scheme == drive letter (C:\path, C:foo)
    return len(m.group(1)) != 1


def open_app(
    app: str,
    *,
    search_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Launch an application by name/path (notepad, calc, explorer, full path, …).

    Fail closed on URL/protocol handlers, UNC shares, and shell metacharacters.
    Web URLs belong in ``open_url`` / navigate — not ``cmd start`` via app launch.

    *search_dirs*: project/work roots so ``game.exe`` / ``.\\hello.exe`` resolve
    to the just-built binary instead of CWD (sidecar install folder).
    """
    import re
    import shutil

    from remedy.execution.process import retain_detached, spawn_hidden

    raw = (app or "").strip()
    if not raw:
        raise ValueError("app name required")
    if any(c in raw for c in ("\n", "\r", "\x00")):
        raise ValueError("open_app refuses control characters")
    # UNC / remote share — never auto-launch from agent tool path
    if raw.startswith(("\\\\", "//")) or raw.startswith("\\"):
        raise ValueError("open_app refuses UNC / share paths")
    if any(c in raw for c in ("&", "|", ">", "<", "^", "%", "`", ";")):
        raise ValueError("open_app refuses shell metacharacters")
    if _open_app_is_protocol_or_url(raw):
        raise ValueError(
            f"open_app refuses URL/protocol handler (got {raw[:48]!r}); "
            "use computer_navigate / open_url for web, or an app name/path"
        )
    # Path-jail checks are OS-independent so Linux CI can still prove them.
    # Normalize Windows separators first — Path.parts on POSIX treats `\` as
    # a filename character, so `..\Windows\…` would otherwise miss `..`.
    rel_probe = Path(raw.replace("\\", "/"))
    if search_dirs and not rel_probe.is_absolute() and ".." in rel_probe.parts:
        raise ValueError("open_app refuses parent-directory traversal")

    _require_windows()

    aliases = {
        "notepad": "notepad.exe",
        "calc": "calc.exe",
        "calculator": "calc.exe",
        "explorer": "explorer.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "pwsh": "pwsh.exe",
        "edge": "msedge.exe",
        "chrome": "chrome.exe",
        "firefox": "firefox.exe",
        "settings": "ms-settings:",
        "terminal": "wt.exe",
    }
    key = raw.lower()
    target = aliases.get(key, raw)
    # Only the explicit settings alias may open ms-settings: (no free-form protocols)
    if key == "settings" and target == "ms-settings:":
        os.startfile(target)
        return {"app": raw, "method": "startfile", "target": target}
    # Absolute / existing path only when the file is present (no bare "C:…" probe)
    path_candidate = Path(target)
    # Project roots first — sidecar CWD may have a leftover game.exe
    if search_dirs and not path_candidate.is_absolute():
        rel = Path(target)
        if ".." in rel.parts:
            raise ValueError("open_app refuses parent-directory traversal")
        from remedy.core.workspace import path_in_roots, resolve_existing_path

        for d in search_dirs:
            try:
                root = resolve_existing_path(d)
            except (OSError, TypeError, ValueError):
                continue
            for cand in (root / rel, root / rel.name):
                try:
                    resolved = resolve_existing_path(cand)
                except (OSError, TypeError, ValueError):
                    continue
                if not path_in_roots(resolved, [root]):
                    continue
                if resolved.is_file():
                    if is_text_document_path(resolved):
                        return refuse_os_open_text_document(resolved)
                    retain_detached(spawn_hidden([str(resolved)]))
                    return {
                        "app": raw,
                        "method": "project_path",
                        "target": str(resolved),
                    }
    if path_candidate.is_file() and path_candidate.is_absolute():
        if is_text_document_path(path_candidate):
            return refuse_os_open_text_document(path_candidate)
        retain_detached(spawn_hidden([str(path_candidate)]))
        return {"app": raw, "method": "path", "target": str(path_candidate)}
    if path_candidate.is_dir() and (
        path_candidate.is_absolute() or (search_dirs and path_candidate.exists())
    ):
        from remedy.core.open_folder import open_folder_os

        info = open_folder_os(path_candidate)
        return {"app": raw, **info}
    if is_text_document_path(raw):
        return refuse_os_open_text_document(raw)
    # Drive-letter path that is not an existing file — fail closed (was Popen anyway)
    if len(target) > 2 and target[1] == ":" and target[0].isalpha() and (
        "/" in target or "\\" in target
    ):
        raise ValueError(f"open_app path not found: {target[:80]}")
    which = shutil.which(target) or shutil.which(raw)
    if which:
        retain_detached(spawn_hidden([which]))
        return {"app": raw, "method": "which", "target": which}
    # Appliances: anything in her house (Start Menu inventory), natural name.
    # "spotify", "word", "steam" resolve here without hardcoded aliases.
    try:
        from remedy.core.computer.appliances import best_appliance

        hit = best_appliance(raw)
        if hit is not None:
            lnk = Path(hit.path)
            if lnk.is_file() and lnk.suffix.lower() == ".lnk":
                # Launch via the app's own shortcut: preserves its intended
                # args/working dir. Path comes from our Start Menu scan only.
                os.startfile(str(lnk))  # noqa: S606 — trusted scan root
                return {
                    "app": raw,
                    "method": "appliance",
                    "target": hit.name,
                    "path": str(lnk),
                }
    except Exception:
        pass
    # Last resort: cmd start only for simple registered app names (no path seps)
    if "/" in raw or "\\" in raw or ":" in raw:
        raise ValueError(
            f"open_app could not resolve {raw[:80]!r} (no path / PATH entry)"
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ +\-]*", raw):
        hint = ""
        try:
            from remedy.core.computer.appliances import suggestions_line

            hint = suggestions_line(raw)
        except Exception:
            hint = ""
        raise ValueError(
            f"open_app refuses unsafe app name for shell start: {raw[:48]!r}"
            + (f" · {hint}" if hint else "")
        )
    retain_detached(spawn_hidden(["cmd", "/c", "start", "", raw]))
    return {"app": raw, "method": "cmd start", "target": raw}


def open_url(url: str) -> dict[str, Any]:
    """Open http(s) URL in the default system browser (Windows-reliable).

    Fail closed: only ``http://`` / ``https://`` are allowed. ``file://``,
    ``javascript:``, bare paths, and other schemes can launch local files or
    handlers via ``os.startfile`` / ``cmd start`` — never pass those through.
    """
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError(
            f"open_url refuses non-http(s) URL (got scheme/prefix {u[:32]!r})"
        )
    # Block credentials in userinfo and obvious newlines/control chars
    if any(c in u for c in ("\n", "\r", "\x00")):
        raise ValueError("open_url refuses URL with control characters")
    try:
        from urllib.parse import urlparse

        parsed = urlparse(u)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "open_url refuses URL with user:password@ credentials (userinfo)"
            )
    except ValueError:
        raise
    except Exception:
        # Fail closed on unparseable credentials forms
        if "@" in u.split("://", 1)[-1].split("/", 1)[0]:
            raise ValueError(
                "open_url refuses URL with userinfo credentials"
            ) from None
    if sys.platform == "win32":
        # os.startfile is more reliable than webbrowser on Windows (default app).
        try:
            os.startfile(u)
            return {"url": u, "method": "os.startfile"}
        except OSError:
            from remedy.execution.process import retain_detached, spawn_hidden

            # Empty title arg after start is required for URLs with &
            retain_detached(spawn_hidden(["cmd", "/c", "start", "", u]))
            return {"url": u, "method": "cmd start"}
    import webbrowser

    webbrowser.open(u)
    return {"url": u, "method": "webbrowser"}


def list_monitors() -> list[dict[str, Any]]:
    """Enumerate display monitors (physical pixels, DPI-aware).

    Each entry: index, left, top, right, bottom, width, height, primary,
    scale (effective DPI / 96) and remedy (True on the monitor that holds the
    Remedy Desktop window).
    """
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
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            for m in monitors:
                if m["left"] <= cx < m["right"] and m["top"] <= cy < m["bottom"]:
                    m["remedy"] = True
                    break
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
