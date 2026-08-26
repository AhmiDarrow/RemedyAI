"""Linux desktop capture + input (xdotool / grim / xdg-open). Additive to Windows."""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from remedy.home import default_home


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
    full = screenshot_png()
    return {
        **full,
        "origin": {"x": 0, "y": 0},
        "requested": {"x": x, "y": y, "width": width, "height": height, "scale": sc},
        "note": "region crop unavailable; full desktop captured",
    }


def screenshot_monitor_png(index: int, path: Path | None = None) -> dict[str, Any]:
    _ = index
    return screenshot_png(path)


def print_window_png(hwnd: int | None = None, path: Path | None = None) -> dict[str, Any]:
    _ = hwnd
    return screenshot_png(path)


def find_webview_host_hwnd() -> int | None:
    return None


def detect_system_prompt() -> dict[str, Any]:
    return {}


def find_remedy_desktop_hwnd() -> int | None:
    return None


def desktop_snapshot(
    limit: int = 40,
    mode: str = "auto",
    hwnd: int | None = None,
) -> list[dict[str, Any]]:
    _ = limit, mode, hwnd
    return []


def _capture_virtual_screen() -> tuple[bytes, int, int, int, int, int]:
    raise RuntimeError("pixel-mark capture is Windows-only; use screenshot without marks")


def detect_ui_candidates(*_a: Any, **_k: Any) -> list[dict[str, Any]]:
    return []


def drag(x1: int, y1: int, x2: int, y2: int) -> None:
    xd = _which("xdotool")
    if not xd:
        click(x2, y2)
        return
    _run([xd, "mousemove", "--sync", str(int(x1)), str(int(y1))])
    _run([xd, "mousedown", "1"])
    _run([xd, "mousemove", "--sync", str(int(x2)), str(int(y2))])
    _run([xd, "mouseup", "1"])


def foreground_window_info() -> dict[str, Any]:
    xd = _which("xdotool")
    if not xd:
        return {}
    proc = _run([xd, "getactivewindow", "getwindowname"])
    if proc.returncode != 0:
        return {}
    title = proc.stdout.decode("utf-8", "replace").strip()
    return {"title": title[:200]} if title else {}


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
        _run([yd, "mousemove", str(int(x)), str(int(y))])
        for _ in range(n):
            _run([yd, "click", btn])
        return
    raise RuntimeError("Linux click needs xdotool (X11) or ydotool (Wayland)")


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    x = int(el.get("x") or el.get("cx") or 0)
    y = int(el.get("y") or el.get("cy") or 0)
    click(x, y, button=button, clicks=clicks)


def type_text(text: str, *, per_char: bool = False) -> int:
    _require_linux()
    raw = text or ""
    xd = _which("xdotool")
    if xd:
        if per_char:
            n = 0
            for ch in raw:
                _run([xd, "type", "--delay", "12", ch])
                n += 1
            return n
        _run([xd, "type", "--delay", "8", raw])
        return len(raw)
    yd = _which("ydotool")
    if yd:
        _run([yd, "type", raw])
        return len(raw)
    raise RuntimeError("Linux type needs xdotool or ydotool")


def type_text_fast(text: str) -> int:
    return type_text(text, per_char=False)


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


def open_url(url: str) -> None:
    xdg = _which("xdg-open")
    if not xdg:
        raise RuntimeError("xdg-open not found")
    subprocess.Popen(  # noqa: S603
        [xdg, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def list_windows(limit: int = 40) -> list[dict[str, Any]]:
    _ = limit
    return []


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
