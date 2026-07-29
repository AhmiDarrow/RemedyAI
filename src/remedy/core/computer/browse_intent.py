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

# Multi-step UI work after open — must NOT short-circuit after navigate alone
_INTERACTION_RE = re.compile(
    r"(?is)\b("
    r"sign\s*in|log\s*in|login|log\s*me\s*in|sign\s*me\s*in|"
    r"password|username|user\s*name|email\s*address|"
    r"type|enter|fill|input|click|press|submit|select|"
    r"once\s+there|then\s+|after\s+that|and\s+then|"
    r"log\s+me|sign\s+me|authenticate|credentials"
    r")\b"
)


def resolve_site_alias(name: str) -> str | None:
    """Map a nickname or bare host to https URL, or None if unknown."""
    raw = (name or "").strip().strip("\"'`")
    if not raw:
        return None
    # Never treat emails / multi-clause phrases as a site URL
    if "@" in raw:
        return None
    key = re.sub(r"\s+", " ", raw.lower())
    key = key.rstrip("/.")
    if key in SITE_ALIASES:
        return SITE_ALIASES[key]
    # strip leading www.
    if key.startswith("www.") and key[4:] in SITE_ALIASES:
        return SITE_ALIASES[key[4:]]
    # Multi-word non-alias (e.g. "gmail sign in…") — not a site
    if " " in key:
        return None
    # bare domain only (no spaces)
    if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", raw):
        return normalize_url(raw)
    if raw.startswith(("http://", "https://")):
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


def wants_page_interaction(message: str) -> bool:
    """True when the user wants to act on the page after open (login, type, click…)."""
    return bool(_INTERACTION_RE.search(message or ""))


def is_open_only_browse(message: str) -> bool:
    """True when the request is only open/show a URL — safe to short-circuit after navigate."""
    msg = (message or "").strip()
    if not msg:
        return False
    if wants_page_interaction(msg):
        return False
    if is_clear_goals_intent(msg):
        return False
    return parse_browse_navigate_url(msg) is not None


def is_pure_action_kick(message: str) -> bool:
    """Short latest-message-only kicks (don't resume older multi-step work)."""
    msg = (message or "").strip()
    if not msg or len(msg) > 200:
        return False
    if is_clear_goals_intent(msg):
        return True
    # Multi-step login/click tasks are not pure kicks — full agent loop required
    if wants_page_interaction(msg):
        return False
    if parse_browse_navigate_url(msg):
        return True
    return False


def _first_site_token(target: str) -> str | None:
    """'gmail sign in once…' → gmail; 'google and search x' handled elsewhere."""
    t = _clean_target(target)
    if not t:
        return None
    # Stop at interaction verbs so "gmail sign in" → gmail
    m = re.match(
        r"(?is)^(?P<head>[a-z0-9][a-z0-9.-]*)\b",
        t,
    )
    if not m:
        return None
    head = m.group("head").lower()
    if head in SITE_ALIASES or head in (
        "google",
        "gmail",
        "youtube",
        "github",
        "reddit",
        "patreon",
    ):
        return head
    return None


def parse_browse_navigate_url(message: str) -> str | None:
    """If *message* opens a site (with or without follow-up work), return the rail URL.

    For multi-step messages (login/type after open), still returns the open URL so
    the agent can pre-navigate; short-circuit after navigate is gated separately.
    """
    msg = (message or "").strip()
    if not msg or len(msg) > 280:
        return None
    # Full URL alone
    if looks_like_url(msg) and " " not in msg.strip() and "@" not in msg:
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
        nested = re.match(
            r"(?is)^(?P<site>google|bing|youtube)\s+"
            r"(?:and\s+)?(?:search(?:\s+for)?|look\s+up|find)\s+(?:for\s+)?(?P<q>.+)$",
            target,
        )
        if nested and not wants_page_interaction(msg):
            url = _search_url(nested.group("site"), nested.group("q"))
            if url:
                return url

        # Exact site nickname
        url = resolve_site_alias(target)
        if url:
            return url
        # "gmail sign in, once there…" → first token site
        head = _first_site_token(target)
        if head:
            url = resolve_site_alias(head)
            if url:
                return url
        # Bare http(s) only — never treat emails as URLs
        if target.startswith(("http://", "https://", "www.")):
            return normalize_url(target)
        # "gta 5 wiki" as target of "show me"
        if not wants_page_interaction(msg):
            wm = _WIKI_TOPIC_RE.match(target) or _WIKI_TOPIC_RE.match(msg)
            if wm:
                topic = _clean_target(wm.group("topic") or "")
                if topic and topic.lower() not in ("the", "a", "an"):
                    return f"https://en.wikipedia.org/wiki/{quote(topic.replace(' ', '_'))}"
        return None

    # Bare "gta 5 wiki show me it"
    if not wants_page_interaction(msg):
        wm = _WIKI_TOPIC_RE.match(msg)
        if wm:
            topic = _clean_target(wm.group("topic") or "")
            if topic and len(topic) < 80:
                return f"https://en.wikipedia.org/wiki/{quote(topic.replace(' ', '_'))}"
    return None
