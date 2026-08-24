"""Open a real directory with the OS file manager.

``host_run(argv=['explorer', r'C:\\folder'])`` used to exec explorer.exe
under CREATE_NO_WINDOW, wait on it, and report HOST_TRANSLATED_FAIL because
explorer's exit code is 1 even when the window opened — or the window never
appeared. ShellExecute / ``os.startfile`` is how the owner double-clicks a
folder. The Files rail is a separate path (``app_control`` + ``/api/files``).
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any

_SKIP_TOKS = frozenset({"/c", "/k", "cmd", "cmd.exe", "start", ""})


def _norm_head(raw: str) -> str:
    # Split on both slashes — Path.name on POSIX treats ``C:\...\explorer.EXE``
    # as one filename, so Linux CI missed the explorer head.
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name


def _looks_flag(tok: str) -> bool:
    """True for Windows explorer/cmd switches, not POSIX absolute paths.

    ``/tmp/foo`` is a directory. ``/c`` / ``/e`` / ``/select,x`` are flags.
    Treating every leading-slash token as a flag made Linux CI miss
    ``explorer /tmp/…`` (session 0.31.2).
    """
    t = (tok or "").strip()
    if not t or t in ('""', "''"):
        return True
    low = t.lower()
    if low.startswith("/select"):
        return True
    return bool(re.fullmatch(r"/[A-Za-z][A-Za-z0-9]*", t))


def existing_dir(raw: str | None) -> Path | None:
    """Return a resolved existing directory, or None."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text or text in (".",):
        return None
    p = Path(text).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    try:
        if p.is_dir():
            return p
    except OSError:
        return None
    return None


def folder_from_argv(argv: list[str] | None) -> Path | None:
    """If *argv* is explorer/start aimed at a directory, return that path."""
    if not argv:
        return None
    toks = [str(a).strip() for a in argv]
    if not any(_norm_head(t) in {"explorer", "start"} for t in toks):
        return None
    paths = [
        t.strip('"').strip("'")
        for t in toks
        if t
        and not _looks_flag(t)
        and _norm_head(t) not in {"cmd", "explorer", "start"}
        and t.lower() not in _SKIP_TOKS
    ]
    if not paths:
        return None
    return existing_dir(paths[-1])


def folder_from_command(command: str) -> Path | None:
    """Same as :func:`folder_from_argv` for a shell command string."""
    cmd = (command or "").strip()
    if not cmd:
        return None
    posix = os.name != "nt"
    try:
        toks = shlex.split(cmd, posix=posix)
    except ValueError:
        toks = cmd.split()
    return folder_from_argv(toks)


def open_folder_os(path: str | Path) -> dict[str, Any]:
    """Show *path* in the OS file manager. Does not wait on the window."""
    p = Path(path).expanduser()
    try:
        p = p.resolve()
    except OSError:
        p = p.absolute()
    if not p.is_dir():
        raise ValueError(f"not a directory: {p}")
    from remedy.core.workspace import refuse_protected_secret_path

    refuse_protected_secret_path(p)
    if os.name == "nt":
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise RuntimeError("os.startfile unavailable")
        startfile(os.path.normpath(str(p)))  # noqa: S606 — folder open, not a URL
        method = "startfile"
    else:
        import shutil
        import subprocess

        opener = shutil.which("xdg-open") or shutil.which("open")
        if not opener:
            raise ValueError("no folder opener on this host (xdg-open)")
        subprocess.Popen(  # noqa: S603
            [opener, str(p)],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        method = "xdg-open"
    return {"ok": True, "method": method, "target": str(p)}


def format_open_folder_result(info: dict[str, Any]) -> str:
    target = info.get("target") or ""
    method = info.get("method") or "os"
    return (
        f"opened folder: {target}\n"
        f"method={method} (OS file manager — not a shell exit code)\n"
        "To show the same directory in Studio's Files rail: "
        "app_control action=open_panel panel=files path=<that directory>."
    )
