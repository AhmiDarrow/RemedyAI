"""Write a JSON state file so a crash cannot destroy it.

``path.write_text(json.dumps(...))`` truncates the file and then fills it. Lose
the process in between — a kill, a crash, a power cut — and what is left on disk
is half a JSON document. Every reader in this codebase handles that the same
way::

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

which is correct, and means a torn write is **silent, permanent loss** of that
checkpoint, plan, or crystal. Nobody is told. For a product whose premise is
continuity on disk, that is the wrong failure.

Write to a sibling temp file, flush it to the platter, then ``os.replace`` —
which is atomic on POSIX and on Windows. A crash either leaves the previous good
file or the new one, never a torn one.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

__all__ = ["scratch_path", "write_json_atomic", "write_text_atomic"]


def scratch_path(target: Path | str) -> Path:
    """A scratch file beside *target* that no other process will pick.

    ``path.with_suffix(".tmp")`` gives every writer the *same* scratch name, so
    two of them — the desktop app and a CLI, two threads, two windows — write
    the same file at once and whichever renames second publishes a corrupted or
    interleaved result. The pid makes it unique; keeping it in the same
    directory keeps ``os.replace`` atomic.
    """
    t = Path(target)
    return t.with_name(f".{t.name}.{os.getpid()}.tmp")


def write_text_atomic(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
) -> Path:
    """Replace *path* with *text*, atomically. Returns the path written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Same directory: os.replace is only atomic within one filesystem, and the
    # system temp dir is often a different one.
    tmp = scratch_path(p)
    try:
        with open(tmp, "w", encoding=encoding, newline="") as fh:
            fh.write(text)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        os.replace(tmp, p)
    except BaseException:
        # Never leave the scratch file behind, including on KeyboardInterrupt.
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p


def write_json_atomic(
    path: Path | str,
    data: Any,
    *,
    indent: int | None = 2,
    default: Any = str,
    fsync: bool = True,
    **dumps_kwargs: Any,
) -> Path:
    """Serialise *data* and replace *path* with it, atomically.

    Serialising **before** touching the file matters as much as the rename: an
    object that cannot be encoded now raises with the old file still intact,
    instead of emptying it and then failing.
    """
    text = json.dumps(data, indent=indent, default=default, **dumps_kwargs)
    return write_text_atomic(path, text, fsync=fsync)
