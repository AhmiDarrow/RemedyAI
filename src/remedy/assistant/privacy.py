"""Privacy helpers: consent gates + minimize data exposed to models/logs."""

from __future__ import annotations

from typing import Any

# Hard cap on body/snippet text in tool results (characters)
MAIL_SNIPPET_MAX = 160
MAIL_BODY_MAX = 2500
BRIEF_SNIPPET_MAX = 80

# Bump when Google OAuth SCOPES or privacy terms expand so users re-accept.
# Empty stored version is grandfathered (existing installs) until next Connect.
CURRENT_CONSENT_VERSION = "gmail_ro_compose_cal_events_v1"


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
    cv = str(getattr(prefs, "consent_version", "") or "").strip()
    if cv and cv != CURRENT_CONSENT_VERSION:
        return (
            False,
            "Privacy terms or account scopes were updated — re-accept in "
            "Settings → Personal assistant → Connect.",
        )
    return True, ""


def require_consent(home: Any = None) -> None:
    ok, reason = consent_ok(home)
    if not ok:
        raise PermissionError(reason)


def redact_secrets(obj: Any) -> Any:
    from remedy.core.metabolism.redact import redact_obj

    return redact_obj(obj)


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
