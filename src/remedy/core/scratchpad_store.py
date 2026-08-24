"""Session scratch pad — server-side so the agent can read what the owner sees.

The Studio Scratch rail used to live only in the browser's localStorage.
Remedy could open the panel and still not read the notes. One file per
session under ``~/.remedy/scratch/`` is the source of truth for Desktop
and WebUI.
"""

from __future__ import annotations

import re
from pathlib import Path

from remedy.home import default_home

_MAX_CHARS = 256_000
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def scratch_dir(*, home: Path | None = None) -> Path:
    d = (home or default_home()) / "scratch"
    d.mkdir(parents=True, exist_ok=True)
    return d


def scratch_id(session_id: str | None) -> str:
    raw = (session_id or "").strip() or "_global"
    cleaned = _SAFE_ID.sub("_", raw).strip(".-") or "_global"
    return cleaned[:80]


def scratch_path(session_id: str | None, *, home: Path | None = None) -> Path:
    return scratch_dir(home=home) / f"{scratch_id(session_id)}.md"


def read_scratch(session_id: str | None, *, home: Path | None = None) -> str:
    p = scratch_path(session_id, home=home)
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except OSError:
        return ""


def write_scratch(
    session_id: str | None,
    text: str,
    *,
    home: Path | None = None,
    append: bool = False,
) -> str:
    body = text if isinstance(text, str) else str(text or "")
    if append:
        body = read_scratch(session_id, home=home) + body
    if len(body) > _MAX_CHARS:
        body = body[:_MAX_CHARS]
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(scratch_path(session_id, home=home), body)
    return body
