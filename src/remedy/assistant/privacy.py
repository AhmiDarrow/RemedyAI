"""Privacy helpers: consent gates + minimize data exposed to models/logs."""

from __future__ import annotations

import re
from typing import Any

# Never allow these keys into tool JSON shown to the model
_SECRET_KEY_RE = re.compile(
    r"(?i)(access_token|refresh_token|id_token|client_secret|authorization|"
    r"api_key|password|passwd|bearer|cookie|set-cookie|private_key)"
)

# Hard cap on body/snippet text in tool results (characters)
MAIL_SNIPPET_MAX = 160
MAIL_BODY_MAX = 2500
BRIEF_SNIPPET_MAX = 80


def consent_ok(home: Any = None) -> tuple[bool, str]:
    """Return (ok, reason) for OAuth / account tools."""
    try:
        from remedy.assistant.store import get_assistant_store

        prefs = get_assistant_store(home).get_prefs()
    except Exception:
        return False, "Could not load privacy preferences."
    if not prefs.privacy_ai_accepted:
        return (
            False,
            "Accept the AI & privacy notice in Settings → Personal assistant first.",
        )
    if not prefs.account_access_accepted:
        return (
            False,
            "Accept account access (OAuth) in Settings → Personal assistant first.",
        )
    return True, ""


def require_consent(home: Any = None) -> None:
    ok, reason = consent_ok(home)
    if not ok:
        raise PermissionError(reason)


def redact_secrets(obj: Any) -> Any:
    """Deep-copy-ish redaction of secret-looking keys from dict/list structures."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if _SECRET_KEY_RE.search(str(k)):
                out[str(k)] = "[redacted]"
            else:
                out[str(k)] = redact_secrets(v)
        return out
    if isinstance(obj, list):
        return [redact_secrets(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 40:
        # Bearer-looking strings
        if re.match(r"(?i)^(ya29\.|1//|eyJ|sk-|xox)", obj.strip()):
            return "[redacted]"
    return obj


def clip(text: str, max_len: int) -> str:
    s = (text or "").replace("\r", " ").strip()
    if len(s) <= max_len:
        return s
    return s[: max(0, max_len - 1)] + "…"


def sanitize_mail_list_item(row: dict[str, Any]) -> dict[str, Any]:
    """Minimize list row for model consumption."""
    return {
        "id": str(row.get("id") or "")[:128],
        "subject": clip(str(row.get("subject") or ""), 120),
        "from": clip(str(row.get("from") or row.get("from_addr") or ""), 80),
        "snippet": clip(str(row.get("snippet") or ""), MAIL_SNIPPET_MAX),
        "date": clip(str(row.get("date") or ""), 40),
        "thread_id": str(row.get("thread_id") or "")[:128],
    }


def sanitize_mail_body(body: str) -> str:
    return clip(body or "", MAIL_BODY_MAX)
