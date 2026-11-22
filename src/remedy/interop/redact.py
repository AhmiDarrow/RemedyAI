"""Strip secrets from structures before they are logged or returned."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "inbound_token",
    "bearer",
}

_BEARER_RE = re.compile(r"(Bearer)\s+\S+", re.IGNORECASE)
_KEY_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|REMEDY_INTEROP_TOKEN=\S+)",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    if not text:
        return text
    out = _BEARER_RE.sub(r"\1 ***", text)
    return _KEY_RE.sub("***", out)


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS or str(key).lower().endswith("_token"):
                out[key] = "***" if item else item
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
