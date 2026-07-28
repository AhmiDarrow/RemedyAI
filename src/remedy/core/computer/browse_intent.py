"""High-confidence browse intents → in-app Browser rail URL.

Used so short kicks like "goto gmail" / "bring up google" open the rail even
when the model only narrates intent (tools gated off / no tool_calls).
"""

from __future__ import annotations

import re
from urllib.parse import quote

from remedy.core.computer.router import looks_like_url, normalize_url

# Common nicknames → full https URL (lowercase keys).
SITE_ALIASES: dict[str, str] = {
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "google mail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "github": "https://github.com",
    "gh": "https://github.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "x.com": "https://x.com",
    "facebook": "https://www.facebook.com",
    "fb": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "ig": "https://www.instagram.com",
    "linkedin": "https://www.linkedin.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "docs": "https://docs.google.com",
    "calendar": "https://calendar.google.com",
    "wikipedia": "https://en.wikipedia.org",
    "wiki": "https://en.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
}

# Imperative open/goto/show for a site or URL (short single-intent messages).
_BROWSE_CMD_RE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:please\s+)?"
    r"(?:can\s+you\s+)?"
    r"(?:goto|go\s+to|open|launch|load|visit|navigate\s+to|take\s+me\s+to|"
    r"bring\s+up|pull\s+up|fire\s+up|show\s+me|show|"
    r"open\s+up|head\s+to|jump\s+to)"
    r")\s+"
    r"(?P<target>.+?)"
    r"\s*[.!]?\s*$"
)

# "gta 5 wiki" / "baldur's gate wiki" style without an imperative verb.
_WIKI_TOPIC_RE = re.compile(
    r"(?is)^\s*(?:show\s+me\s+)?(?P<topic>.+?)\s+wiki(?:pedia)?\s*(?:show\s+me\s+it)?\s*[.!]?\s*$"
)

_STRIP_FILLER_RE = re.compile(
    r"(?is)\b("
    r"the\s+(?:page|site|website|app)|"
    r"in\s+(?:the\s+)?(?:browser|rail|remedy)|"
    r"for\s+me|please|now|there|here"
    r")\b"
)


def resolve_site_alias(name: str) -> str | None:
    """Map a nickname or bare host to https URL, or None if unknown."""
    raw = (name or "").strip().strip("\"'`")
    if not raw:
        return None
    key = re.sub(r"\s+", " ", raw.lower())
    key = key.rstrip("/.")
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    # strip leading www.
    if key.startswith("www.") and key[4:] in SITE_ALIASES:
        return SITE_ALIASES[key[4:]]
    # bare domain already
    if looks_like_url(raw) or re.match(
        r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", raw
    ):
        return normalize_url(raw)
    return None


def _clean_target(target: str) -> str:
    t = (target or "").strip().strip("\"'`")
    t = _STRIP_FILLER_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,!")
    return t


def short_site_label(url: str) -> str:
    """Human label for a brief confirmation (Gmail, Google, Wikipedia, …)."""
    u = (url or "").strip().lower()
    if "mail.google" in u or "gmail" in u:
        return "Gmail"
    if "google.com" in u and "mail" not in u:
        return "Google"
    if "youtube" in u:
        return "YouTube"
    if "wikipedia" in u:
        return "Wikipedia"
    if "github.com" in u:
        return "GitHub"
    if "reddit.com" in u:
        return "Reddit"
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host:
            return host
    except Exception:
        pass
    return url or "page"


def parse_browse_navigate_url(message: str) -> str | None:
    """If *message* is a high-confidence browse request, return the rail URL.

    Returns None when the message is ambiguous (coding task, multi-step, chat).
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 160:
        # Long prompts are multi-intent — leave to the model.
        return None
    # Full URL alone
    if looks_like_url(msg) and " " not in msg.strip():
        return normalize_url(msg)

    m = _BROWSE_CMD_RE.match(msg)
    if m:
        target = _clean_target(m.group("target") or "")
        if not target:
            return None
        # "gmail in the rail" already cleaned
        url = resolve_site_alias(target)
        if url:
            return url
        if looks_like_url(target):
            return normalize_url(target)
        # "gta 5 wiki" as target of "show me"
        wm = _WIKI_TOPIC_RE.match(target) or _WIKI_TOPIC_RE.match(msg)
        if wm:
            topic = _clean_target(wm.group("topic") or "")
            if topic and topic.lower() not in ("the", "a", "an"):
                return f"https://en.wikipedia.org/wiki/{quote(topic.replace(' ', '_'))}"
        return None

    # Bare "gta 5 wiki show me it"
    wm = _WIKI_TOPIC_RE.match(msg)
    if wm:
        topic = _clean_target(wm.group("topic") or "")
        if topic and len(topic) < 80:
            return f"https://en.wikipedia.org/wiki/{quote(topic.replace(' ', '_'))}"
    return None
