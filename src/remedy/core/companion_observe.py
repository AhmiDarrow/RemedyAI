"""Watch-the-app loop — visual observe after UI writes.

Green tests do not prove the window looks right. After GUI-ish writes the
machine asks for a desktop/foreground capture and treats a missing observe
as unfinished work (same class as a red verify).
"""

from __future__ import annotations

import os
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


_REMEDY_EXES = frozenset(
    {
        "remedy desktop.exe",
        "remedy-desktop.exe",
        "remedy.exe",
    }
)


def is_remedy_chrome(ident: dict[str, Any] | None) -> bool:
    """True when the window is Remedy itself (not the app under design)."""
    if not ident:
        return False
    exe = str(ident.get("exe_name") or "").lower()
    title = str(ident.get("title") or "").lower()
    path = str(ident.get("exe") or "").replace("\\", "/").lower()
    if exe in _REMEDY_EXES or exe.startswith("remedy-desktop"):
        return True
    if exe == "app.exe" and "remedy" in path:
        return True
    return "remedy desktop" in title


def _hwnd_identity(hwnd: int) -> dict[str, Any]:
    out: dict[str, Any] = {"hwnd": int(hwnd or 0), "title": "", "exe": "", "exe_name": ""}
    if not hwnd:
        return out
    with suppress(Exception):
        from remedy.core.companion import get_companion_backend

        fg = get_companion_backend().foreground() or {}
        if int(fg.get("hwnd") or 0) == int(hwnd):
            return {
                "hwnd": int(hwnd),
                "title": str(fg.get("title") or ""),
                "exe": str(fg.get("exe") or ""),
                "exe_name": str(fg.get("exe_name") or ""),
            }
    if os.name != "nt":
        return out
    with suppress(Exception):
        import ctypes
        from ctypes import wintypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return out
        user32 = windll.user32
        kernel32 = windll.kernel32
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out["title"] = buf.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, int(pid.value))
        if hproc:
            try:
                size = wintypes.DWORD(512)
                pbuf = ctypes.create_unicode_buffer(512)
                if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                    out["exe"] = pbuf.value
                    out["exe_name"] = Path(pbuf.value).name
            finally:
                kernel32.CloseHandle(hproc)
    return out


def pick_observe_hwnd(runtime: Any = None) -> dict[str, Any]:
    """Foreground hwnd unless it is Remedy chrome — then another visible window."""
    del runtime
    fg: dict[str, Any] = {}
    with suppress(Exception):
        from remedy.core.companion import get_companion_backend

        fg = get_companion_backend().foreground() or {}
    if fg.get("hwnd") and not is_remedy_chrome(fg):
        return {"ok": True, "hwnd": int(fg["hwnd"]), "via": "window", "ident": fg}
    best: dict[str, Any] | None = None
    best_area = 0
    with suppress(Exception):
        from remedy.core.computer.desktop_win import list_windows

        for win in list_windows(limit=40):
            hwnd = int(win.get("hwnd") or 0)
            if not hwnd:
                continue
            ident = _hwnd_identity(hwnd)
            if not ident.get("title"):
                ident["title"] = str(win.get("title") or "")
            if is_remedy_chrome(ident):
                continue
            b = win.get("bounds") or {}
            area = max(0, int(b.get("right", 0)) - int(b.get("left", 0))) * max(
                0, int(b.get("bottom", 0)) - int(b.get("top", 0))
            )
            if area > best_area:
                best_area = area
                best = {"ok": True, "hwnd": hwnd, "via": "other-window", "ident": ident}
    if best:
        return best
    return {
        "ok": False,
        "error": "foreground is Remedy chrome — launch/focus the app then companion_observe",
        "via": "remedy-chrome",
        "ident": fg,
    }


def capture_foreground_png(runtime: Any = None) -> dict[str, Any]:
    """Screenshot the app under design — never Remedy's own chrome."""
    home = None
    with suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    pick = pick_observe_hwnd(runtime)
    if not pick.get("ok"):
        return {
            "ok": False,
            "error": pick.get("error") or "no target window",
            "via": pick.get("via") or "none",
        }
    with suppress(Exception):
        from remedy.core.computer.desktop_win import print_window_png

        shot = print_window_png(int(pick["hwnd"]))
        if isinstance(shot, dict) and shot.get("path"):
            return {
                "ok": True,
                "path": str(shot["path"]),
                "via": str(pick.get("via") or "window"),
            }
    # Executor path (browser rail / computer host) — last resort, not full desktop
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
            for tok in raw.replace("\\", "/").split():
                if tok.lower().endswith(".png") and len(tok) < 400:
                    path = tok
                    break
        if path:
            return {"ok": True, "path": path[:400], "via": "executor"}
        return {"ok": False, "error": raw[:240] or "screenshot empty", "via": "executor"}
    return {
        "ok": False,
        "error": pick.get("error") or "no capture backend (not Windows or host down)",
        "via": pick.get("via") or "none",
    }


_RASTER_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"})
_MAX_DESIGN_EVIDENCE = 2
_CANNOT_SEE = (
    "You cannot see the UI yet — no decode and no native image queued. "
    "Do not draw ASCII or box layouts."
)
_DECODER_IDLE = "decoder idle — native chat vision will receive the PNG"


