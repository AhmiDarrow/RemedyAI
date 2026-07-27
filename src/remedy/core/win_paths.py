"""Windows path landmines (reserved device names) and path helpers.

Path splitting is **OS-independent**: we always split on both ``/`` and ``\\``
so Linux CI and Windows agree when checking Windows-style paths (e.g. Godot
trees checked out or referenced from a POSIX runner).
"""

from __future__ import annotations

import re
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


def _segment_base(name: str) -> str:
    """Basename of a path segment, stripping trailing dots/spaces (Win rules)."""
    if not name:
        return ""
    # Normalize separators so Path.name works on POSIX for "foo\\bar"
    cleaned = str(name).replace("\\", "/").strip().rstrip(". ")
    if not cleaned:
        return ""
    return Path(cleaned).name.strip().rstrip(". ")


def reserved_device_basename(name: str) -> str | None:
    """If *name* is a reserved device (or reserved.ext), return the device stem.

    Examples: ``nul`` → ``nul``, ``CON.txt`` → ``CON``, ``main.gd`` → None.
    Always returns the short device form (case-preserved from the match group
    when possible; otherwise the stem as given).
    """
    base = _segment_base(name)
    if not base:
        return None
    m = _RESERVED_RE.match(base)
    if m:
        # Stable canonical form (OS/path case must not change the return value)
        return m.group(1).upper()
    stem = base.split(".")[0]
    if stem.upper() in _RESERVED_STEMS:
        return stem.upper()
    return None


def is_windows_reserved_name(name: str) -> bool:
    """True if *name* (a single path segment) is a Windows reserved device name."""
    return reserved_device_basename(name) is not None


def _path_segments(path: str | Path) -> list[str]:
    """Split *path* into components using both ``/`` and ``\\`` (cross-platform)."""
    text = str(path).replace("\\", "/")
    # Drop empty segments from leading/trailing slashes
    return [p for p in text.split("/") if p and p not in (".",)]


def path_has_windows_reserved_segment(path: str | Path) -> str | None:
    """Return the reserved device name if any path component is reserved, else None.

    Return value is always the short device form (e.g. ``nul``), never a full
    path, so callers and tests behave the same on Windows and Linux.
    """
    try:
        parts = _path_segments(path)
    except Exception:
        return None
    for part in parts:
        # Skip drive letters like 'C:'
        if len(part) == 2 and part[1] == ":":
            continue
        if part in (".", ".."):
            continue
        device = reserved_device_basename(part)
        if device is not None:
            return device
    return None


def check_tool_path_safe(path: str | Path) -> str | None:
    """Return an error message if *path* uses a Windows reserved device name.

    Checked on every platform so agents thrashing a Windows tree from Linux
    (CI, WSL, cross mounts) still avoid ``nul`` / ``con`` landmines.
    """
    reserved = path_has_windows_reserved_segment(path)
    if reserved:
        return (
            f"path uses Windows reserved device name {reserved!r} — "
            "skip this path; do not open, write, or shell into it"
        )
    return None
