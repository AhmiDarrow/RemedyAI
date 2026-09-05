"""POSIX screenshot helpers over ``host_binding`` (PNG encode in Zig)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.core.computer import desktop_policy as P
from remedy.core.computer import host_binding as H
from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

_LINUX_HANDS_HINT = (
    "needs an X11 display with XTest (DISPLAY set; XWayland works). "
    "Pure Wayland without XWayland is not supported yet"
)


def host_fail(need: str, exc: BaseException) -> RuntimeError:
    return RuntimeError(f"Linux {need} failed via remedy_core — {_LINUX_HANDS_HINT}: {exc}")


def capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    try:
        shot = H.capture_virtual_screen(3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise host_fail("capture", exc) from exc
    return shot.pixels, shot.stride, shot.width, shot.height, shot.left, shot.top


def screenshot_png(path: Path | None = None, *, marks: list[Any] | None = None) -> dict[str, Any]:
    raw, stride, width, height, left, top = capture_virtual_screen()
    if width < 2 or height < 2:
        raise RuntimeError(f"Linux screenshot failed — {_LINUX_HANDS_HINT}")
    try:
        return P.finalize_shot(
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
            extra={"method": "remedy_core"},
        )
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise host_fail("screenshot", exc) from exc


def screenshot_region_png(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    path: Path | None = None,
    scale: float = 1.0,
) -> dict[str, Any]:
    ox, oy, fw, fh = H.virtual_screen_rect()
    bx, by, rw, rh, sc = P.clip_region_to_virtual(
        x, y, width, height, scale=scale, origin_x=ox, origin_y=oy, full_w=fw, full_h=fh
    )
    crop = H.capture_region(ox + bx, oy + by, rw, rh, 3)
    return P.finalize_shot(
        crop.pixels,
        crop.stride,
        rw,
        rh,
        path=path,
        prefix="region",
        origin_x=ox + bx,
        origin_y=oy + by,
        purge=False,
        extra={
            "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
            "method": "remedy_core",
        },
    )


def print_window_png(hwnd: int | None = None, path: Path | None = None) -> dict[str, Any]:
    if not hwnd:
        return screenshot_png(path)
    try:
        shot = H.print_window(int(hwnd), 3)
    except (NativeRuntimeUnavailableError, H.HostError) as exc:
        raise host_fail("window capture", exc) from exc
    return P.finalize_shot(
        shot.pixels,
        shot.stride,
        shot.width,
        shot.height,
        path=path,
        prefix="hwnd",
        origin_x=shot.left,
        origin_y=shot.top,
        purge=False,
        extra={"method": "remedy_core"},
    )
