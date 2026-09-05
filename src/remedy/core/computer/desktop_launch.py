"""App/URL launch policy for desktop computer-use.

Kept out of the thin OS host bindings so desktop_win / desktop_common stay
host_binding wrappers.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_TEXT_OPEN_EXTS = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".toml",
        ".yml",
        ".yaml",
        ".json",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".css",
        ".rs",
        ".go",
        ".c",
        ".h",
        ".log",
        ".ini",
        ".cfg",
        ".env",
    }
)


def _require_windows() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Desktop computer use requires Windows")


def _require_linux() -> None:
    if sys.platform == "win32":
        raise RuntimeError("POSIX desktop computer use only")


def is_text_document_path(raw: str | Path) -> bool:
    try:
        return Path(str(raw)).suffix.lower() in _TEXT_OPEN_EXTS
    except Exception:
        return False


def refuse_os_open_text_document(path: str | Path) -> dict[str, Any]:
    target = Path(path).expanduser()
    raise ValueError(
        f"Do not open {target.name!r} in an OS app. Use file_read on "
        f"{str(target)[:200]} — Remedy already can read it."
    )


def _open_app_is_protocol_or_url(raw: str) -> bool:
    s = (raw or "").strip()
    if not s:
        return False
    if "://" in s or s.startswith("//"):
        return True
    m = re.match(r"(?i)^([a-z][a-z0-9+.-]*):", s)
    return bool(m) and len(m.group(1)) != 1


def open_app(app: str, *, search_dirs: list[Path] | None = None) -> dict[str, Any]:
    from remedy.execution.process import retain_detached, spawn_hidden

    raw = (app or "").strip()
    if not raw:
        raise ValueError("app name required")
    if any(c in raw for c in ("\n", "\r", "\x00")):
        raise ValueError("open_app refuses control characters")
    if raw.startswith(("\\\\", "//")) or raw.startswith("\\"):
        raise ValueError("open_app refuses UNC / share paths")
    if any(c in raw for c in ("&", "|", ">", "<", "^", "%", "`", ";")):
        raise ValueError("open_app refuses shell metacharacters")
    if _open_app_is_protocol_or_url(raw):
        raise ValueError(
            f"open_app refuses URL/protocol handler (got {raw[:48]!r}); "
            "use computer_navigate / open_url for web, or an app name/path"
        )
    rel_probe = Path(raw.replace("\\", "/"))
    if search_dirs and not rel_probe.is_absolute() and ".." in rel_probe.parts:
        raise ValueError("open_app refuses parent-directory traversal")
    _require_windows()
    aliases = {
        "notepad": "notepad.exe",
        "calc": "calc.exe",
        "calculator": "calc.exe",
        "explorer": "explorer.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "pwsh": "pwsh.exe",
        "edge": "msedge.exe",
        "chrome": "chrome.exe",
        "firefox": "firefox.exe",
        "settings": "ms-settings:",
        "terminal": "wt.exe",
    }
    key = raw.lower()
    target = aliases.get(key, raw)
    if key == "settings" and target == "ms-settings:":
        os.startfile(target)
        return {"app": raw, "method": "startfile", "target": target}
    path_candidate = Path(target)
    if search_dirs and not path_candidate.is_absolute():
        rel = Path(target)
        if ".." in rel.parts:
            raise ValueError("open_app refuses parent-directory traversal")
        from remedy.core.workspace import path_in_roots, resolve_existing_path

        for d in search_dirs:
            try:
                root = resolve_existing_path(d)
            except (OSError, TypeError, ValueError):
                continue
            for cand in (root / rel, root / rel.name):
                try:
                    resolved = resolve_existing_path(cand)
                except (OSError, TypeError, ValueError):
                    continue
                if not path_in_roots(resolved, [root]):
                    continue
                if resolved.is_file():
                    if is_text_document_path(resolved):
                        return refuse_os_open_text_document(resolved)
                    retain_detached(spawn_hidden([str(resolved)]))
                    return {
                        "app": raw,
                        "method": "project_path",
                        "target": str(resolved),
                    }
    if path_candidate.is_file() and path_candidate.is_absolute():
        if is_text_document_path(path_candidate):
            return refuse_os_open_text_document(path_candidate)
        retain_detached(spawn_hidden([str(path_candidate)]))
        return {"app": raw, "method": "path", "target": str(path_candidate)}
    if path_candidate.is_dir() and (
        path_candidate.is_absolute() or (search_dirs and path_candidate.exists())
    ):
        from remedy.core.open_folder import open_folder_os

        return {"app": raw, **open_folder_os(path_candidate)}
    if is_text_document_path(raw):
        return refuse_os_open_text_document(raw)
    if len(target) > 2 and target[1] == ":" and target[0].isalpha() and (
        "/" in target or "\\" in target
    ):
        raise ValueError(f"open_app path not found: {target[:80]}")
    which = shutil.which(target) or shutil.which(raw)
    if which:
        retain_detached(spawn_hidden([which]))
        return {"app": raw, "method": "which", "target": which}
    try:
        from remedy.core.computer.appliances import best_appliance

        hit = best_appliance(raw)
        if hit is not None:
            lnk = Path(hit.path)
            if lnk.is_file() and lnk.suffix.lower() == ".lnk":
                os.startfile(str(lnk))  # noqa: S606 — trusted scan root
                return {
                    "app": raw,
                    "method": "appliance",
                    "target": hit.name,
                    "path": str(lnk),
                }
    except Exception:
        pass
    if "/" in raw or "\\" in raw or ":" in raw:
        raise ValueError(f"open_app could not resolve {raw[:80]!r} (no path / PATH entry)")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ +\-]*", raw):
        hint = ""
        with contextlib.suppress(Exception):
            from remedy.core.computer.appliances import suggestions_line

            hint = suggestions_line(raw)
        raise ValueError(
            f"open_app refuses unsafe app name for shell start: {raw[:48]!r}"
            + (f" · {hint}" if hint else "")
        )
    retain_detached(spawn_hidden(["cmd", "/c", "start", "", raw]))
    return {"app": raw, "method": "cmd start", "target": raw}


def open_url(url: str) -> dict[str, Any]:
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError(f"open_url refuses non-http(s) URL (got scheme/prefix {u[:32]!r})")
    if any(c in u for c in ("\n", "\r", "\x00")):
        raise ValueError("open_url refuses URL with control characters")
    try:
        from urllib.parse import urlparse

        parsed = urlparse(u)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError(
                "open_url refuses URL with user:password@ credentials (userinfo)"
            )
    except ValueError:
        raise
    except Exception:
        if "@" in u.split("://", 1)[-1].split("/", 1)[0]:
            raise ValueError("open_url refuses URL with userinfo credentials") from None
    if sys.platform == "win32":
        try:
            os.startfile(u)
            return {"url": u, "method": "os.startfile"}
        except OSError:
            from remedy.execution.process import retain_detached, spawn_hidden

            retain_detached(spawn_hidden(["cmd", "/c", "start", "", u]))
            return {"url": u, "method": "cmd start"}
    import webbrowser

    webbrowser.open(u)
    return {"url": u, "method": "webbrowser"}

def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def open_app_linux(name: str, search_dirs: list[str] | None = None) -> dict[str, Any]:
    _require_linux()
    raw = (name or "").strip()
    if not raw:
        return {"ok": False, "message": "app name required"}
    if search_dirs:
        rel = Path(raw)
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
    gtk, xdg = which("gtk-launch"), which("xdg-open")
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


def open_url_linux(url: str) -> dict[str, Any]:
    u = (url or "").strip()
    if not u:
        raise ValueError("empty url")
    low = u.lower()
    if not (low.startswith("http://") or low.startswith("https://")):
        raise ValueError(f"open_url refuses non-http(s) URL (got scheme/prefix {u[:32]!r})")
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
            raise ValueError("open_url refuses URL with userinfo credentials") from None
    xdg = which("xdg-open")
    if not xdg:
        raise RuntimeError("xdg-open not found")
    subprocess.Popen(  # noqa: S603
        [xdg, u],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"url": u, "method": "xdg-open"}
