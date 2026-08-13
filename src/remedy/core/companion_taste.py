"""Durable design taste — how this owner likes things to look and feel.

Not a one-off critique. Facts persist under ``~/.remedy/taste.json`` and
inject on every design pass so spacing, type, and density do not reset.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

_TASTE_HINT = re.compile(
    r"(?i)\b("
    r"prefer|always use|never use|spacing|typeface|font|density|"
    r"dark mode|light mode|radius|padding|margin|palette|contrast|"
    r"8px|4px|12px|16px|inter|geist|sf pro|jetbrains"
    r")\b"
)


def _home(runtime: Any = None) -> Path:
    with suppress(Exception):
        h = getattr(getattr(runtime, "config", None), "home_dir", None)
        if h:
            return Path(h)
    return Path.home() / ".remedy"


def _path(runtime: Any = None) -> Path:
    return _home(runtime) / "taste.json"


def load_taste(runtime: Any = None) -> list[dict[str, str]]:
    fp = _path(runtime)
    if not fp.is_file():
        return []
    try:
        raw = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw if isinstance(raw, list) else raw.get("items") or []
    out: list[dict[str, str]] = []
    for row in items:
        if isinstance(row, dict) and (row.get("fact") or "").strip():
            out.append(
                {
                    "id": str(row.get("id") or uuid4().hex[:8]),
                    "fact": str(row.get("fact") or "")[:240],
                }
            )
    return out


def save_taste(items: list[dict[str, str]], runtime: Any = None) -> Path:
    fp = _path(runtime)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return fp


def remember_taste(fact: str, runtime: Any = None) -> dict[str, str]:
    text = (fact or "").strip()
    if not text:
        return {"id": "", "fact": ""}
    items = load_taste(runtime)
    # de-dupe by lowercase fact
    low = text.lower()
    for row in items:
        if row.get("fact", "").lower() == low:
            return row
    row = {"id": uuid4().hex[:8], "fact": text[:240]}
    items.append(row)
    items = items[-40:]
    save_taste(items, runtime)
    with suppress(Exception):
        mem = getattr(runtime, "memory", None)
        if mem is not None and getattr(mem, "profile", None) is not None:
            mem.profile.add_fact(text[:240], category="design", confidence=0.9)
    return row


def extract_taste(message: str) -> list[str]:
    """Pull explicit design preferences from a user line."""
    msg = (message or "").strip()
    if not msg or not _TASTE_HINT.search(msg):
        return []
    # Keep short imperative / preference sentences
    bits: list[str] = []
    for part in re.split(r"[.\n;]+", msg):
        p = part.strip()
        if 8 <= len(p) <= 200 and _TASTE_HINT.search(p):
            bits.append(p)
    return bits[:4]


def format_taste_block(items: list[dict[str, str]] | None) -> str:
    if not items:
        return ""
    lines = ["## Design taste (durable — honor these on every visual pass)"]
    for row in items[-12:]:
        lines.append(f"- {row.get('fact')}")
    return "\n".join(lines)
