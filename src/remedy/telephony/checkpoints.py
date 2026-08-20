"""Hard call checkpoints — no approval mode may waive these.

Mirrors computer-use money/credential stops: she will not speak card
numbers, SSNs, passwords, or one-time codes, and she will not agree to a
payment or contract on a live line. The owner has to take that moment.
"""

from __future__ import annotations

import re

# Spoken-form secrets and irreversible agreements. Kept tight so ordinary
# talk ("what's the card for the library", "did they send you a verification
# code?", "I agree, that sounds frustrating") is not blocked: a secret is
# only caught when she would *say* one (a digit run, or "<secret> is …"),
# and an agreement only when it binds the owner to pay/cancel/sign.
_DIGITS = r"(?:\d[ -]?){3,}"
_SECRET_RE = re.compile(
    r"(?is)(?:"
    r"\b(?:\d[ -]?){13,19}\b|"  # PAN-shaped digit run
    r"\b(?:card|account|routing)\s*(?:number|no\.?|#)\s*(?:is|was|:)\s*" + _DIGITS + r"|"
    r"\b(?:cvv|cvc|security\s+code|pin)\s*(?:is|was|:)?\s*" + _DIGITS + r"|"
    r"\b(?:social\s+security|ssn)\s*(?:number|no\.?|#)?\s*(?:is|was|:)\s*" + _DIGITS + r"|"
    r"\b(?:one[-\s]?time|verification|2fa|otp|auth(?:entication)?)\s+"
    r"(?:code|password|passcode)\s*(?:is|was|:)\s*\S+|"
    r"\b(?:the|my|your)\s+code\s+is\s*\S+|"
    r"\bpassword\s+(?:is|was|:)\s*\S+|"
    r"\bpin\s+(?:is|was|:)\s*\S+"
    r")"
)

_AGREE_RE = re.compile(
    r"(?is)\b(?:"
    r"i\s+(?:agree|accept|authorize|consent)\s+to\s+(?:the\s+|that\s+|this\s+|a\s+)?"
    r"(?:pay|charge|fee|price|amount|terms|contract|agreement|purchase|"
    r"subscri|renew|cancel|sign|quote|offer|plan|upgrade|upsell)|"
    r"(?:i|we)\s+(?:accept|authorize)\s+(?:the|that|this)\s+"
    r"(?:charge|payment|fee|terms|contract|offer|quote)|"
    r"go\s+ahead\s+and\s+(?:pay|charge|cancel|sign|renew|bill)|"
    r"yes,?\s+please\s+(?:charge|pay|bill|cancel\s+the|sign\s+me)|"
    r"that\s+is\s+my\s+(?:full\s+)?(?:ssn|social)|"
    r"you\s+(?:can|may)\s+(?:charge|bill)\s+(?:the|my|that)"
    r")"
)

REFUSAL = (
    "I will not say that on a call. That needs you — a card number, a code, "
    "a password, or agreeing to pay or cancel. I can wait while you take it."
)


def may_speak(text: str) -> tuple[bool, str | None]:
    """False + owner-plain refusal when the line would speak a secret or bind them."""
    body = text or ""
    if _SECRET_RE.search(body) or _AGREE_RE.search(body):
        return False, REFUSAL
    return True, None
