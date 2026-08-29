"""Linux desktop capture + input (xdotool / grim / xdg-open). Additive to Windows."""

from __future__ import annotations

import contextlib
import shutil
import struct
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from remedy.home import default_home

# Match Windows paste threshold so type_text_fast behaves the same under tests.
PASTE_THRESHOLD = 120


def _require_linux() -> None:
    if sys.platform == "win32":
        raise RuntimeError("desktop_linux is for POSIX desktops")


def _home_shots() -> Path:
    return default_home() / "computer" / "shots"


def _default_shot_path(kind: str) -> Path:
    root = _home_shots()
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{kind}-{int(time.time() * 1000)}.png"


def _run(cmd: list[str], *, timeout: float = 8.0) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        cmd,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


_LINUX_HANDS_HINT = (
    "install grim (or scrot), xdotool or ydotool, and wmctrl "
    "(Debian Recommends on the .deb)"
)


def _pointer_backend() -> tuple[str, str] | None:
    """('xdotool'|'ydotool', path) — keep both platforms."""
    xd = _which("xdotool")
    if xd:
        return ("xdotool", xd)
    yd = _which("ydotool")
    if yd:
        return ("ydotool", yd)
    return None


def _missing_hands(need: str) -> RuntimeError:
    return RuntimeError(f"Linux {need} needs xdotool or ydotool — {_LINUX_HANDS_HINT}")


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        import struct

        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return 0, 0


def screenshot_png(
    path: Path | None = None,
    *,
    marks: list[Any] | None = None,
) -> dict[str, Any]:
    """Full-desktop PNG via grim (Wayland) or import/scrot/gnome-screenshot (X11)."""
    _require_linux()
    _ = marks
    out = Path(path) if path is not None else _default_shot_path("desktop")
    out.parent.mkdir(parents=True, exist_ok=True)
    tools: list[list[str]] = []
    grim = _which("grim")
    if grim:
        tools.append([grim, str(out)])
    gnome = _which("gnome-screenshot")
    if gnome:
        tools.append([gnome, "-f", str(out)])
    scrot = _which("scrot")
    if scrot:
        tools.append([scrot, "-o", str(out)])
    magick = _which("import", "magick")
    if magick:
        if Path(magick).name == "magick":
            tools.append([magick, "import", "-window", "root", str(out)])
        else:
            tools.append([magick, "-window", "root", str(out)])
    last_err = "no screenshot tool (install grim, scrot, or ImageMagick import)"
    for cmd in tools:
        try:
            proc = _run(cmd, timeout=12.0)
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_err = str(exc)
            continue
        if proc.returncode == 0 and out.is_file() and out.stat().st_size > 32:
            w, h = _png_size(out)
            return {"path": str(out), "width": w, "height": h, "method": cmd[0]}
        last_err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")[:240]
    raise RuntimeError(f"Linux screenshot failed: {last_err}")


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
    out = Path(path) if path is not None else _default_shot_path("region")
    out.parent.mkdir(parents=True, exist_ok=True)
    grim = _which("grim")
    if grim:
        geom = f"{rx},{ry} {rw}x{rh}"
        proc = _run([grim, "-g", geom, str(out)], timeout=12.0)
        if proc.returncode == 0 and out.is_file():
            w, h = _png_size(out)
            return {
                "path": str(out),
                "width": w,
                "height": h,
                "origin": {"x": rx, "y": ry},
                "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
                "method": "grim",
            }
    magick = _which("import")
    if magick:
        proc = _run(
            [magick, "-window", "root", "-crop", f"{rw}x{rh}+{rx}+{ry}", str(out)],
            timeout=12.0,
        )
        if proc.returncode == 0 and out.is_file():
            w, h = _png_size(out)
            return {
                "path": str(out),
                "width": w,
                "height": h,
                "origin": {"x": rx, "y": ry},
                "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
                "method": "import",
            }
    raise RuntimeError(
        "Linux region screenshot needs grim or ImageMagick import — "
        f"{_LINUX_HANDS_HINT}. Not returning a full-desktop PNG as a rail crop."
    )


