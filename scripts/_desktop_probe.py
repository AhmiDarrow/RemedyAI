from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from live_desktop_ui_10runs import (  # noqa: E402
    api_ok,
    find_desktop_hwnd,
    focus_composer,
    focus_desktop,
    list_sessions,
    shot,
    snapshot,
)


def main() -> None:
    print("api", api_ok())
    hwnd = find_desktop_hwnd()
    print("hwnd", hwnd)
    if not hwnd:
        return
    hwnd = focus_desktop()
    els = snapshot(hwnd)
    print("els", len(els))
    for e in els:
        ref = e.get("ref")
        role = e.get("role") or e.get("tag") or ""
        name = (e.get("name") or "")[:70]
        print(f"  {ref:4} {role:14} {name}")
    print("sessions", len(list_sessions()))
    print("shot", shot(hwnd, "probe"))
    print("composer", focus_composer(hwnd))


if __name__ == "__main__":
    main()
