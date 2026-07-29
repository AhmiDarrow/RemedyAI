"""High-confidence browse intents → in-app Browser rail URL.

Used so short kicks like "goto gmail" / "bring up google" / "goto google and
search elephant" open the rail without the model replaying older tasks.
"""

from __future__ import annotations

import re
from urllib.parse import quote, quote_plus

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
    "google docs": "https://docs.google.com",
    "calendar": "https://calendar.google.com",
    "wikipedia": "https://en.wikipedia.org",
    "wiki": "https://en.wikipedia.org",
    "amazon": "https://www.amazon.com",
    "netflix": "https://www.netflix.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "patreon": "https://www.patreon.com",
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

# goto google and search elephant / go to google search for cats
_SEARCH_ON_SITE_RE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:please\s+)?"
    r"(?:goto|go\s+to|open|visit)\s+"
    r"(?P<site>google|bing|youtube|duckduckgo|ddg)\s+"
    r"(?:and\s+)?(?:search(?:\s+for)?|look\s+up|find|query)\s+"
    r"(?:for\s+)?(?P<q>.+?)"
    r"|"
    r"(?:search|look\s+up|find)\s+(?:for\s+)?(?P<q2>.+?)\s+(?:on|in|via)\s+"
    r"(?P<site2>google|bing|youtube|duckduckgo|ddg)"
    r"|"
    r"(?:search|look\s+up)\s+(?P<site3>google|bing|youtube)\s+(?:for\s+)?(?P<q3>.+?)"
    r"|"
    r"(?P<site4>google|bing)\s+search\s+(?:for\s+)?(?P<q4>.+?)"
    r")\s*[.!]?\s*$"
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

# Clear goals — no web work, no history replay
_CLEAR_GOALS_RE = re.compile(
    r"(?is)^\s*(?:"
    r"(?:just\s+)?clear\s+goals?(?:\s*,?\s*we\s+have\s+none)?|"
    r"(?:please\s+)?clear\s+(?:all\s+)?(?:my\s+)?goals?|"
    r"no\s+goals?(?:\s+please)?|"
    r"goals?\s*:\s*none|"
    r"we\s+have\s+no\s+goals?"
    r")\s*[.!]?\s*$"
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


def _search_url(site: str, query: str) -> str | None:
    site = (site or "").strip().lower()
    q = _clean_target(query)
    if not q or len(q) > 120:
        return None
    if site in ("google",):
        return f"https://www.google.com/search?q={quote_plus(q)}"
    if site in ("bing",):
        return f"https://www.bing.com/search?q={quote_plus(q)}"
    if site in ("youtube", "yt"):
        return f"https://www.youtube.com/results?search_query={quote_plus(q)}"
    if site in ("duckduckgo", "ddg"):
        return f"https://duckduckgo.com/?q={quote_plus(q)}"
    return None


def short_site_label(url: str) -> str:
    """Human label for a brief confirmation (Gmail, Google, Wikipedia, …)."""
    u = (url or "").strip()
    low = u.lower()
    if "google.com/search" in low:
        from urllib.parse import parse_qs, urlparse

        try:
            q = (parse_qs(urlparse(u).query).get("q") or [""])[0]
            if q:
                return f'Google search: "{q}"'
        except Exception:
            pass
        return "Google search"
    if "mail.google" in low or "gmail" in low:
        return "Gmail"
    if "google.com" in low and "mail" not in low:
        return "Google"
    if "youtube" in low:
        return "YouTube"
    if "wikipedia" in low:
        return "Wikipedia"
    if "github.com" in low:
        return "GitHub"
    if "reddit.com" in low:
        return "Reddit"
    if "patreon.com" in low:
        return "Patreon"
    try:
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").removeprefix("www.")
        if host:
            return host
    except Exception:
        pass
    return url or "page"


def is_clear_goals_intent(message: str) -> bool:
    """True when the user only wants goals cleared — no web / history replay."""
    return bool(_CLEAR_GOALS_RE.match((message or "").strip()))


def is_pure_action_kick(message: str) -> bool:
    """Short latest-message-only kicks (don't resume older multi-step work)."""
    msg = (message or "").strip()
    if not msg or len(msg) > 160:
        return False
    if is_clear_goals_intent(msg):
        return True
    if parse_browse_navigate_url(msg):
        return True
    return False


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

    # Search patterns first ("goto google and search elephant")
    sm = _SEARCH_ON_SITE_RE.match(msg)
    if sm:
        site = (
            sm.group("site")
            or sm.group("site2")
            or sm.group("site3")
            or sm.group("site4")
            or "google"
        )
        q = sm.group("q") or sm.group("q2") or sm.group("q3") or sm.group("q4") or ""
        url = _search_url(site, q)
        if url:
            return url

    m = _BROWSE_CMD_RE.match(msg)
    if m:
        target = _clean_target(m.group("target") or "")
        if not target:
            return None
        # Nested: target is "google and search elephant"
        inner = _SEARCH_ON_SITE_RE.match(
            f"goto {target}" if not target.lower().startswith(("goto", "go ")) else target
        ) or _SEARCH_ON_SITE_RE.match(f"go to {target}")
        if not inner:
            # parse "google and search elephant" as site+query without goto prefix
            nested = re.match(
                r"(?is)^(?P<site>google|bing|youtube)\s+"
                r"(?:and\s+)?(?:search(?:\s+for)?|look\s+up|find)\s+(?:for\s+)?(?P<q>.+)$",
                target,
            )
            if nested:
                url = _search_url(nested.group("site"), nested.group("q"))
                if url:
                    return url
        else:
            site = (
                inner.group("site")
                or inner.group("site2")
                or inner.group("site3")
                or inner.group("site4")
                or "google"
            )
            q = (
                inner.group("q")
                or inner.group("q2")
                or inner.group("q3")
                or inner.group("q4")
                or ""
            )
            url = _search_url(site, q)
            if url:
                return url

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
