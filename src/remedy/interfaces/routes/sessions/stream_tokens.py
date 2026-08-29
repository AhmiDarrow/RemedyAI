"""Parse agent stream tokens into SSE events for the desktop client."""

from __future__ import annotations

import json
from typing import Any


def parse_tool_call_token(token: str) -> dict[str, Any]:
    """Parse ``@@tool_call:…`` into name/args for SSE ``tool_call`` events."""
    raw = token[len("@@tool_call:") :]
    tool_name = raw
    args: dict = {}
    try:
        if raw.strip().startswith("{"):
            obj = json.loads(raw)
            tool_name = str(obj.get("name") or "tool")
            a = obj.get("args")
            if isinstance(a, dict):
                args = a
        else:
            tool_name = raw.split("|", 1)[0].strip() or "tool"
    except Exception:
        tool_name = raw.split("|", 1)[0].strip() or "tool"
    return {"name": tool_name, "args": args}


def parse_tool_result_token(token: str) -> dict[str, Any]:
    """Parse ``@@tool_result:…`` payload."""
    raw = token[len("@@tool_result:") :]
    try:
        if raw.strip().startswith("{"):
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return {"name": "tool", "result": raw}


def parse_life_task_token(token: str) -> dict[str, Any]:
    """Parse ``@@life_task:…`` into the owner-card payload."""
    raw = token[len("@@life_task:") :] if token.startswith("@@life_task:") else token
    try:
        obj = json.loads(raw) if raw else {}
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def sse_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
