"""Shared secret redaction for metabolism surfaces (ledger, IR, export, crystal).

Fail-closed: prefer over-redact to under-redact. Never log raw secrets.
"""

from __future__ import annotations

import re
from typing import Any

# Broad patterns for provider keys, bearer tokens, PEM, connection strings
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Authorization / Bearer headers (value to EOL)
    re.compile(r"(?i)\bauthorization\s*:\s*\S.*"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    # key=value style secrets (value may include spaces if quoted — take non-space)
    re.compile(
        r"(?i)(api[_-]?key|secret|password|passwd|pwd|token|"
        r"client_secret|refresh_token|access_token|private_key)"
        r"\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)\b(sk|xai|ghp|gho|ghu|ghs|ghr|xox[baprs]|AKIA)[-_][A-Za-z0-9+/=_\-]{8,}"),
    re.compile(
        r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----"
    ),
    re.compile(r"(?i)\b(mongodb(\+srv)?|postgres(ql)?|mysql|redis)://[^\s\"']+"),
    re.compile(r"(?i)\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
]

_SECRET_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "secret",
        "authorization",
        "refresh_token",
        "access_token",
        "client_secret",
        "private_key",
        "cookie",
        "set-cookie",
        "bot_token",
        "app_password",
    }
)


def redact_text(text: str) -> str:
    """Redact secret-shaped substrings in free text."""
    if not text:
        return ""
    out = text
    for rx in _SECRET_PATTERNS:
        out = rx.sub("[redacted]", out)
    return out


def looks_like_secret_text(text: str) -> bool:
    if not text or len(text) < 8:
        return False
    t = text.strip()
    for rx in _SECRET_PATTERNS:
        if rx.search(t):
            return True
    return False


def redact_obj(obj: Any, *, depth: int = 0) -> Any:
    """Deep-redact dict/list values; strip known secret keys."""
    if depth > 8:
        return "[redacted-depth]"
    if isinstance(obj, str):
        return redact_text(obj)[:8000]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower().replace("-", "_")
            if kl in _SECRET_KEY_NAMES or kl.endswith("_token") or kl.endswith("_password") or kl.endswith("_secret"):
                out[k] = "[redacted]"
            else:
                out[k] = redact_obj(v, depth=depth + 1)
        return out
    if isinstance(obj, list):
        return [redact_obj(x, depth=depth + 1) for x in obj[:80]]
    return obj
