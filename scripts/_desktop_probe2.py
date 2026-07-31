"""Quick UIA dump of Remedy Desktop for automation tuning."""
from __future__ import annotations

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import ctypes

from remedy.core.computer import desktop_win as win


def main() -> None:
    hwnd = None
    for w in win.list_windows(80):
        if str(w.get("title") or "").strip() == "Remedy Desktop":
            hwnd = int(w["hwnd"])
            print("hwnd", hwnd, "bounds", w.get("bounds"))
            break
    if not hwnd:
        print("NO WINDOW")
        return
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    els = win.desktop_snapshot(limit=100, mode="auto", hwnd=hwnd)
    print("elements", len(els))
    for e in els:
        print(
            f"{e.get('ref'):4} {(e.get('role') or e.get('tag') or ''):16} "
            f"{(e.get('name') or '')[:70]!r} @({e.get('x')},{e.get('y')})"
        )
    info = win.print_window_png(hwnd)
    print("shot", info)


if __name__ == "__main__":
    main()
