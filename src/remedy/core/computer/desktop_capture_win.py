"""Windows screenshot helpers over ``host_binding`` (PNG encode in Zig)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_common as C
from remedy.core.computer import host_binding as H


def screenshot_png(
    path: Path | None = None, *, marks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from remedy.core.computer import desktop_win as W

    raw, stride, width, height, left, top = W._capture_virtual_screen()
    out = Path(path) if path is not None else C.default_shot_path("desk")
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
    C.write_png_bgr(out, width, height, raw, stride)
    with contextlib.suppress(Exception):
        C.purge_old_shots(max_age_s=900.0, home_dir=C.remedy_home())
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
    from remedy.core.computer import desktop_win as W

    W._require_windows()
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
    out = Path(path) if path is not None else C.default_shot_path("region")
    out.parent.mkdir(parents=True, exist_ok=True)
    C.write_png_bgr(out, rw, rh, crop.pixels, crop.stride)
    return {
        "path": str(out),
        "width": rw,
        "height": rh,
        "origin": {"x": origin_x + bx, "y": origin_y + by},
        "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
    }


def print_window_png(hwnd: int, path: Path | None = None) -> dict[str, Any]:
    from remedy.core.computer import desktop_win as W

    W._require_windows()
    shot = H.print_window(int(hwnd), 3)
    out = Path(path) if path is not None else C.default_shot_path("hwnd")
    out.parent.mkdir(parents=True, exist_ok=True)
    C.write_png_bgr(out, shot.width, shot.height, shot.pixels, shot.stride)
    return {
        "path": str(out),
        "width": shot.width,
        "height": shot.height,
        "origin": {"x": shot.left, "y": shot.top},
        "hwnd": int(hwnd),
        "method": "PrintWindow",
    }


def list_monitors() -> list[dict[str, Any]]:
    from remedy.core.computer import desktop_win as W
    from remedy.core.computer.desktop_win_policy import find_remedy_desktop_hwnd

    W._require_windows()
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
