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

Thread- and process-safe: the scratch name carries both the pid and the thread
id, so concurrent writers to one path never share a scratch file; whichever
``os.replace`` lands last wins, and the file is always one whole document.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

__all__ = [
    "replace_with_retry",
    "scratch_path",
    "write_bytes_atomic",
    "write_json_atomic",
    "write_text_atomic",
]

# Windows refuses os.replace while another handle has the target open without
# FILE_SHARE_DELETE — which is what a plain reader holds. Readers are brief, so
# a short bounded retry rides it out; the sleeps total well under a second.
_REPLACE_ATTEMPTS = 10
_REPLACE_SLEEP_S = 0.02


def scratch_path(target: Path | str) -> Path:
    """A scratch file beside *target* that no other process will pick.

    ``path.with_suffix(".tmp")`` gives every writer the *same* scratch name, so
    two of them — the desktop app and a CLI, two threads, two windows — write
    the same file at once and whichever renames second publishes a corrupted or
    interleaved result. The pid *and* thread id make it unique (the pid alone
    let two threads of one process collide); keeping it in the same directory
    keeps ``os.replace`` atomic.
    """
    t = Path(target)
    return t.with_name(f".{t.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def replace_with_retry(src: Path | str, dst: Path | str) -> None:
    """``os.replace`` that rides out a transient Windows sharing violation.

    A reader holding *dst* open makes ``os.replace`` raise ``PermissionError``
    on Windows. Retry a bounded number of times with short sleeps (about 1s in
    total, growing), then re-raise the last error.
    """
    delay = _REPLACE_SLEEP_S
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 0.15)


def write_bytes_atomic(
    path: Path | str,
    data: bytes,
    *,
    fsync: bool = True,
    mode: int | None = None,
) -> Path:
    """Replace *path* with *data*, atomically. Returns the path written.

    *mode*, when given, is applied to the scratch file before the rename so the
    published file never exists with looser permissions (best effort — Windows
    ignores most of it).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = scratch_path(p)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            if fsync:
                fh.flush()
                os.fsync(fh.fileno())
        if mode is not None:
            with contextlib.suppress(OSError):
                os.chmod(tmp, mode)
        replace_with_retry(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return p


def write_text_atomic(
    path: Path | str,
    text: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
    fsync: bool = True,
    mode: int | None = None,
) -> Path:
    """Replace *path* with *text*, atomically. Returns the path written.

    Encoding happens before anything touches the disk, so a lone surrogate
    under ``errors="strict"`` raises with the old file intact. Same directory
    for the scratch file: ``os.replace`` is only atomic within one filesystem,
    and the system temp dir is often a different one.
    """
    data = text.encode(encoding, errors=errors)
    return write_bytes_atomic(path, data, fsync=fsync, mode=mode)


def write_json_atomic(
    path: Path | str,
    data: Any,
    *,
    indent: int | None = 2,
    fsync: bool = True,
    mode: int | None = None,
    **dumps_kwargs: Any,
) -> Path:
    """Serialise *data* and replace *path* with it, atomically.

    Serialising **before** touching the file matters as much as the rename: an
    object that cannot be encoded now raises with the old file still intact,
    instead of emptying it and then failing. No ``default=`` is supplied — a
    value that cannot be serialised is a bug to surface, not something to
    quietly ``str()`` into the state file. Callers that want coercion pass
    ``default=`` themselves.
    """
    text = json.dumps(data, indent=indent, **dumps_kwargs)
    return write_text_atomic(path, text, fsync=fsync, mode=mode)
