"""Windows path landmines (reserved device names) and path helpers."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# DOS device names (case-insensitive). A file named "nul" breaks many tools.
_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)

_RESERVED_RE = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)",
    re.IGNORECASE,
)


def is_windows_reserved_name(name: str) -> bool:
    """True if *name* (a single path segment) is a Windows reserved device name."""
    if not name:
        return False
    base = Path(str(name).replace("\\", "/")).name.strip().rstrip(". ")
    if not base:
        return False
    if _RESERVED_RE.match(base):
        return True
    stem = base.split(".")[0]
    return stem.upper() in _RESERVED_STEMS


def path_has_windows_reserved_segment(path: str | Path) -> str | None:
    """Return the reserved segment if any path component is reserved, else None."""
    try:
        parts = Path(path).parts
    except Exception:
        return None
    for part in parts:
        # Skip drive letters like 'C:\\'
        if len(part) == 2 and part[1] == ":":
            continue
        if part in ("/", "\\", ".", ".."):
            continue
        if is_windows_reserved_name(part):
            return part
    return None


def check_tool_path_safe(path: str | Path) -> str | None:
    """Return an error message if *path* is unsafe on this OS, else None.

    Currently: Windows reserved device names anywhere in the path.
    """
    if sys.platform != "win32":
        # Still reject explicit reserved basenames on other OS if someone
        # checks out a Windows-hostile tree and tools thrash on it via WSL share.
        reserved = path_has_windows_reserved_segment(path)
        if reserved:
            return (
                f"path uses Windows reserved device name {reserved!r} — "
                "skip this path; do not open, write, or shell into it"
            )
        return None
    reserved = path_has_windows_reserved_segment(path)
    if reserved:
        return (
            f"path uses Windows reserved device name {reserved!r} — "
            "skip this path; do not open, write, or shell into it"
        )
    return None