def _markdown_shot(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    with suppress(Exception):
        from remedy.interfaces.attachments import markdown_image_embed

        return markdown_image_embed(Path(p).name, p) or ""
    return ""


def _chat_can_see(runtime: Any) -> bool:
    with suppress(Exception):
        from remedy.core.computer.vision_observe import chat_supports_native_vision

        return bool(chat_supports_native_vision(runtime))
    return False


def _design_observe_message(res: dict[str, Any]) -> str:
    path = str(res.get("path") or "")
    bits = [f"Visual observe. Shot: `{path}`." if path else "Visual observe."]
    if res.get("decode_ok") and res.get("decode_text"):
        bits.append(str(res["decode_text"]))
    elif res.get("queued") and res.get("native"):
        bits.append(_DECODER_IDLE)
    else:
        bits.append(_CANNOT_SEE)
    bits.append("file_edit only listed defects. Never invent a layout.")
    return "\n".join(bits)


def design_observe_path(
    runtime: Any,
    path: str,
    *,
    hint: str = "",
    via: str = "",
) -> dict[str, Any]:
    """Decode + queue a design shot. Never raises. Never says file_read."""
    p = str(path or "").strip()
    out: dict[str, Any] = {
        "ok": False,
        "path": p,
        "via": via,
        "error": "",
        "queued": False,
        "native": False,
        "decode_ok": False,
        "decode_text": "",
        "decode_error": "",
        "image_md": "",
        "message": "",
    }
    if not p or not Path(p).is_file():
        out["error"] = "screenshot missing"
        out["message"] = _CANNOT_SEE
        return out
    decoded: dict[str, Any] = {"ok": False, "text": "", "error": ""}
    with suppress(Exception):
        from remedy.core.computer.vision_observe import observe_screenshot

        decoded = (
            observe_screenshot(p, runtime=runtime, kind="design", hint=hint)
            or decoded
        )
    if runtime is not None:
        shots = getattr(runtime, "_pending_cua_shots", None) or []
        out["queued"] = any(str(s.get("path") or "") == p for s in shots)
    out["native"] = _chat_can_see(runtime)
    out["ok"] = True
    out["decode_ok"] = bool(decoded.get("ok") and decoded.get("text"))
    out["decode_text"] = str(decoded.get("text") or "")
    out["decode_error"] = str(decoded.get("error") or "")
    out["image_md"] = _markdown_shot(p)
    out["message"] = _design_observe_message(out)
    return out


def design_observe_paths(
    runtime: Any,
    paths: list[Any] | None,
    *,
    hint: str = "",
) -> list[dict[str, Any]]:
    """Design-observe existing raster evidence (no extra capture)."""
    out: list[dict[str, Any]] = []
    for raw in paths or []:
        p = str(raw or "").strip()
        if not p:
            continue
        if Path(p).suffix.lower() not in _RASTER_SUFFIXES:
            continue
        if not Path(p).is_file():
            continue
        out.append(design_observe_path(runtime, p, hint=hint, via="evidence"))
        if len(out) >= _MAX_DESIGN_EVIDENCE:
            break
    return out


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
    if not cap.get("ok"):
        return {
            "ok": False,
            "path": cap.get("path") or "",
            "via": cap.get("via") or "",
            "error": cap.get("error") or "",
            "queued": False,
            "native": False,
            "decode_ok": False,
            "decode_text": "",
            "image_md": "",
            "message": (
                f"Could not capture ({cap.get('error')}). "
                "computer_screenshot target=desktop then look. "
                f"{_CANNOT_SEE}"
            ),
        }
    return design_observe_path(
        runtime,
        str(cap.get("path") or ""),
        hint=goal,
        via=str(cap.get("via") or ""),
    )


def format_observe_message(result: dict[str, Any] | None) -> dict[str, str] | None:
    if not result:
        return None
    body = str(result.get("message") or "")
    extras: list[str] = []
    decode_text = str(result.get("decode_text") or "").strip()
    if decode_text and decode_text not in body:
        extras.append(decode_text)
    elif (
        result.get("ok")
        and result.get("queued")
        and result.get("native")
        and not result.get("decode_ok")
        and _DECODER_IDLE not in body
    ):
        extras.append(_DECODER_IDLE)
    native_will_see = bool(result.get("queued") and result.get("native"))
    if (
        not result.get("decode_ok")
        and not native_will_see
        and "cannot see" not in body.lower()
    ):
        extras.append(_CANNOT_SEE)
    image_md = str(result.get("image_md") or "").strip()
    if not image_md and result.get("ok"):
        image_md = _markdown_shot(str(result.get("path") or ""))
    if image_md:
        extras.append(image_md)
    content = "\n".join(
        p
        for p in (
            "[Build engine · VISUAL OBSERVE]",
            body,
            *extras,
            "Green tests do not prove the window. Look, then fix or confirm.",
        )
        if p
    )
    return {"role": "user", "content": content}


def append_observe_messages(
    runtime: Any,
    result: dict[str, Any] | None,
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Text observe + native image_url on the same turn. Never raises."""
    if not result:
        return None
    vmsg = format_observe_message(result)
    if vmsg is not None:
        messages.append(vmsg)
    flush_msg = None
    if result.get("ok"):
        with suppress(Exception):
            from remedy.core.computer.vision_observe import flush_native_screenshots

            flush_msg = flush_native_screenshots(runtime)
            if flush_msg is not None:
                messages.append(flush_msg)
    return flush_msg
