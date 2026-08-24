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

    # OpenAI / Anthropic / OpenRouter / xAI / GitHub / Slack / AWS-style
    re.compile(
        r"(?i)\b(sk-ant-|sk-or-|sk-proj-|sk-|xai-|ghp_|gho_|ghu_|ghs_|ghr_|"
        r"xox[baprs]-|xapp-|AKIA|ASIA)[A-Za-z0-9+/=_\-]{8,}"
    ),
    # Discord webhook token path + classic bot tokens (id.timestamp.hmac)
    re.compile(
        r"(?i)(https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/)([A-Za-z0-9_\-]+)"
    ),
    re.compile(
        r"\b([A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}\.[A-Za-z0-9_\-]{20,})\b"
    ),
    # Matrix access tokens
    re.compile(r"\bsyt_[A-Za-z0-9._\-]{16,}\b"),
    # Google API keys, HuggingFace, npm, Stripe
    re.compile(r"(?i)\bAIza[0-9A-Za-z\-_]{20,}"),
    re.compile(r"(?i)\bhf_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bnpm_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\b(sk_live|sk_test|rk_live|rk_test)_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)\bya29\.[A-Za-z0-9._\-]{10,}"),
    re.compile(r"(?i)\b1//[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
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
        "session_token",
        "id_token",
        "auth_token",
        "x_api_key",
        "x-api-key",
    }
)


# Key=value assignments: redact the *value* when it looks like a secret, never
# the identifier. Bare ``token`` in this list is ``\btoken\b`` so it does not
# eat ``bot_token: str`` (that became ``bot_[redacted]`` and the agent rewrote
# working Discord source in a loop — session 4d89).
_KV_ASSIGN_RE = re.compile(
    r"(?i)\b("
    r"api[_-]?key|apikey|client_secret|refresh_token|access_token|"
    r"private_key|session[_-]?token|id[_-]?token|auth[_-]?token|"
    r"bot_token|app_password|signing_secret|password|passwd|pwd|"
    r"secret|token"
    r")\b"
    r"(\s*[:=]\s*)"
    r"(?P<val>(['\"])([^'\"\n]*)\4|[^\s,#;\]\}\)]+)"
)
_KEEP_ASSIGNMENT_VALUES = frozenset(
    {
        "",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "none",
        "null",
        "true",
        "false",
        "undefined",
        "...",
        "…",
    }
)
_TYPE_NAME_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.\[\]]*(\s*\|\s*[A-Za-z_][A-Za-z0-9_.\[\]]*)*"
)


def _assignment_looks_like_secret(raw_val: str) -> bool:
    v = (raw_val or "").strip()
    inner = v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"" else v
    s = inner.strip()
    if not s:
        return False
    if s.lower() in _KEEP_ASSIGNMENT_VALUES or s in ("None", "True", "False"):
        return False
    if s.startswith(("os.", "environ", "${", "os.environ", "env.")):
        return False
    return not _TYPE_NAME_RE.fullmatch(s)


def _kv_repl(m: re.Match[str]) -> str:
    key, op, val = m.group(1), m.group(2), m.group("val")
    if not _assignment_looks_like_secret(val):
        return m.group(0)
    if len(val) >= 2 and val[0] in "'\"" and val[-1] == val[0]:
        return f"{key}{op}{val[0]}[redacted]{val[0]}"
    return f"{key}{op}[redacted]"


def _needs_secret_scan(text: str) -> bool:
    """Cheap gate before multi-regex secret scans (hot path on tool results)."""
    if not text or len(text) < 8:
        return False
    # Pure short alphabetic prose almost never holds keys — skip full scan.
    has_digit = False
    has_special = False
    for c in text:
        if c.isdigit():
            has_digit = True
        elif c in ":=/_+@.-":
            has_special = True
        if has_digit and has_special:
            return True
    if not (has_digit or has_special):
        return False
    low = text.lower() if len(text) <= 400 else text[:400].lower()
    return any(
        k in low
        for k in (
            "key",
            "token",
            "secret",
            "pass",
            "bearer",
            "auth",
            "sk-",
            "sk_",
            "rk_",
            "xai-",
            "ghp_",
            "xox",
            "xapp",
            "akia",
            "aiza",
            "hf_",
            "npm_",
            "ya29",
            "syt_",
            "discord",
            "webhook",
            "begin ",
            "mongo",
            "postgre",
            "mysql",
            "redis",
            "eyj",
            "private",
        )
    )


def redact_text(text: str) -> str:
    """Redact secret-shaped substrings in free text."""
    if not text:
        return ""
    if not _needs_secret_scan(text):
        return text
    out = _KV_ASSIGN_RE.sub(_kv_repl, text)
    for rx in _SECRET_PATTERNS:
        out = rx.sub("[redacted]", out)
    return out


def looks_like_secret_text(text: str) -> bool:
    if not text or len(text) < 8:
        return False
    t = text.strip()
    if not _needs_secret_scan(t):
        return False
    if any(rx.search(t) for rx in _SECRET_PATTERNS):
        return True
    return any(
        _assignment_looks_like_secret(m.group("val"))
        for m in _KV_ASSIGN_RE.finditer(t)
    )


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
