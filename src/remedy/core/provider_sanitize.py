"""Sanitize chat payloads before they leave the machine to an LLM provider.

Trust boundary: tool results may contain mail snippets, page text, or paths.
Secrets (tokens, keys) must never be forwarded. Bodies are size-capped.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Max characters for a single tool/function message content when sending upstream.
TOOL_CONTENT_MAX = 6_000
# Max for normal user/assistant text (already bounded in practice).
TEXT_CONTENT_MAX = 100_000

_SECRET_KEY_RE = re.compile(
    r"(?i)(access_token|refresh_token|id_token|client_secret|authorization|"
    r"api_key|apikey|password|passwd|bearer|private_key|bot_token|app_secret|"
    r"signing_secret|session_token|x-api-key)"
)

_SECRET_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-[a-zA-Z0-9]{20,}"
    r"|xai-[a-zA-Z0-9]{20,}"
    r"|ya29\.[a-zA-Z0-9._-]+"
    r"|1//[a-zA-Z0-9_-]+"
    r"|ghp_[a-zA-Z0-9]{20,}"
    r"|xox[baprs]-[a-zA-Z0-9-]{10,}"
    r")\b"
)


def _clip(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)] + "…"


def _scrub_text(text: str, *, max_len: int) -> str:
    t = _SECRET_VALUE_RE.sub("[redacted]", text or "")
    return _clip(t, max_len)


def _scrub_obj(obj: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "[truncated]"
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _SECRET_KEY_RE.search(ks):
                out[ks] = "[redacted]"
            else:
                out[ks] = _scrub_obj(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [_scrub_obj(x, depth=depth + 1) for x in obj[:200]]
    if isinstance(obj, str):
        return _scrub_text(obj, max_len=TOOL_CONTENT_MAX)
    return obj


def sanitize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized copy of one chat message (does not mutate input).

    Fast path: shallow copy + string scrub for common role/content messages.
    Deepcopy only when nested tool_calls or non-string content need isolation.
    """
    if not isinstance(msg, dict):
        return {"role": "user", "content": _scrub_text(str(msg), max_len=TEXT_CONTENT_MAX)}

    role = str(msg.get("role") or "")
    content = msg.get("content")
    has_tool_calls = isinstance(msg.get("tool_calls"), list) and bool(msg["tool_calls"])
    nested = has_tool_calls or not isinstance(content, (str, type(None)))

    if nested:
        m = copy.deepcopy(msg)
        content = m.get("content")
    else:
        m = dict(msg)

    if role in ("tool", "function"):
        if isinstance(content, str):
            m["content"] = _scrub_text(content, max_len=TOOL_CONTENT_MAX)
        elif content is not None:
            m["content"] = _scrub_obj(content)
    elif isinstance(content, str):
        m["content"] = _scrub_text(content, max_len=TEXT_CONTENT_MAX)
    elif isinstance(content, list):
        # Multimodal parts
        parts = []
        for p in content:
            if not isinstance(p, dict):
                parts.append(p)
                continue
            q = dict(p)
            if q.get("type") == "text" and isinstance(q.get("text"), str):
                q["text"] = _scrub_text(q["text"], max_len=TEXT_CONTENT_MAX)
            # Do not forward raw base64 blobs larger than needed — leave image_url as-is
            # (vision path usually uses decode brief as text already).
            parts.append(q)
        m["content"] = parts
    elif content is not None:
        m["content"] = _scrub_obj(content)

    # Strip any accidental secret-bearing top-level keys
    for k in list(m.keys()):
        if _SECRET_KEY_RE.search(str(k)) and k not in (
            "role",
            "content",
            "tool_calls",
            "name",
            "tool_call_id",
        ):
            m[k] = "[redacted]"

    if "tool_calls" in m and isinstance(m["tool_calls"], list):
        cleaned = []
        for tc in m["tool_calls"]:
            if not isinstance(tc, dict):
                continue
            tcc = tc if nested else copy.deepcopy(tc)
            fn = tcc.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("arguments"), str):
                args = fn["arguments"]
                if _SECRET_VALUE_RE.search(args) or len(args) > TOOL_CONTENT_MAX:
                    fn["arguments"] = _scrub_text(args, max_len=TOOL_CONTENT_MAX)
            cleaned.append(tcc)
        m["tool_calls"] = cleaned

    return m


def sanitize_messages_for_provider(
    messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Sanitize a full messages array for outbound provider HTTP."""
    return [sanitize_message(m) for m in (messages or []) if isinstance(m, dict)]


def sanitize_chat_body(body: dict[str, Any]) -> dict[str, Any]:
    """Copy body and sanitize messages (does not mutate input)."""
    out = dict(body)
    if "messages" in out:
        out["messages"] = sanitize_messages_for_provider(out.get("messages"))
    return out
