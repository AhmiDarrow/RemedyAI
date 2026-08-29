"""Append-only Connect events on the PC. No payloads, no secrets."""

from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from remedy.home import default_home

logger = logging.getLogger(__name__)

_ALLOWED_EVENTS = frozenset({"pair", "revoke", "pause", "approve-from-phone"})
_SECRET_KEYS = frozenset(
    {
        "secret",
        "ps",
        "payload",
        "token",
        "api_key",
        "bearer",
        "local_api_token",
        "authorization",
        "password",
        "hp",
        "pair_secret",
    }
)
_SECRET_FRAGMENTS = (
    "bearer ",
    "local_api_token",
    "api_key=",
    "ps=",
    "authorization:",
)


def _connect_dir(home: Path | None = None) -> Path:
    base = Path(home) if home is not None else default_home()
    d = base / "auth" / "connect"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audit_path(home: Path | None = None) -> Path:
    return _connect_dir(home) / "audit.log"


def _safe_value(key: str, value: object) -> str | None:
    k = str(key or "").strip().lower()
    if k in _SECRET_KEYS or k.endswith("_secret") or k.endswith("_token"):
        return None
    text = str(value if value is not None else "")
    if len(text) > 120:
        text = text[:120]
    low = text.lower()
    if any(frag in low for frag in _SECRET_FRAGMENTS):
        return None
    if "\n" in text or "\r" in text:
        text = text.replace("\n", " ").replace("\r", " ")
    return text


def append_event(kind: str, home: Path | None = None, **fields: Any) -> None:
    """Write one audit line. Unknown kinds are ignored (fail closed)."""
    event = str(kind or "").strip().lower()
    if event not in _ALLOWED_EVENTS:
        return
    parts = [f"ts={int(time.time())}", f"event={event}"]
    for key, value in fields.items():
        safe = _safe_value(str(key), value)
        if safe is None:
            continue
        parts.append(f"{key}={safe}")
    line = " ".join(parts)
    low = line.lower()
    if any(frag in low for frag in _SECRET_FRAGMENTS):
        logger.warning("connect audit dropped a line that looked like a secret")
        return
    path = audit_path(home)
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        logger.warning("connect audit write failed: %s", exc)
        return
    with contextlib.suppress(OSError):
        path.chmod(0o600)
