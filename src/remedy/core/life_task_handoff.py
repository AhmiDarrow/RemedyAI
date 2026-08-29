"""Resume a life-task after the owner clears a password / 2FA / CAPTCHA wall.

Pay / send / delete never auto-resume. Those stay a Yes on the card.
When the Browser rail is up and the wall URL/text is gone, remaining steps
run — the checkpoint itself is never pressed.
"""

from __future__ import annotations

import re
from typing import Any

from remedy.core.build_oracle import coerce_text_arg
from remedy.core.life_task_drive import step_is_checkpoint

_CAPTCHA_RE = re.compile(
    r"(?is)(captcha|hcaptcha|recaptcha|turnstile|not a robot|"
    r"verify you are human|press.?and.?hold|human.?check)"
)
_PASSWORD_RE = re.compile(
    r"(?is)(\benter (your )?password\b|\bpassword\b.*\bsign in\b|"
    r"\bcurrent password\b)"
)
_2FA_RE = re.compile(
    r"(?is)(two[-\s]?factor|2fa|one[-\s]?time (code|password)|authenticator|"
    r"verification code|otp\b)"
)
_LOGIN_URL_RE = re.compile(
    r"(?is)(/log-?in|/sign-?in|/signin|/challenge|/captcha|/verify|"
    r"accounts\.google|/checkpoint|recaptcha)"
)
_PAY_RE = re.compile(
    r"(?is)(place order|pay now|buy now|complete purchase|submit (the )?(order|payment))"
)


def classify_handoff(step: dict[str, Any] | None) -> str:
    """captcha | password | 2fa | pay | send | other."""
    if not step or not isinstance(step, dict):
        return ""
    kind = str(step.get("kind") or step.get("checkpoint") or "").strip().lower()
    if kind in {"pay", "payment", "submit"}:
        return "pay"
    if kind in {"send", "delete"}:
        return kind
    if kind in {"password", "captcha", "2fa"}:
        return kind
    blob = " ".join(
        str(step.get(k) or "")
        for k in ("title", "text", "click", "label", "detail", "action")
    )
    if _PAY_RE.search(blob):
        return "pay"
    if _CAPTCHA_RE.search(blob):
        return "captcha"
    if _2FA_RE.search(blob):
        return "2fa"
    if _PASSWORD_RE.search(blob) or re.search(r"(?i)\b(sign in|log in|password)\b", blob):
        return "password"
    if step_is_checkpoint(step):
        return "other"
    return ""


def auto_resume_kind(kind: str) -> bool:
    """True when clearing the wall should continue (never pay/send/delete)."""
    return kind in {"captcha", "password", "2fa"}


def wall_present(page_text: str = "", url: str = "") -> set[str]:
    blob = f"{page_text or ''} {url or ''}"
    found: set[str] = set()
    if _CAPTCHA_RE.search(blob) or _LOGIN_URL_RE.search(url or ""):
        if _CAPTCHA_RE.search(blob):
            found.add("captcha")
    if _LOGIN_URL_RE.search(url or ""):
        found.add("password")
    if _PASSWORD_RE.search(page_text or ""):
        found.add("password")
    if _2FA_RE.search(blob):
        found.add("2fa")
    return found


def wall_cleared(
    kind: str,
    *,
    page_text: str = "",
    url: str = "",
    paused_url: str = "",
    rail_ready: bool = False,
) -> bool:
    """True when the owner finished the wall and the rail can be driven again."""
    if not auto_resume_kind(kind):
        return False
    if not rail_ready:
        return False
    u = coerce_text_arg(url)
    paused = coerce_text_arg(paused_url)
    present = wall_present(page_text, u)
    if kind in present:
        return False
    if page_text.strip():
        return kind not in present
    # URL-only: navigation off the paused challenge URL, or off a login path.
    if paused and u and u.rstrip("/") != paused.rstrip("/"):
        return not _LOGIN_URL_RE.search(u)
    return bool(u and not _LOGIN_URL_RE.search(u))


def handoff_payload(step: dict[str, Any] | None, *, paused_url: str = "") -> dict[str, Any]:
    kind = classify_handoff(step)
    if not kind:
        return {}
    return {
        "kind": kind,
        "auto": auto_resume_kind(kind),
        "paused_url": coerce_text_arg(paused_url),
    }
