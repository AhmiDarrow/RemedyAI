"""Watch-the-app loop — visual observe after UI writes.

Green tests do not prove the window looks right. After GUI-ish writes the
machine asks for a desktop/foreground capture and treats a missing observe
as unfinished work (same class as a red verify).
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

UI_SUFFIXES = frozenset(
    {
        ".tsx",
        ".jsx",
        ".css",
        ".scss",
        ".sass",
        ".html",
        ".vue",
        ".svelte",
        ".qml",
    }
)
_GUI_GOAL = (
    "ui",
    "ux",
    "css",
    "layout",
    "pygame",
    "game",
    "window",
    "desktop",
    "visual",
    "design",
    "landing",
)


def write_set_looks_visual(write_set: list[str] | None, goal: str = "") -> bool:
    for p in write_set or []:
        low = str(p).lower()
        if any(low.endswith(s) for s in UI_SUFFIXES):
            return True
    g = (goal or "").lower()
    return any(k in g for k in _GUI_GOAL)


def capture_foreground_png(runtime: Any = None) -> dict[str, Any]:
    """Best-effort desktop/foreground screenshot. Never raises."""
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    with suppress(Exception):
        from remedy.core.computer.desktop_win import (
            print_window_png,
            screenshot_png,
        )

        fg_hwnd = 0
        with suppress(Exception):
            from remedy.core.companion import get_companion_backend

            fg = get_companion_backend().foreground() or {}
            fg_hwnd = int(fg.get("hwnd") or 0)
        if fg_hwnd:
            with suppress(Exception):
                shot = print_window_png(fg_hwnd)
                if isinstance(shot, dict) and shot.get("path"):
                    return {"ok": True, "path": str(shot["path"]), "via": "window"}
        shot = screenshot_png()
        if isinstance(shot, dict) and shot.get("path"):
            return {"ok": True, "path": str(shot["path"]), "via": "desktop"}
        if isinstance(shot, Path):
            return {"ok": True, "path": str(shot), "via": "desktop"}
    # Executor path (browser rail / computer host)
    with suppress(Exception):
        from remedy.core.computer.executor import get_computer_executor
        from remedy.core.computer.types import ComputerAction

        ex = get_computer_executor(home)
        text = ex.run(ComputerAction.SCREENSHOT, target="desktop", runtime=runtime)
        raw = str(text or "")
        path = ""
        with suppress(Exception):
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                path = str(parsed.get("path") or "")
        if not path and ".png" in raw:
            # Only accept a standalone path-looking token, never the raw JSON.
            for tok in raw.replace("\\", "/").split():
                if tok.lower().endswith(".png") and len(tok) < 400:
                    path = tok
                    break
        if path:
            return {"ok": True, "path": path[:400], "via": "executor"}
        return {"ok": False, "error": raw[:240] or "screenshot empty", "via": "executor"}
    return {"ok": False, "error": "no capture backend (not Windows or host down)", "via": "none"}


def maybe_visual_observe(
    runtime: Any,
    state: Any,
) -> dict[str, Any] | None:
    """Once per turn after visual writes: capture + require a look."""
    if state is None or not getattr(state, "active", False):
        return None
    if getattr(state, "visual_observe_ran", False):
        return None
    ws = list(getattr(state, "write_set", None) or [])
    goal = str(getattr(state, "goal", "") or "")
    if not write_set_looks_visual(ws, goal):
        return None
    state.visual_observe_ran = True
    cap = capture_foreground_png(runtime)
    return {
        "ok": bool(cap.get("ok")),
        "path": cap.get("path") or "",
        "via": cap.get("via") or "",
        "error": cap.get("error") or "",
        "message": (
            "Visual observe after UI write. "
            + (
                f"Shot: `{cap.get('path')}`. file_read it or use vision; "
                "file_edit only what you see is wrong."
                if cap.get("ok")
                else f"Could not capture ({cap.get('error')}). "
                "computer_screenshot target=desktop then look."
            )
        ),
    }


def format_observe_message(result: dict[str, Any] | None) -> dict[str, str] | None:
    if not result:
        return None
    return {
        "role": "user",
        "content": (
            "[Build engine · VISUAL OBSERVE]\n"
            f"{result.get('message')}\n"
            "Green tests do not prove the window. Look, then fix or confirm."
        ),
    }
