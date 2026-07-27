"""Serialize chat sessions for API responses (keeps routes thin)."""

from __future__ import annotations

from typing import Any


def session_to_public(s: Any) -> dict[str, Any]:
    """ChatSession model / row → JSON-safe dict including messenger origin fields."""
    if hasattr(s, "model_dump"):
        d = s.model_dump(mode="json")
    elif isinstance(s, dict):
        d = dict(s)
    else:
        d = {
            "id": getattr(s, "id", None),
            "title": getattr(s, "title", None),
            "model": getattr(s, "model", None),
            "agent": getattr(s, "agent", None),
            "project_path": getattr(s, "project_path", None),
            "llm_provider": getattr(s, "llm_provider", None),
            "message_count": getattr(s, "message_count", 0),
            "origin_channel": getattr(s, "origin_channel", None),
            "external_chat_id": getattr(s, "external_chat_id", None),
            "external_user": getattr(s, "external_user", None),
            "created_at": getattr(s, "created_at", None),
            "updated_at": getattr(s, "updated_at", None),
        }
    for k in ("created_at", "updated_at"):
        v = d.get(k)
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d
