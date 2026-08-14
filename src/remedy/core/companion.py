"""Personal PC companion — clipboard, foreground app, recent files.

Repo agents (Cursor, cloud IDEs, terminal builders) only see a worktree.
Remedy lives on the machine. This module is the cheap, structured snapshot
of *what the owner is looking at and holding* so design/code/get-it-done
starts from reality, not a clarifying question.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

_COMPANION_RE = re.compile(
    r"(?i)\b("
    r"clipboard|what'?s on (my |the )?(screen|clipboard|desk)|"
    r"look at (this|that|my screen|the (screen|clipboard|desktop|ui))|"
    r"i copied|i just copied|paste( this)?|"
    r"on my (screen|desktop|monitor)|"
    r"what am i looking at|what'?s (open|focused)|"
    r"design (this|that|the|my)|critique (this|that|the)|"
    r"does this look|make (it|this) (pretty|beautiful|sleek|nice)|"
    r"mock(up)?|screenshot|visual(ly)?"
    r")\b"
)

_CLIP_TEXT_CAP = 4_000
_RECENT_N = 8
_RECENT_MAX_AGE_S = 3 * 24 * 3600
_CF_UNICODETEXT = 13
_CF_HDROP = 15
_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002
_PROCESS_QUERY_LIMITED = 0x1000


class CompanionBackend(Protocol):
    def clipboard_text(self) -> str | None: ...
    def clipboard_files(self) -> list[str]: ...
    def clipboard_image_png(self) -> bytes | None: ...
    def set_clipboard_text(self, text: str) -> bool: ...
    def foreground(self) -> dict[str, Any]: ...


@dataclass
class FakeCompanionBackend:
    """Deterministic backend for tests."""

    text: str | None = None
    files: list[str] = field(default_factory=list)
    image_png: bytes | None = None
    fg: dict[str, Any] = field(default_factory=dict)
    written: str | None = None

    def clipboard_text(self) -> str | None:
        return self.text

    def clipboard_files(self) -> list[str]:
        return list(self.files)

    def clipboard_image_png(self) -> bytes | None:
        return self.image_png

    def set_clipboard_text(self, text: str) -> bool:
        self.text = text
        self.written = text
        return True

    def foreground(self) -> dict[str, Any]:
        return dict(self.fg)


class Win32CompanionBackend:
    """ctypes Win32 clipboard + foreground (no extra deps)."""

    def clipboard_text(self) -> str | None:
        if os.name != "nt":
            return None
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return None
        try:
            if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception:
            return None
        finally:
            user32.CloseClipboard()

    def clipboard_files(self) -> list[str]:
        if os.name != "nt":
            return []
        import ctypes

        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        if not user32.OpenClipboard(None):
            return []
        try:
            if not user32.IsClipboardFormatAvailable(_CF_HDROP):
                return []
            handle = user32.GetClipboardData(_CF_HDROP)
            if not handle:
                return []
            count = int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0))
            out: list[str] = []
            buf = ctypes.create_unicode_buffer(1024)
            for i in range(min(count, 20)):
                n = int(shell32.DragQueryFileW(handle, i, buf, 1024))
                if n:
                    out.append(buf.value)
            return out
        except Exception:
            return []
        finally:
            user32.CloseClipboard()

    def clipboard_image_png(self) -> bytes | None:
        """Best-effort DIB → PNG via the existing BGR writer."""
        if os.name != "nt":
            return None
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return None
        try:
            if not user32.IsClipboardFormatAvailable(_CF_DIB):
                return None
            handle = user32.GetClipboardData(_CF_DIB)
            if not handle:
                return None
            kernel32.GlobalSize.restype = ctypes.c_size_t
            size = int(kernel32.GlobalSize(handle) or 0)
            if size < 40:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                raw = ctypes.string_at(ptr, size)
            finally:
                kernel32.GlobalUnlock(handle)
        except Exception:
            return None
        finally:
            user32.CloseClipboard()
        return _dib_to_png_bytes(raw)

    def set_clipboard_text(self, text: str) -> bool:
        if os.name != "nt":
            return False
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        data = (text or "") + "\x00"
        buf = data.encode("utf-16-le")
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            hglob = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(buf))
            if not hglob:
                return False
            kernel32.GlobalLock.restype = ctypes.c_void_p
            dest = kernel32.GlobalLock(hglob)
            if not dest:
                kernel32.GlobalFree(hglob)
                return False
            ctypes.memmove(dest, buf, len(buf))
            kernel32.GlobalUnlock(hglob)
            if not user32.SetClipboardData(_CF_UNICODETEXT, hglob):
                kernel32.GlobalFree(hglob)
                return False
            return True
        except Exception:
            return False
        finally:
            user32.CloseClipboard()

    def foreground(self) -> dict[str, Any]:
        if os.name != "nt":
            return {}
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return {}
        n = int(user32.GetWindowTextLengthW(hwnd) or 0)
        title = ""
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        with suppress(Exception):
            hproc = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED, False, int(pid.value))
            if hproc:
                try:
                    size = wintypes.DWORD(512)
                    pbuf = ctypes.create_unicode_buffer(512)
                    if kernel32.QueryFullProcessImageNameW(hproc, 0, pbuf, ctypes.byref(size)):
                        exe = pbuf.value
                finally:
                    kernel32.CloseHandle(hproc)
        return {
            "hwnd": hwnd,
            "title": title[:200],
            "pid": int(pid.value),
            "exe": exe,
            "exe_name": Path(exe).name if exe else "",
        }


_BACKEND: CompanionBackend | None = None


def set_companion_backend(backend: CompanionBackend | None) -> None:
    global _BACKEND
    _BACKEND = backend


def get_companion_backend() -> CompanionBackend:
    if _BACKEND is not None:
        return _BACKEND
    return Win32CompanionBackend()


def looks_like_companion_request(message: str) -> bool:
    return bool(_COMPANION_RE.search(message or ""))


def _dib_to_png_bytes(dib: bytes) -> bytes | None:
    """BITMAPINFOHEADER DIB → PNG bytes via the desktop BGR writer."""
    if len(dib) < 40:
        return None
    import struct
    import tempfile

    width, height = struct.unpack_from("<ii", dib, 4)
    bitcount = struct.unpack_from("<H", dib, 14)[0]
    compression = struct.unpack_from("<I", dib, 16)[0]
    if compression != 0 or bitcount not in (24, 32) or width <= 0:
        return None
    abs_h = abs(height)
    header = 40
    stride_src = ((width * bitcount + 31) // 32) * 4
    pixels = dib[header:]
    row_dst = width * 3
    rows: list[bytes] = []
    for y in range(abs_h):
        src_y = y if height < 0 else (abs_h - 1 - y)
        off = src_y * stride_src
        row = pixels[off : off + stride_src]
        if len(row) < stride_src:
            return None
        if bitcount == 24:
            rows.append(row[:row_dst])
        else:
            out = bytearray(row_dst)
            for x in range(width):
                out[x * 3 : x * 3 + 3] = row[x * 4 : x * 4 + 3]
            rows.append(bytes(out))
    raw = b"".join(rows)
    try:
        from remedy.core.computer.desktop_win import _write_png_bgr

        fd, name = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        path = Path(name)
        _write_png_bgr(path, width, abs_h, raw, row_dst)
        data = path.read_bytes()
        path.unlink(missing_ok=True)
        return data
    except Exception:
        return None


def _redact(text: str) -> str:
    with suppress(Exception):
        from remedy.core.metabolism.redact import redact_text

        return redact_text(text)
    return text


def recent_files(
    *,
    extra_roots: list[Path] | None = None,
    limit: int = _RECENT_N,
) -> list[dict[str, Any]]:
    """Newest files on Desktop / Downloads / Documents (not hidden junk)."""
    home = Path.home()
    roots = [
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
    ]
    if extra_roots:
        roots.extend(extra_roots)
    found: list[tuple[float, Path]] = []
    now = time.time()
    skip = {".git", "__pycache__", "node_modules", ".remedy"}
    for root in roots:
        if not root.is_dir():
            continue
        with suppress(OSError):
            for p in root.iterdir():
                if not p.is_file() or p.name.startswith("."):
                    continue
                if p.parent.name in skip:
                    continue
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if now - m > _RECENT_MAX_AGE_S:
                    continue
                found.append((m, p))
    found.sort(key=lambda t: t[0], reverse=True)
    out: list[dict[str, Any]] = []
    for m, p in found[: max(1, min(20, limit))]:
        age = max(0, int(now - m))
        if age < 60:
            ago = f"{age}s"
        elif age < 3600:
            ago = f"{age // 60}m"
        elif age < 86400:
            ago = f"{age // 3600}h"
        else:
            ago = f"{age // 86400}d"
        out.append({"path": str(p), "name": p.name, "ago": ago, "mtime": m})
    return out


def _save_clip_image(png: bytes, home: Path | None) -> Path | None:
    base = Path(home) if home else (Path.home() / ".remedy")
    dest_dir = base / "computer" / "shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"clip_{int(time.time() * 1000)}.png"
    dest.write_bytes(png)
    return dest


def gather_companion_snapshot(
    runtime: Any = None,
    *,
    backend: CompanionBackend | None = None,
    include_recent: bool = True,
) -> dict[str, Any]:
    """Structured PC snapshot (clipboard + foreground + recent files)."""
    be = backend or get_companion_backend()
    home = None
    with suppress(Exception):
        home = Path(getattr(getattr(runtime, "config", None), "home_dir", None) or "")
        if not home:
            home = None
    text = be.clipboard_text()
    files = be.clipboard_files()
    image = be.clipboard_image_png()
    image_path = ""
    if image:
        saved = _save_clip_image(image, home)
        if saved:
            image_path = str(saved)
    fg = be.foreground() or {}
    recent = recent_files() if include_recent else []
    clip: dict[str, Any] = {"kind": "empty"}
    if text and text.strip():
        body = _redact(text)
        clip = {
            "kind": "text",
            "chars": len(body),
            "preview": body[:_CLIP_TEXT_CAP],
            "truncated": len(body) > _CLIP_TEXT_CAP,
        }
    elif image_path:
        clip = {"kind": "image", "path": image_path, "bytes": len(image or b"")}
    elif files:
        clip = {"kind": "files", "paths": files[:12]}
    return {
        "foreground": fg,
        "clipboard": clip,
        "recent": recent,
    }


def format_companion_block(snap: dict[str, Any] | None) -> str:
    if not snap:
        return ""
    lines = ["## PC companion (what the owner is holding / looking at)"]
    fg = snap.get("foreground") or {}
    if fg.get("title") or fg.get("exe_name"):
        lines.append(
            f"- Foreground: `{fg.get('exe_name') or '?'}` — {(fg.get('title') or '')[:120]}"
        )
    else:
        lines.append("- Foreground: (unknown)")
    clip = snap.get("clipboard") or {}
    kind = clip.get("kind") or "empty"
    if kind == "text":
        prev = str(clip.get("preview") or "").replace("\n", " ")[:180]
        extra = "…" if clip.get("truncated") else ""
        lines.append(f"- Clipboard: text {clip.get('chars')} chars — {prev}{extra}")
    elif kind == "image":
        lines.append(f"- Clipboard: image → `{clip.get('path')}`")
    elif kind == "files":
        paths = ", ".join(f"`{p}`" for p in (clip.get("paths") or [])[:4])
        lines.append(f"- Clipboard: files {paths}")
    else:
        lines.append("- Clipboard: empty")
    recent = snap.get("recent") or []
    if recent:
        bits = [f"{r.get('name')} ({r.get('ago')})" for r in recent[:5]]
        lines.append("- Recent Desktop/Downloads/Documents: " + "; ".join(bits))
    lines.append(
        "Use this as ground truth. clipboard_read / companion_context for details. "
        "Do not ask 'what did you copy?' when the clipboard already has it."
    )
    return "\n".join(lines)


def design_pass(
    runtime: Any = None,
    *,
    backend: CompanionBackend | None = None,
    goal: str = "",
) -> dict[str, Any]:
    """Gather visual evidence + seed a design checklist (no LLM required)."""
    snap = gather_companion_snapshot(runtime, backend=backend)
    with suppress(Exception):
        from remedy.core.build_todos import upsert_todos

        upsert_todos(
            runtime,
            [
                {"id": "see", "content": "Observe screen/clipboard/mock (companion_context)", "status": "in_progress"},
                {"id": "critique", "content": "Structured critique: goal, hierarchy, usability, top 3 fixes", "status": "pending"},
                {"id": "make", "content": "Implement the top fix in the real files / UI", "status": "pending"},
                {"id": "look", "content": "Re-observe (screenshot or run the UI) before claiming done", "status": "pending"},
            ],
            merge=False,
        )
    evidence = []
    clip = snap.get("clipboard") or {}
    if clip.get("kind") == "image":
        evidence.append(clip.get("path"))
    if clip.get("kind") == "files":
        evidence.extend(clip.get("paths") or [])
    for r in (snap.get("recent") or []):
        name = str(r.get("name") or "").lower()
        if name.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".fig")):
            evidence.append(r.get("path"))
    taste_block = ""
    with suppress(Exception):
        from remedy.core.companion_taste import format_taste_block, load_taste

        taste_block = format_taste_block(load_taste(runtime))
    return {
        "ok": True,
        "goal": (goal or "")[:200],
        "snapshot": snap,
        "evidence": evidence[:8],
        "taste": taste_block,
        "message": (
            "Design pass: evidence gathered. Honor durable taste. Critique with "
            "goal / hierarchy / usability / top 3 fixes, then implement and "
            "re-observe. Do not stop at adjectives."
        ),
    }
