"""Content-addressed hop memo — identical units are not regenerated.

Keyed by SHA-256 of (path, symbol, behavior, tests, closure, error vector).
Stored under ``{project}/.remedy-build/hop-memo/``. A hit is only reused after
the structural oracle still accepts the cached body.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic


def memo_key(
    *,
    path: str = "",
    symbol: str = "",
    behavior: str = "",
    tests: str = "",
    closure: str = "",
    errors: list[str] | None = None,
) -> str:
    blob = "\n".join(
        [
            (path or "").replace("\\", "/"),
            symbol or "",
            (behavior or "")[:800],
            (tests or "")[:2000],
            (closure or "")[:4000],
            "\n".join((errors or [])[:12]),
        ]
    )
    return hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()


def _dir(root: Path) -> Path:
    return root / ".remedy-build" / "hop-memo"


def _file(root: Path, key: str) -> Path:
    return _dir(root) / key[:2] / f"{key}.json"


def lookup_hop(root: Path | str, key: str) -> str | None:
    """Return cached source if present and marked ok."""
    if not key:
        return None
    fp = _file(Path(root), key)
    if not fp.is_file():
        return None
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not raw.get("ok"):
        return None
    src = raw.get("source")
    return str(src) if isinstance(src, str) and src.strip() else None


def store_hop(
    root: Path | str,
    key: str,
    source: str,
    *,
    ok: bool,
    path: str = "",
) -> Path | None:
    if not key or not (source or "").strip():
        return None
    fp = _file(Path(root), key)
    fp.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": bool(ok),
        "path": (path or "").replace("\\", "/"),
        "source": source,
        "sha": hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest(),
    }
    write_json_atomic(fp, payload, indent=None)
    return fp


def try_reuse(
    root: Path | str,
    key: str,
    *,
    oracle_fn: Any | None = None,
    unit: Any | None = None,
) -> str | None:
    """Lookup + optional oracle re-check (stale cache is dropped)."""
    src = lookup_hop(root, key)
    if not src:
        return None
    if oracle_fn is not None and unit is not None:
        with suppress(Exception):
            errs = oracle_fn(unit, src)
            if errs:
                return None
    return src
