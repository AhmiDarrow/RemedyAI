"""Windows desktop policy helpers (UAC/dialog/snapshot/find) over the thin binding.

Uses ``host_binding`` / ``desktop_common`` / ``guidance`` — never a second
ctypes host path. Host failures stay fail-closed via ``HostError``.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from remedy.core.computer import desktop_common as C
from remedy.core.computer import host_binding as H

_SECURE_TITLES = ("user account control", "windows security")
_DIALOG_CLASS = "#32770"


def _window_class(hwnd: int) -> str:
    with contextlib.suppress(H.HostError, OSError, ValueError, TypeError):
        return H.window_class(int(hwnd))
    return ""


def find_remedy_desktop_hwnd() -> int | None:
    """HWND of the running Remedy Desktop window, if any."""
    from remedy.core.computer import desktop_win as W

    for w in W.list_windows(limit=80):
        title = str(w.get("title") or "").strip()
        if title == "Remedy Desktop" or title.startswith("Remedy Desktop"):
            return int(w["hwnd"])
    return None


def detect_system_prompt() -> dict[str, Any]:
    """Detect a UAC / secure-desktop consent prompt Remedy cannot drive."""
    from remedy.core.computer import desktop_win as W

    with contextlib.suppress(Exception):
        fg = W.foreground_window_info()
        title = str(fg.get("title") or "").lower()
        cls = _window_class(int(fg.get("hwnd") or 0)).lower()
        if any(t in title for t in _SECURE_TITLES) or cls in (
            "credential dialog xaml host",
            "#32770",
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


def find_dialog_window() -> dict[str, Any] | None:
    """The foreground common dialog (Save As / Open / message box), or None."""
    from remedy.core.computer import desktop_win as W

    with contextlib.suppress(Exception):
        fg = W.foreground_window_info()
        hwnd = int(fg.get("hwnd") or 0)
        if hwnd and _window_class(hwnd).lower() == _DIALOG_CLASS.lower():
            return {"hwnd": hwnd, "title": str(fg.get("title") or "")}
        for w in W.list_windows(limit=30):
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
    """Desktop interactive snapshot (windows and/or UIA controls)."""
    from remedy.core.computer import desktop_win as W

    mode_s = (mode or "auto").strip().lower()
    cap = max(1, min(int(limit or 40), 100))
    wins = W.list_windows(limit=min(cap, 80))
    win_els = C.windows_as_elements(wins, cap)
    if mode_s == "windows":
        return win_els[:cap]

    ctrl_els: list[dict[str, Any]] = []
    try:
        root_hwnd = hwnd
        if root_hwnd is None and wins:
            try:
                fg = int(H.foreground_window()[0] or 0)
                root_hwnd = fg or wins[0].get("hwnd")
            except Exception:
                root_hwnd = wins[0].get("hwnd")
        from remedy.core.computer.guidance import uia_control_snapshot

        raw = uia_control_snapshot(hwnd=root_hwnd, max_elements=cap)
        if raw:
            ctrl_els = raw
    except Exception:
        ctrl_els = []

    if mode_s in ("controls", "uia", "deep"):
        return (ctrl_els or win_els)[:cap]

    out = list(win_els)
    seen = {(int(e["x"]), int(e["y"])) for e in out}
    for c in ctrl_els:
        if len(out) >= cap:
            break
        key = (int(c.get("x") or 0), int(c.get("y") or 0))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out[:cap]


def find_webview_host_hwnd() -> int | None:
    """Best-effort: locate a WebView2 / Chromium host under a Remedy-titled window."""
    from remedy.core.computer import desktop_win as W

    W._require_windows()
    for w in W.list_windows(limit=200):
        title = str(w.get("title") or "").lower()
        if "remedy" not in title and "tauri" not in title:
            continue
        hwnd = int(w["hwnd"])
        for cls in (
            "Chrome_WidgetWin_1",
            "Chrome_RenderWidgetHostHWND",
            "WebView2",
            "Intermediate D3D Window",
        ):
            child = W.find_child_hwnd(hwnd, class_name=cls)
            if child:
                return child
        return hwnd
    return None


def click_element(el: dict[str, Any], *, button: str = "left", clicks: int = 1) -> None:
    """Focus window if hwnd present, then click element center."""
    from remedy.core.computer import desktop_win as W

    hwnd = el.get("hwnd")
    if hwnd:
        with contextlib.suppress(Exception):
            W.focus_window(int(hwnd))
            time.sleep(0.05)
    W.click(int(el.get("x") or 0), int(el.get("y") or 0), button=button, clicks=clicks)


def focus_window_by_title(title_substr: str) -> dict[str, Any] | None:
    """Focus first visible window whose title contains *title_substr*."""
    from remedy.core.computer import desktop_win as W

    needle = (title_substr or "").strip().lower()
    if not needle:
        return None
    for w in W.list_windows(limit=80):
        title = str(w.get("title") or "")
        if needle in title.lower():
            hwnd = int(w["hwnd"])
            W.focus_window(hwnd)
            return {"hwnd": hwnd, "title": title}
    return None