def screenshot_monitor_png(index: int, path: Path | None = None) -> dict[str, Any]:
    _ = index
    return screenshot_png(path)


def print_window_png(hwnd: int | None = None, path: Path | None = None) -> dict[str, Any]:
    _ = hwnd
    return screenshot_png(path)


def find_webview_host_hwnd() -> int | None:
    return None


def detect_system_prompt() -> dict[str, Any]:
    """Linux has no UAC secure-desktop analogue we can detect yet."""
    return {"blocked": False, "kind": "", "message": ""}


def find_remedy_desktop_hwnd() -> int | None:
    return None


def desktop_snapshot(
    limit: int = 40,
    mode: str = "auto",
    hwnd: int | None = None,
) -> list[dict[str, Any]]:
    """Linux interactive snapshot — AT-SPI controls (UIA analogue), else windows.

    *mode*:
      - ``windows`` — top-level windows only (refs w1…)
      - ``controls`` / ``atspi`` — AT-SPI clickable widgets (refs c1…)
      - ``auto`` — AT-SPI clickables when present, else windows
    Empty auto/controls keeps Set-of-Mark on ``detect_ui_candidates``
    (OCR / pixel edges) instead of marking huge window frames.
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


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    """Best-effort frame for Set-of-Mark pixel / OCR fallback.

    Linux CI often has no grim/scrot — return a tiny blank buffer so callers
    (and tests that monkeypatch this) still get a stable shape instead of a
    hard Windows-only error.
    """
    _require_linux()
    with contextlib.suppress(Exception):
        info = screenshot_png()
        path = Path(str(info.get("path") or ""))
        if path.is_file():
            decoded = _read_png_bgr(path)
            if decoded is not None:
                raw, stride, w, h = decoded
                return raw, stride, w, h, 0, 0
            w = int(info.get("width") or 0)
            h = int(info.get("height") or 0)
            if w > 0 and h > 0:
                stride = (w * 3 + 3) & ~3
                return b"\x00" * (stride * h), stride, w, h, 0, 0
    w = h = 10
    stride = (w * 3 + 3) & ~3
    return b"\x00" * (stride * h), stride, w, h, 0, 0


# GTK/Qt/GNOME roles that are worth a Set-of-Mark box. Huge chrome is skipped.
_ATSPI_CLICK_ROLES = frozenset(
    {
        "push button",
        "button",
        "toggle button",
        "toggle",
        "radio button",
        "radio",
        "check box",
        "checkbox",
        "menu item",
        "check menu item",
        "radio menu item",
        "link",
        "hyperlink",
        "entry",
        "password text",
        "text",
        "edit",
        "editbar",
        "spin button",
        "combo box",
        "combobox",
        "slider",
        "page tab",
        "tab",
        "list item",
        "tree item",
        "heading",
        "image",
        "icon",
        "split button",
        "tool bar",
        "toolbar",
    }
)
_ATSPI_SKIP_ROLES = frozenset(
    {
        "application",
        "frame",
        "window",
        "desktop frame",
        "filler",
        "separator",
        "scroll bar",
        "scrollbar",
        "redundant object",
        "bounding box",
        "layered pane",
        "html container",
        "document web",
        "document frame",
        "page tab list",
        "menu bar",
        "menubar",
        "status bar",
        "statusbar",
        "split pane",
        "panel",
        "unknown",
        "invalid",
    }
)


def _atspi_call(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            with contextlib.suppress(Exception):
                return fn()
        elif fn is not None and not callable(fn):
            return fn
    return default


def _atspi_extents(acc: Any) -> tuple[int, int, int, int] | None:
    """Return (x, y, w, h) in screen pixels, or None."""
    comp = None
    for name in (
        "get_component_iface",
        "get_component",
        "queryComponent",
        "query_component",
    ):
        fn = getattr(acc, name, None)
        if not callable(fn):
            continue
        with contextlib.suppress(Exception):
            comp = fn()
        if comp is not None:
            break
    if comp is None:
        return None
    rect = None
    for name in ("get_extents", "getExtents"):
        fn = getattr(comp, name, None)
        if not callable(fn):
            continue
        for coord in (0, None):
            with contextlib.suppress(Exception):
                rect = fn() if coord is None else fn(coord)
            if rect is not None:
                break
        if rect is not None:
            break
    if rect is None:
        return None
    try:
        x = int(rect.x)
        y = int(rect.y)
        w = int(rect.width)
        h = int(rect.height)
    except Exception:
        try:
            x = int(rect[0])  # type: ignore[index]
            y = int(rect[1])  # type: ignore[index]
            w = int(rect[2])  # type: ignore[index]
            h = int(rect[3])  # type: ignore[index]
        except Exception:
            return None
    if w < 4 or h < 4:
        return None
    return x, y, w, h


def _atspi_children(acc: Any) -> list[Any]:
    n = 0
    raw_n = _atspi_call(acc, "get_child_count", "getChildCount", default=None)
    if raw_n is None:
        raw_n = getattr(acc, "childCount", 0)
    with contextlib.suppress(Exception):
        n = int(raw_n or 0)
    kids: list[Any] = []
    for i in range(max(0, min(n, 80))):
        child = None
        for name in ("get_child_at_index", "getChildAtIndex"):
            fn = getattr(acc, name, None)
            if callable(fn):
                with contextlib.suppress(Exception):
                    child = fn(i)
                if child is not None:
                    break
        if child is None:
            with contextlib.suppress(Exception):
                child = acc[i]
        if child is not None:
            kids.append(child)
    return kids


def _walk_atspi_tree(root: Any, *, max_marks: int = 40) -> list[dict[str, Any]]:
    """BFS AT-SPI accessibles to candidate dicts. Pure; tests fake the tree."""
    cap = max(1, int(max_marks or 40))
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    queue: list[tuple[Any, int]] = [(root, 0)]
    visited = 0
    while queue and len(out) < cap and visited < 500:
        acc, depth = queue.pop(0)
        visited += 1
        ident = id(acc)
        if ident in seen or depth > 12:
            continue
        seen.add(ident)
        role = str(
            _atspi_call(acc, "get_role_name", "getRoleName", default="") or ""
        ).strip().lower()
        name = str(
            _atspi_call(acc, "get_name", "getName", default="") or ""
        ).strip()
        extents = _atspi_extents(acc)
        if (
            extents is not None
            and role not in _ATSPI_SKIP_ROLES
            and (role in _ATSPI_CLICK_ROLES or (name and extents[2] < 800))
        ):
            x0, y0, w, h = extents
            if w < 4000 and h < 3000:
                out.append(
                    {
                        "x": x0 + w // 2,
                        "y": y0 + h // 2,
                        "w": w,
                        "h": h,
                        "area": w * h,
                        "name": (name or role)[:80],
                        "role": role or "widget",
                        "source": "atspi",
                    }
                )
        if depth < 12 and len(out) < cap:
            for child in _atspi_children(acc):
                queue.append((child, depth + 1))
    out.sort(key=lambda c: -int(c["area"]))
    return out[:cap]


def _load_atspi_desktop() -> Any | None:
    """Return the AT-SPI desktop root, or None when GI/pyatspi is missing."""
    with contextlib.suppress(Exception):
        import gi  # type: ignore

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore

        return Atspi.get_desktop(0)
    with contextlib.suppress(Exception):
        import pyatspi  # type: ignore

        return pyatspi.Registry.getDesktop(0)
    return None


def _atspi_clickable_candidates(*, max_marks: int = 40) -> list[dict[str, Any]]:
    """Live AT-SPI walk. Monkeypatch in tests — no live desktop required."""
    root = _load_atspi_desktop()
    if root is None:
        return []
    with contextlib.suppress(Exception):
        return _walk_atspi_tree(root, max_marks=max_marks)
    return []


def _write_png_bgr(path: Path, width: int, height: int, raw: bytes, stride: int) -> None:
    """Minimal PNG writer (RGB from BGR rows). Same shape as Windows."""
    import binascii
    import zlib

    row_bytes = width * 3
    scanlines = bytearray()
    for y in range(height):
        base = y * stride
        row = raw[base : base + row_bytes]
        if len(row) < row_bytes:
            break
        rgb = bytearray(row)
        rgb[0::3] = row[2::3]
        rgb[2::3] = row[0::3]
        scanlines += b"\x00"
        scanlines += rgb
    compressed = zlib.compress(bytes(scanlines), 1)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


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


def _ocr_words_from_bgr(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
) -> list[dict[str, Any]]:
    """Tesseract word boxes from a BGR frame. Never raises. No-op if missing."""
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
    """OCR words to candidate centers (image pixels). Monkeypatch in tests."""
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
    """Windows-equivalent pixel-edge Set-of-Mark fallback (games / canvas)."""
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
    """Keep primary first; add extra whose center is not already taken."""
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
    """Clickable Linux targets: AT-SPI, then OCR word boxes, then pixels.

    Same return shape as Windows (``x``/``y`` centers, ``w``/``h``/``area``)
    so Set-of-Mark and click-by-xy share one executor path. AT-SPI still
    runs when the capture is a tiny blank (headless grim miss) so GTK/Qt
    apps keep named boxes. OCR and the Windows-equivalent edge detector
    need a real frame.
    """
    cap = max(1, int(max_marks or 20))
    atspi = _atspi_clickable_candidates(max_marks=cap)
    ocr: list[dict[str, Any]] = []
    if width >= 32 and height >= 32 and raw:
        ocr = _ocr_word_candidates(raw, stride, width, height, max_marks=cap)
    merged = _merge_candidates(atspi, ocr, cap)
    if merged:
        return merged
    return _pixel_ui_candidates(raw, stride, width, height, max_marks=cap)


def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    backend = _pointer_backend()
    if not backend:
        raise _missing_hands("drag")
    kind, tool = backend
    if kind == "xdotool":
        _run([tool, "mousemove", "--sync", str(int(x1)), str(int(y1))])
        _run([tool, "mousedown", "1"])
        _run([tool, "mousemove", "--sync", str(int(x2)), str(int(y2))])
        _run([tool, "mouseup", "1"])
        return
    _run([tool, "mousemove", str(int(x1)), str(int(y1))])
    _run([tool, "click", "0x40"])
    _run([tool, "mousemove", str(int(x2)), str(int(y2))])
    _run([tool, "click", "0x80"])


def foreground_window_info() -> dict[str, Any]:
    """Shape matches Windows: always ``hwnd`` + ``title`` keys when possible."""
    xd = _which("xdotool")
    if not xd:
        return {"hwnd": 0, "title": ""}
    wid = _run([xd, "getactivewindow"])
    hwnd = 0
    if wid.returncode == 0:
        with contextlib.suppress(ValueError):
            hwnd = int(wid.stdout.decode("utf-8", "replace").strip() or "0")
    proc = _run([xd, "getactivewindow", "getwindowname"])
    title = ""
    if proc.returncode == 0:
        title = proc.stdout.decode("utf-8", "replace").strip()[:200]
    return {"hwnd": hwnd, "title": title}


def focus_window(hwnd: int) -> bool:
    """Activate hwnd via xdotool windowactivate / wmctrl -i -a."""
    _require_linux()
    if not hwnd:
        return False
    wid = str(int(hwnd))
    xd = _which("xdotool")
    wm = _which("wmctrl")
    if xd:
        _run([xd, "windowactivate", "--sync", wid])
    elif wm:
        _run([wm, "-i", "-a", wid])
    else:
        return False
    info = foreground_window_info()
    got = int(info.get("hwnd") or 0)
    return got == int(hwnd)


def move_mouse(x: int, y: int) -> None:
    """Pointer only — menus and CSS :hover need the cursor on the control."""
    _require_linux()
    xd = _which("xdotool")
    if xd:
        _run([xd, "mousemove", "--sync", str(int(x)), str(int(y))])
        return
    yd = _which("ydotool")
    if yd:
        _run([yd, "mousemove", str(int(x)), str(int(y))])
        return
    raise RuntimeError("Linux hover needs xdotool (X11) or ydotool (Wayland)")


def click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> None:
    _require_linux()
    btn = {"left": "1", "middle": "2", "right": "3"}.get((button or "left").lower(), "1")
    n = max(1, int(clicks or 1))
    xd = _which("xdotool")
    if xd:
        _run([xd, "mousemove", "--sync", str(int(x)), str(int(y))])
        for _ in range(n):
            _run([xd, "click", btn])
        return
    yd = _which("ydotool")
    if yd:
        ybtn = {"1": "0xC0", "2": "0xC2", "3": "0xC1"}.get(btn, "0xC0")
        _run([yd, "mousemove", str(int(x)), str(int(y))])
        for _ in range(n):
            _run([yd, "click", ybtn])
        return
    raise RuntimeError("Linux click needs xdotool (X11) or ydotool (Wayland)")


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    x = int(el.get("x") or el.get("cx") or 0)
    y = int(el.get("y") or el.get("cy") or 0)
    click(x, y, button=button, clicks=clicks)


def type_text(
    text: str,
    *,
    per_char: bool = False,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> int:
    """Type unicode text. Mirrors Windows abort_check / chars_typed contract."""
    _require_linux()
    raw = text or ""
    n = 0
    xd = _which("xdotool")
    yd = _which("ydotool") if not xd else None
    if not xd and not yd:
        raise RuntimeError("Linux type needs xdotool or ydotool")
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
        if xd:
            if ch in ("\r", "\n"):
                _run([xd, "key", "Return"])
            elif per_char or len(raw) <= 4:
                _run([xd, "type", "--delay", "12", ch])
            else:
                # Batch the remainder once we know we are not aborting mid-string.
                rest = raw[i:]
                _run([xd, "type", "--delay", "8", rest])
                n += len(rest)
                if chars_typed is not None:
                    chars_typed[:] = [n]
                return n
        else:
            assert yd is not None
            _run([yd, "type", ch])
        n += 1
    if chars_typed is not None:
        chars_typed[:] = [n]
    return n


def type_text_fast(
    text: str,
    *,
    abort_check: Callable[[], bool] | None = None,
    chars_typed: list[int] | None = None,
) -> dict[str, Any]:
    """Type text — paste when long (clipboard), else keystrokes. Same shape as Windows."""
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


def scroll(x: int, y: int, *, dy: int = -3, dx: int = 0) -> None:
    """Scroll at (x,y). dy>0 = up, dy<0 = down (matches Windows notch sign)."""
    _require_linux()
    backend = _pointer_backend()
    if not backend:
        raise _missing_hands("scroll")
    kind, tool = backend
    if kind == "xdotool":
        _run([tool, "mousemove", "--sync", str(int(x)), str(int(y))])
        notches = abs(int(dy or 0))
        btn = "4" if int(dy or 0) > 0 else "5"
        for _ in range(max(1, notches) if dy else 0):
            _run([tool, "click", btn])
        if dx:
            hbtn = "7" if int(dx) > 0 else "6"
            for _ in range(abs(int(dx))):
                _run([tool, "click", hbtn])
        return
    _run([tool, "mousemove", str(int(x)), str(int(y))])
    notches = abs(int(dy or 0))
    ybtn = "0xC4" if int(dy or 0) > 0 else "0xC5"
    for _ in range(max(1, notches) if dy else 0):
        _run([tool, "click", ybtn])
    if dx:
        yh = "0xC7" if int(dx) > 0 else "0xC6"
        for _ in range(abs(int(dx))):
            _run([tool, "click", yh])


_YDOTOOL_KEYCODES: dict[str, int] = {
    "ctrl": 29,
    "control": 29,
    "alt": 56,
    "shift": 42,
    "meta": 125,
    "win": 125,
    "super": 125,
    "enter": 28,
    "return": 28,
    "tab": 15,
    "esc": 1,
    "escape": 1,
    "space": 57,
    "backspace": 14,
    "delete": 111,
    "up": 103,
    "down": 108,
    "left": 105,
    "right": 106,
    "home": 102,
    "end": 107,
    "pageup": 104,
    "pagedown": 109,
}


def press_key(key: str) -> None:
    """Press a key or combo like 'ctrl+s', 'enter', 'shift+f6' via xdotool/ydotool."""
    _require_linux()
    raw = (key or "").strip()
    if not raw:
        return
    backend = _pointer_backend()
    if not backend:
        raise _missing_hands("press_key")
    kind, tool = backend
    parts = [p.strip().lower() for p in raw.replace("-", "+").split("+") if p.strip()]
    mapping = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "meta": "super",
        "win": "super",
        "super": "super",
        "enter": "Return",
        "return": "Return",
        "tab": "Tab",
        "esc": "Escape",
        "escape": "Escape",
        "space": "space",
        "backspace": "BackSpace",
        "delete": "Delete",
        "up": "Up",
        "down": "Down",
        "left": "Left",
        "right": "Right",
        "home": "Home",
        "end": "End",
        "pageup": "Page_Up",
        "pagedown": "Page_Down",
    }
    if kind == "ydotool":
        codes: list[int] = []
        for p in parts:
            if p in _YDOTOOL_KEYCODES:
                codes.append(_YDOTOOL_KEYCODES[p])
            elif len(p) == 1 and p.isalpha():
                codes.append(ord(p.lower()) - ord("a") + 30)
            elif len(p) == 1 and p.isdigit():
                codes.append(11 if p == "0" else int(p) + 1)
            elif p.startswith("f") and p[1:].isdigit():
                n = int(p[1:])
                codes.append(58 + n if 1 <= n <= 10 else 87 + (n - 11))
            else:
                raise RuntimeError(f"ydotool cannot map key {p!r}")
        down = [f"{c}:1" for c in codes]
        up = [f"{c}:0" for c in reversed(codes)]
        _run([tool, "key", *down, *up])
        return
    mapped: list[str] = []
    for p in parts:
        if p in mapping:
            mapped.append(mapping[p])
        elif len(p) == 1:
            mapped.append(p)
        elif p.startswith("f") and p[1:].isdigit():
            mapped.append(p.upper())
        else:
            mapped.append(p)
    combo = "+".join(mapped)
    _run([tool, "key", combo])


def press_hold(
    x: int,
    y: int,
    *,
    hold_ms: int = 2600,
    abort_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Press-and-hold at (x,y) via xdotool/ydotool mousedown/up."""
    _require_linux()
    backend = _pointer_backend()
    if not backend:
        raise _missing_hands("press_hold")
    kind, tool = backend
    if kind == "xdotool":
        _run([tool, "mousemove", "--sync", str(int(x)), str(int(y))])
        _run([tool, "mousedown", "1"])
    else:
        _run([tool, "mousemove", str(int(x)), str(int(y))])
        _run([tool, "click", "0x40"])
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
        if kind == "xdotool":
            _run([tool, "mouseup", "1"])
        else:
            _run([tool, "click", "0x80"])
    return {"held_ms": int(min(held, total) * 1000), "x": x, "y": y}


