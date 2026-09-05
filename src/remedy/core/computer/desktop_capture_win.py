"""Windows screenshot helpers over ``host_binding`` (PNG encode in Zig)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_policy as C
from remedy.core.computer import host_binding as H


def screenshot_png(
    path: Path | None = None, *, marks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    from remedy.core.computer import desktop_win as W

    raw, stride, width, height, left, top = W._capture_virtual_screen()
    return C.finalize_shot(
        raw,
        stride,
        width,
        height,
        path=path,
        prefix="desk",
        origin_x=left,
        origin_y=top,
        marks=marks,
        require_dict_marks=True,
    )


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
    origin_x, origin_y, full_w, full_h = H.virtual_screen_rect()
    bx, by, rw, rh, sc = C.clip_region_to_virtual(
        x,
        y,
        width,
        height,
        scale=scale,
        origin_x=origin_x,
        origin_y=origin_y,
        full_w=full_w,
        full_h=full_h,
    )
    crop = H.capture_region(origin_x + bx, origin_y + by, rw, rh, 3)
    return C.finalize_shot(
        crop.pixels,
        crop.stride,
        rw,
        rh,
        path=path,
        prefix="region",
        origin_x=origin_x + bx,
        origin_y=origin_y + by,
        purge=False,
        extra={
            "requested": {
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "scale": sc,
            }
        },
    )


def print_window_png(hwnd: int, path: Path | None = None) -> dict[str, Any]:
    from remedy.core.computer import desktop_win as W

    W._require_windows()
    shot = H.print_window(int(hwnd), 3)
    info = C.finalize_shot(
        shot.pixels,
        shot.stride,
        shot.width,
        shot.height,
        path=path,
        prefix="hwnd",
        origin_x=shot.left,
        origin_y=shot.top,
        purge=False,
        extra={"hwnd": int(hwnd), "method": "PrintWindow"},
    )
    return info


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
    return C.screenshot_monitor_from_list(
        list_monitors(),
        monitor_index,
        path=path,
        region_shot=screenshot_region_png,
        full_shot=screenshot_png,
    )
