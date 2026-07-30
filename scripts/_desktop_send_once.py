"""Prove one real Desktop UI chat send.

WebView2 often ignores raw SendInput unicode without focus on the child HWND.
Strategy: focus WebView child → click composer → clipboard paste → click Send.
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from remedy.core.computer import desktop_win as win

HOME = Path.home() / ".remedy"
API = "http://127.0.0.1:7400"
OUT = HOME / "logs" / "desktop_ui_10runs"
OUT.mkdir(parents=True, exist_ok=True)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def tok() -> str:
    p = HOME / "auth" / "local_api_token"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else ""


def api(method: str, path: str, body=None, timeout=30.0):
    data = None
    h = {"Accept": "application/json", "Authorization": f"Bearer {tok()}"}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{API}{path}", data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return r.status, json.loads(raw) if raw else {}


def desktop() -> dict:
    for w in win.list_windows(40):
        if str(w.get("title") or "").strip() == "Remedy Desktop":
            return w
    raise RuntimeError("no Remedy Desktop")


def enum_children(hwnd: int) -> list[dict]:
    results: list[dict] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(child, _lp):  # type: ignore
        if not user32.IsWindowVisible(child):
            return True
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child, buf, 256)
        cls = buf.value
        tbuf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(child, tbuf, 256)
        rect = wintypes.RECT()
        user32.GetWindowRect(child, ctypes.byref(rect))
        results.append(
            {
                "hwnd": int(child),
                "class": cls,
                "title": tbuf.value[:80],
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "w": int(rect.right - rect.left),
                "h": int(rect.bottom - rect.top),
            }
        )
        return True

    user32.EnumChildWindows(hwnd, cb, 0)
    return results


def force_focus(hwnd: int) -> None:
    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None)
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    user32.AttachThreadInput(cur_tid, fg_tid, True)
    user32.AttachThreadInput(cur_tid, target_tid, True)
    user32.ShowWindow(hwnd, 9)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.SetFocus(hwnd)
    user32.AttachThreadInput(cur_tid, fg_tid, False)
    user32.AttachThreadInput(cur_tid, target_tid, False)
    time.sleep(0.25)


def set_clipboard(text: str) -> None:
    """Put unicode text on Windows clipboard for Ctrl+V into WebView."""
    # tkinter path is reliable on Windows without HGLOBAL typing issues
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
        return
    except Exception:
        pass
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    if not user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise RuntimeError("GlobalAlloc failed")
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            raise RuntimeError("SetClipboardData failed")
    finally:
        user32.CloseClipboard()


def xy_abs(left: int, top: int, right: int, bottom: int, px: float, py: float) -> tuple[int, int]:
    return (
        int(left + (right - left) * px),
        int(top + (bottom - top) * py),
    )


def main() -> int:
    marker = f"UI_PROBE_{int(time.time())}"
    print("marker", marker)
    # DPI
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
    print("dpi", dpi)

    w = desktop()
    hwnd = int(w["hwnd"])
    print("main", w)
    kids = enum_children(hwnd)
    print("children", len(kids))
    for k in kids:
        print(
            f"  {k['hwnd']} {k['class'][:40]!r} {k['w']}x{k['h']} "
            f"@({k['left']},{k['top']}) title={k['title']!r}"
        )

    # Prefer Chrome render host (receives clicks/keys); fallback WRY_WEBVIEW
    web = None
    for prefer in (
        "Chrome_RenderWidgetHostHWND",
        "Chrome_WidgetWin_1",
        "WRY_WEBVIEW",
        "Chrome_WidgetWin_0",
    ):
        for k in kids:
            if k["class"] == prefer and k["w"] > 200 and k["h"] > 200:
                web = k
                break
        if web:
            break
    if web is None and kids:
        web = max(kids, key=lambda k: k["w"] * k["h"])
    print("webview target", web)

    force_focus(hwnd)
    if web:
        force_focus(int(web["hwnd"]))

    # Baseline
    _, sessions = api("GET", "/api/sessions")
    sess = (sessions.get("sessions") or [None])[0]
    sid = sess["id"] if sess else None
    n0 = 0
    if sid:
        _, msgs = api("GET", f"/api/sessions/{sid}/messages")
        ml = msgs.get("messages") if isinstance(msgs, dict) else msgs
        n0 = len(ml) if isinstance(ml, list) else 0
    print("session", sid, "msgs", n0)

    # Click composer: within main window, above status/nav bars.
    # From screenshots: composer is ~12% up from bottom, horizontally centered.
    b = w["bounds"]
    # Use webview rect if available (client content, no title bar)
    if web and web["h"] > 100:
        left, top, right, bottom = web["left"], web["top"], web["right"], web["bottom"]
    else:
        left, top, right, bottom = b["left"], b["top"], b["right"], b["bottom"]

    # Composer band: above bottom nav (~ last 40px) and status (~24px).
    # Content 800px tall → composer center ≈ 0.84–0.88 from top of webview.
    for px, py in [(0.45, 0.86), (0.50, 0.845), (0.40, 0.87), (0.55, 0.855)]:
        x, y = xy_abs(left, top, right, bottom, px, py)
        print(f"click composer {px},{py} -> {x},{y}")
        force_focus(hwnd)
        if web:
            # PostMessage click focus to render host
            force_focus(int(web["hwnd"]))
        win.click(x, y, clicks=2)
        time.sleep(0.2)

    # Clipboard paste (more reliable than SendInput unicode into WebView2)
    set_clipboard(marker)
    time.sleep(0.15)
    # Ensure focus again then paste
    force_focus(int(web["hwnd"]) if web else hwnd)
    win.click(*xy_abs(left, top, right, bottom, 0.45, 0.86))
    time.sleep(0.15)
    win.press_key("ctrl+v")
    time.sleep(0.8)
    # Also try type as backup if paste failed
    win.type_text(" ")
    win.type_text(marker)
    time.sleep(0.4)

    shot = win.print_window_png(hwnd)
    Path(OUT / "send_once_typed.png").write_bytes(Path(shot["path"]).read_bytes())
    print("typed shot saved")

    # Click Send paper-plane (right of composer, above bottom nav)
    for px, py in [(0.94, 0.86), (0.955, 0.845), (0.92, 0.87)]:
        x, y = xy_abs(left, top, right, bottom, px, py)
        print(f"click send {px},{py} -> {x},{y}")
        win.click(x, y)
        time.sleep(0.25)

    # Enter / Ctrl+Enter backup
    win.press_key("enter")
    time.sleep(0.3)
    win.press_key("ctrl+enter")
    time.sleep(1.5)

    # Wait for marker in messages
    found = False
    detail = ""
    deadline = time.time() + 50
    while time.time() < deadline:
        _, sessions = api("GET", "/api/sessions")
        for s in sessions.get("sessions") or []:
            _, msgs = api("GET", f"/api/sessions/{s['id']}/messages")
            ml = msgs.get("messages") if isinstance(msgs, dict) else msgs
            if not isinstance(ml, list):
                continue
            for m in ml:
                blob = str(m.get("content") or m.get("text") or "")
                if marker in blob:
                    found = True
                    detail = f"session={s['id'][:8]} role={m.get('role')} n={len(ml)}"
                    break
            if found:
                break
        if found:
            break
        time.sleep(1.0)

    shot2 = win.print_window_png(hwnd)
    Path(OUT / "send_once_after.png").write_bytes(Path(shot2["path"]).read_bytes())
    print("RESULT", "PASS" if found else "FAIL", detail or "marker not found")
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