def manage_window(
    hwnd: int,
    verb: str,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """minimize | maximize | restore | close | move | resize via xdotool/wmctrl."""
    _require_linux()
    v = (verb or "").strip().lower()
    if not hwnd:
        return {"ok": False, "message": "hwnd required"}
    xd = _which("xdotool")
    wm = _which("wmctrl")
    wid = str(int(hwnd))
    if v == "minimize":
        if xd:
            _run([xd, "windowminimize", wid])
            return {"ok": True, "message": f"minimize hwnd={hwnd}"}
        return {"ok": False, "message": "xdotool required for minimize"}
    if v == "maximize":
        if wm:
            _run([wm, "-i", "-r", wid, "-b", "add,maximized_vert,maximized_horz"])
            return {"ok": True, "message": f"maximize hwnd={hwnd}"}
        if xd:
            _run([xd, "windowsize", wid, "100%", "100%"])
            return {"ok": True, "message": f"maximize hwnd={hwnd}"}
        return {"ok": False, "message": "wmctrl/xdotool required for maximize"}
    if v == "restore":
        if wm:
            _run([wm, "-i", "-r", wid, "-b", "remove,maximized_vert,maximized_horz"])
            return {"ok": True, "message": f"restore hwnd={hwnd}"}
        if xd:
            _run([xd, "windowmap", wid])
            return {"ok": True, "message": f"restore hwnd={hwnd}"}
        return {
            "ok": False,
            "message": f"wmctrl/xdotool required for restore — {_LINUX_HANDS_HINT}",
        }
    if v == "close":
        if xd:
            _run([xd, "windowclose", wid])
            return {
                "ok": True,
                "message": (
                    f"Sent close to hwnd={hwnd} (the app may show a save prompt — "
                    "snapshot to see it)"
                ),
            }
        if wm:
            _run([wm, "-i", "-c", wid])
            return {"ok": True, "message": f"Sent close to hwnd={hwnd}"}
        return {"ok": False, "message": "xdotool/wmctrl required for close"}
    if v in ("move", "resize"):
        if not xd:
            return {"ok": False, "message": "xdotool required for move/resize"}
        if v == "move" and x is not None and y is not None:
            _run([xd, "windowmove", wid, str(int(x)), str(int(y))])
        if v == "resize" and width is not None and height is not None:
            _run([xd, "windowsize", wid, str(int(width)), str(int(height))])
        nx = int(x) if x is not None else 0
        ny = int(y) if y is not None else 0
        nw = int(width) if width is not None else 0
        nh = int(height) if height is not None else 0
        return {"ok": True, "message": f"{v} hwnd={hwnd} → ({nx},{ny}) {nw}x{nh}"}
    return {"ok": False, "message": f"Unknown window verb {verb!r}"}


def open_app(name: str, search_dirs: list[str] | None = None) -> dict[str, Any]:
    """Launch via PATH / xdg-open / gtk-launch. Does not replace Windows open_app."""
    _require_linux()
    raw = (name or "").strip()
    if not raw:
        return {"ok": False, "message": "app name required"}
    if search_dirs:
        from pathlib import Path as P

        rel = P(raw)
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


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    """Visible windows via xdotool (best-effort). Shape matches Windows list_windows."""
    xd = _which("xdotool")
    if not xd:
        return []
    proc = _run([xd, "search", "--onlyvisible", "--name", ".*"])
    if proc.returncode != 0:
        return []
    ids = [
        line.strip()
        for line in proc.stdout.decode("utf-8", "replace").splitlines()
        if line.strip().isdigit()
    ]
    out: list[dict[str, Any]] = []
    for wid in ids[: max(1, int(limit))]:
        name_p = _run([xd, "getwindowname", wid])
        geo_p = _run([xd, "getwindowgeometry", "--shell", wid])
        title = name_p.stdout.decode("utf-8", "replace").strip() if name_p.returncode == 0 else ""
        if not title:
            continue
        geo: dict[str, int] = {}
        if geo_p.returncode == 0:
            for line in geo_p.stdout.decode("utf-8", "replace").splitlines():
                if "=" in line:
                    k, _, v = line.partition("=")
                    with contextlib.suppress(ValueError):
                        geo[k.strip()] = int(v.strip())
        left = int(geo.get("X", 0))
        top = int(geo.get("Y", 0))
        width = int(geo.get("WIDTH", 0))
        height = int(geo.get("HEIGHT", 0))
        out.append(
            {
                "hwnd": int(wid),
                "title": title[:200],
                "bounds": {
                    "left": left,
                    "top": top,
                    "right": left + width,
                    "bottom": top + height,
                },
            }
        )
    return out


def list_monitors() -> list[dict[str, Any]]:
    return [{"index": 0, "primary": True}]


def get_clipboard_text() -> str:
    xsel = _which("xsel")
    if xsel:
        proc = _run([xsel, "-o", "-b"])
        if proc.returncode == 0:
            return proc.stdout.decode("utf-8", "replace")
    return ""


def set_clipboard_text(text: str) -> bool:
    xsel = _which("xsel")
    if not xsel:
        return False
    try:
        subprocess.run(  # noqa: S603
            [xsel, "-i", "-b"],
            input=(text or "").encode("utf-8"),
            check=False,
            timeout=4.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
