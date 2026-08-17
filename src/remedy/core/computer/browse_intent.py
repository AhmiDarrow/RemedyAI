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
    # Retail / grocery — Remedy shops for owners who cannot drive the mouse.
    "walmart": "https://www.walmart.com",
    "target": "https://www.target.com",
    "best buy": "https://www.bestbuy.com",
    "bestbuy": "https://www.bestbuy.com",
    "costco": "https://www.costco.com",
    "sams club": "https://www.samsclub.com",
    "sam's club": "https://www.samsclub.com",
    "kroger": "https://www.kroger.com",
    "aldi": "https://www.aldi.us",
    "walgreens": "https://www.walgreens.com",
    "cvs": "https://www.cvs.com",
    "home depot": "https://www.homedepot.com",
    "homedepot": "https://www.homedepot.com",
    "lowes": "https://www.lowes.com",
    "lowe's": "https://www.lowes.com",
    "ebay": "https://www.ebay.com",
    "etsy": "https://www.etsy.com",
    "chewy": "https://www.chewy.com",
    "instacart": "https://www.instacart.com",
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

# Multi-step UI work after open — must NOT short-circuit after navigate alone.
# Verb-PHRASES only: bare nouns like "book"/"order"/"cart"/"post" wrecked the
# wiki/search open-only kicks ("show me the jungle book wiki", "search google
# for best cart") — see tests. Commerce verbs need an object/context to count.
_INTERACTION_RE = re.compile(
    r"(?is)("
    r"\bsign\s*in\b|\blog\s*in\b|\blogin\b|\blog\s*me\s*in\b|\bsign\s*me\s*in\b|"
    r"\bpassword\b|\busername\b|\buser\s*name\b|\bemail\s*address\b|"
    r"\btype\b|\bfill\s+(?:in|out)\b|\bclick\b|\bpress\b|\bsubmit\b|"
    r"\bonce\s+there\b|\bafter\s+that\b|\band\s+then\b|"
    r"\blog\s+me\b|\bsign\s+me\b|\bauthenticate\b|\bcredentials\b|"
    # Life-task / commerce verb-phrases — "goto amazon and order X" is a full
    # task, never an open-only browse (docs/LIFE_TASK_PARTNER.md). "order" is
    # gated to exclude the nouns that wrecked wiki kicks: "order of (the
    # phoenix)" and "in order to".
    r"\bbuy\b|"
    r"(?<!in\s)\border\s+(?!of\b)(?!to\b)\w|"
    r"\bpurchase\b|\bcheck\s*out\b|\badd\s+to\s+(?:the\s+|my\s+)?(?:cart|basket|bag)\b|"
    r"\bplace\s+(?:an?\s+)?order\b|\bplace\s+the\s+order\b|"
    r"\bbook\s+(?:me\s+|a\s+|an\s+|the\s+)|\breserve\s+(?:me\s+|a\s+|an\s+|the\s+)|"
    r"\bschedule\s+(?:me\s+|a\s+|an\s+|the\s+|my\s+)|"
    r"\bapply\s+for\b|\bpay\s+(?:for|my|the)\b|\brenew\s+(?:my|the)\b|"
    r"\bsign\s+up\b|\bsubscribe\s+to\b|\benroll\b|\bdonate\b|"
    r"\bcancel\s+(?:my|the|an?)\b|\breturn\s+(?:my|the|an?)\b|\btrack\s+(?:my|the|an?)\b|"
    r"\breply\s+to\b|\bpost\s+(?:a|an|my|the)\b"
    r")"
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
    retail = _retail_search_url(site, q)
    if retail:
        return retail
    return None


# Product search lands DIRECTLY on results — never the homepage, never the
# header's store/ZIP locator ("milk" typed there → /store-finder, lost turn).
_RETAIL_SEARCH: dict[str, str] = {
    "walmart": "https://www.walmart.com/search?q={q}",
    "target": "https://www.target.com/s?searchTerm={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "best buy": "https://www.bestbuy.com/site/searchpage.jsp?st={q}",
    "costco": "https://www.costco.com/CatalogSearch?keyword={q}",
    "samsclub": "https://www.samsclub.com/s/{q}",
    "sams club": "https://www.samsclub.com/s/{q}",
    "sam's club": "https://www.samsclub.com/s/{q}",
    "kroger": "https://www.kroger.com/search?query={q}",
    "walgreens": "https://www.walgreens.com/search/results.jsp?Ntt={q}",
    "cvs": "https://www.cvs.com/search?searchTerm={q}",
    "home depot": "https://www.homedepot.com/s/{q}",
    "homedepot": "https://www.homedepot.com/s/{q}",
    "lowes": "https://www.lowes.com/search?searchTerm={q}",
    "lowe's": "https://www.lowes.com/search?searchTerm={q}",
    "ebay": "https://www.ebay.com/sch/i.html?_nkw={q}",
    "etsy": "https://www.etsy.com/search?q={q}",
    "chewy": "https://www.chewy.com/s?query={q}",
}


def _retail_search_url(site: str, q: str) -> str | None:
    tpl = _RETAIL_SEARCH.get((site or "").strip().lower())
    if not tpl or not q:
        return None
    return tpl.format(q=quote_plus(q))


def retail_search_url(site: str, query: str) -> str | None:
    """Public: direct product-search URL for a known retailer (or None)."""
    return _retail_search_url(site, _clean_target(query))


# "go to walmart and find whole milk", "target search paper towels",
# "walmart buy a gallon of milk" → straight to the retailer's results page.
# Built once from the retailer table so a new alias needs no second edit.
# Unambiguous shopping verbs only. Bare "get"/"grab" were dropped: they turned
# product phrases that merely start with them ("walmart get well soon card")
# into verb+remainder mis-parses ("well soon card"). "get me…"/"go get" still
# route via the general life-task path; here we keep the search kick precise.
_RETAIL_VERB = (
    r"search(?:\s+for)?|look\s+up|look\s+for|find|shop\s+for|"
    r"buy|order|purchase|pick\s+up|browse\s+for|add"
)


_RETAIL_SEARCH_RE: re.Pattern[str] | None = None


def _retail_search_matcher() -> re.Pattern[str]:
    global _RETAIL_SEARCH_RE
    rx = _RETAIL_SEARCH_RE
    if rx is None:
        names = sorted((re.escape(k) for k in _RETAIL_SEARCH), key=len, reverse=True)
        alt = "|".join(names)
        # The product is the FIRST clause after the verb — stop at a sentence
        # or clause boundary so a multi-step prompt ("...find whole milk. Add
        # one gallon to the cart, then stop...") searches "whole milk", not the
        # whole paragraph jammed into ?q=.
        rx = re.compile(
            r"(?is)^\s*(?:please\s+)?"
            r"(?:goto|go\s+to|open|visit|on|at|from)?\s*"
            r"(?P<site>" + alt + r")\s*[,;:]?\s+"
            r"(?:and\s+)?(?:" + _RETAIL_VERB + r")\s+"
            r"(?:me\s+)?(?:a\s+|an\s+|some\s+|the\s+|one\s+)?(?P<q>.+?)"
            r"(?=[.,;:!?\n]|\s+(?:then\b|and\s+(?:then|stop|hand|add|put)\b|"
            r"add\s+(?:it|them|one|to)\b|please\b)|\s*$)"
        )
        _RETAIL_SEARCH_RE = rx
    return rx


def parse_retail_search(message: str) -> str | None:
    """'<retailer> <verb> <product>' → the retailer's direct results URL."""
    msg = (message or "").strip()
    if not msg or len(msg) > 280:
        return None
    m = _retail_search_matcher().match(msg)
    if not m:
        return None
    return retail_search_url(m.group("site"), m.group("q"))


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
    return bool(parse_browse_navigate_url(msg))


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

    # Retail product search first — "go to walmart and find milk" lands on
    # the results page, never the homepage/store-locator (a lost turn).
    retail = parse_retail_search(msg)
    if retail:
        return retail

    # Search patterns ("goto google and search elephant")
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
