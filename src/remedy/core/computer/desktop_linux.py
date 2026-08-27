"""Linux desktop capture + input (xdotool / grim / xdg-open). Additive to Windows."""

from __future__ import annotations

import contextlib
import shutil
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
    """Linux has no UAC secure-desktop analogue we can detect yet."""
    return {"blocked": False, "kind": "", "message": ""}


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
    """Best-effort frame for Set-of-Mark pixel fallback.

    Linux CI often has no grim/scrot — return a tiny blank buffer so callers
    (and tests that monkeypatch this) still get a stable shape instead of a
    hard Windows-only error.
    """
    _require_linux()
    with contextlib.suppress(Exception):
        info = screenshot_png()
        path = Path(str(info.get("path") or ""))
        if path.is_file():
            w = int(info.get("width") or 0)
            h = int(info.get("height") or 0)
            if w > 0 and h > 0:
                # Executor only needs dimensions + origin for mark math when
                # detect_ui_candidates is also stubbed/monkeypatched.
                stride = (w * 3 + 3) & ~3
                return b"\x00" * (stride * h), stride, w, h, 0, 0
    # Headless / no tool: 10x10 blank BGR buffer, origin (0,0).
    w = h = 10
    stride = (w * 3 + 3) & ~3
    return b"\x00" * (stride * h), stride, w, h, 0, 0


def detect_ui_candidates(
    raw: bytes,
    stride: int,
    width: int,
    height: int,
    *,
    max_marks: int = 20,
) -> list[dict[str, Any]]:
    """Pixel candidate detection is Windows-tuned; Linux returns none for now."""
    _ = raw, stride, width, height, max_marks
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
    xd = _which("xdotool")
    if not xd:
        return
    _run([xd, "mousemove", "--sync", str(int(x)), str(int(y))])
    # xdotool click 4 = up, 5 = down
    notches = abs(int(dy or 0))
    btn = "4" if int(dy or 0) > 0 else "5"
    for _ in range(max(1, notches) if dy else 0):
        _run([xd, "click", btn])
    if dx:
        hbtn = "7" if int(dx) > 0 else "6"
        for _ in range(abs(int(dx))):
            _run([xd, "click", hbtn])


def press_key(key: str) -> None:
    """Press a key or combo like 'ctrl+s', 'enter', 'shift+f6' via xdotool."""
    _require_linux()
    raw = (key or "").strip()
    if not raw:
        return
    xd = _which("xdotool")
    if not xd:
        raise RuntimeError("Linux press_key needs xdotool")
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
    _run([xd, "key", combo])


def press_hold(
    x: int,
    y: int,
    *,
    hold_ms: int = 2600,
    abort_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Press-and-hold at (x,y) via xdotool mousedown/up."""
    _require_linux()
    xd = _which("xdotool")
    if not xd:
        click(x, y)
        return {"held_ms": 0, "x": x, "y": y}
    _run([xd, "mousemove", "--sync", str(int(x)), str(int(y))])
    _run([xd, "mousedown", "1"])
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
        _run([xd, "mouseup", "1"])
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
        return {"ok": True, "message": f"restore hwnd={hwnd}"}
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
