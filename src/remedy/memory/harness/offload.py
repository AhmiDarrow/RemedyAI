"""Offload fat tool bodies from send-view; keep retrievable handles."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def _offload_root(home: Path | str | None = None) -> Path:
    if home is None:
        root = Path.home() / ".remedy" / "tool_offload"
    else:
        root = Path(home).expanduser() / "tool_offload"
    root.mkdir(parents=True, exist_ok=True)
    return root


def offload_tool_body(
    content: str,
    *,
    session_id: str = "",
    tool_name: str = "",
    home: Path | str | None = None,
    min_chars: int = 4000,
) -> tuple[str, str | None]:
    """If content is large, write to disk and return (handle_text, path).

    Returns (original, None) when too small or write fails.
    """
    text = content or ""
    if len(text) < min_chars:
        return text, None
    try:
        root = _offload_root(home)
        sid = "".join(c for c in (session_id or "global") if c.isalnum() or c in "-_")[:40]
        h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
        path = root / f"{sid}_{h}.txt"
        if not path.is_file():
            path.write_text(text, encoding="utf-8", errors="replace")
        # First line outcome + handle
        first = text.strip().split("\n", 1)[0][:160]
        handle = (
            f"{first}\n"
            f"…[tool output offloaded {len(text)} chars → {path}]\n"
            f"Re-read with file_read path={path} if full stdout needed."
        )
        meta = {
            "path": str(path),
            "tool": tool_name,
            "session_id": session_id,
            "chars": len(text),
            "sha16": h,
            "ts": time.time(),
        }
        meta_path = path.with_suffix(".json")
        if not meta_path.is_file():
            meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return handle, str(path)
    except Exception:
        return text, None


def maybe_offload_messages(
    messages: list[dict[str, Any]],
    *,
    session_id: str = "",
    home: Path | str | None = None,
    min_chars: int = 6000,
    keep_recent_tools: int = 4,
) -> list[dict[str, Any]]:
    """Offload older fat tool messages; keep recent tools full."""
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    protect = set(tool_idxs[-max(1, keep_recent_tools) :]) if tool_idxs else set()
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        if i in protect or msg.get("role") != "tool":
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str) or len(content) < min_chars:
            out.append(msg)
            continue
        # Already a handle from a prior offload — do not re-hash / re-write.
        if "tool output offloaded" in content:
            out.append(msg)
            continue
        handle, path = offload_tool_body(
            content,
            session_id=session_id,
            tool_name=str(msg.get("name") or ""),
            home=home,
            min_chars=min_chars,
        )
        if path is None:
            out.append(msg)
            continue
        m = dict(msg)
        m["content"] = handle
        out.append(m)
    return out
