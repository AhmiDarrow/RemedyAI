"""statecache — mtime-keyed reads for small, hot state files.

The organism's organs (proprioception, myelin, vigil) keep small JSON
state that changes rarely — a few writes per session — but is consulted
on EVERY turn by the soul inject. Re-opening and re-parsing those files
each turn is cheap on NVMe and painful on NTFS with antivirus hooks
(each open can cost milliseconds).

This cache keys parsed content by ``(mtime_ns, size)``: a read becomes
one ``stat`` (~microseconds) unless the file actually changed, in which
case it is re-parsed once. Writers never need to invalidate — their
atomic ``replace`` bumps the mtime and the next stat sees it. Callers
MUST treat returned objects as read-only (helpers that mutate should
copy what they change; the organs' loaders build fresh dataclasses from
the raw dicts, so sharing the parsed JSON is safe).
"""

from __future__ import annotations

import json
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_json_cache: dict[str, tuple[int, int, Any]] = {}
_jsonl_cache: dict[str, tuple[int, int, list[dict[str, Any]]]] = {}
_MAX_ENTRIES = 64


def _stat_key(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
        return st.st_mtime_ns, st.st_size
    except OSError:
        return None


def _trim(cache: dict[str, Any]) -> None:
    while len(cache) > _MAX_ENTRIES:
        cache.pop(next(iter(cache)))


def read_json_cached(path: Path) -> Any | None:
    """Parsed JSON for *path*, re-read only when mtime/size change.

    Returns None when the file is missing or unparseable. Treat the
    returned structure as READ-ONLY — it is shared across callers.
    """
    key = str(path)
    stat = _stat_key(path)
    if stat is None:
        with _lock:
            _json_cache.pop(key, None)
        return None
    with _lock:
        hit = _json_cache.get(key)
        if hit is not None and (hit[0], hit[1]) == stat:
            return hit[2]
    data: Any | None = None
    with suppress(OSError, json.JSONDecodeError, UnicodeError):
        data = json.loads(path.read_text(encoding="utf-8"))
    with _lock:
        if data is not None:
            _json_cache[key] = (stat[0], stat[1], data)
            _trim(_json_cache)
        else:
            _json_cache.pop(key, None)
    return data


def read_jsonl_cached(path: Path) -> list[dict[str, Any]]:
    """Parsed JSONL entries for *path* (dict lines only), mtime-cached.

    Returns [] when missing. Treat entries as READ-ONLY.
    """
    key = str(path)
    stat = _stat_key(path)
    if stat is None:
        with _lock:
            _jsonl_cache.pop(key, None)
        return []
    with _lock:
        hit = _jsonl_cache.get(key)
        if hit is not None and (hit[0], hit[1]) == stat:
            return hit[2]
    entries: list[dict[str, Any]] = []
    with suppress(OSError, UnicodeError):
        for line in path.read_text(encoding="utf-8").splitlines():
            with suppress(json.JSONDecodeError):
                e = json.loads(line)
                if isinstance(e, dict):
                    entries.append(e)
    with _lock:
        _jsonl_cache[key] = (stat[0], stat[1], entries)
        _trim(_jsonl_cache)
    return entries


def clear_state_cache() -> None:
    """Test helper."""
    with _lock:
        _json_cache.clear()
        _jsonl_cache.clear()
