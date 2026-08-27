"""Hybrid target router: browser rail vs full desktop."""

from __future__ import annotations

import re
from enum import StrEnum


class ComputerTarget(StrEnum):
    BROWSER = "browser"
    DESKTOP = "desktop"


_URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"']+|(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|ai|app|co|uk|edu|gov)(?:/[^\s<>\"']*)?)"
)

_METADATA_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "metadata",
    "instance-data",
    "kubernetes.default.svc",
}
_WILDCARD_IP_DNS = (".nip.io", ".sslip.io", ".xip.io")


def _is_blocked_metadata_host(host: str) -> bool:
    """True for IMDS / link-local / wildcard-DNS hosts (not just 169.254.169.254)."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    if h in _METADATA_HOSTS:
        return True
    if h.endswith(".internal") or h.endswith(".localhost"):
        return True
    labels = h.split(".")
    if "metadata" in labels:
        return True
    if any(h.endswith(sfx) for sfx in _WILDCARD_IP_DNS):
        return True
    return bool(h.startswith("169.254."))

# Phrases that usually need OS control beyond the in-rail browser.
_DESKTOP_HINTS = re.compile(
    r"(?i)\b("
    r"desktop|start\s*menu|taskbar|explorer|file\s*explorer|"
    r"installer|setup\.exe|msi\b|notepad|calculator|calc\.exe|"
    r"settings\s*app|control\s*panel|system\s*settings|"
    r"discord\s*app|slack\s*app|steam|photoshop|"
    r"winword|word\.exe|ms\s*word|excel\.exe|excel\b|"
    r"native\s*app|other\s*window|outside\s*the\s*browser|"
    r"whole\s*screen|primary\s*monitor|click\s*on\s*the\s*desktop|"
    # Games / compiled apps — must not be driven through the Browser rail
    r"pygame|godot|unity|sdl\b|win32|"
    r"play\s+(it|the\s+game|the\s+app)|"
    r"compiled\s+(game|app|program)|"
    r"window\s+title|focus\s+(the\s+)?window|"
    r"\w+\.exe"
    r")\b"
)

_BROWSER_HINTS = re.compile(
    r"(?i)\b("
    r"in[- ]?app\s*browser|browser\s*rail|embedded\s*browser|"
    r"web\s*page|website|url|http|https|form\s*on\s*the\s*site|"
    r"cloud\s*console|docs\s*site|github\.com|wiki|fandom|wikipedia|"
    r"open\s+(the\s+)?(site|page|link)|show\s+me\s+(the\s+)?(site|page|wiki)"
    r")\b"
)

# User explicitly wants OS / external browser (not the in-app rail).
_SYSTEM_BROWSER_HINTS = re.compile(
    r"(?i)\b("
    r"system\s*browser|external\s*browser|default\s*browser|"
    r"outside\s*(the\s*)?(app|remedy)|in\s*firefox|in\s*chrome|in\s*edge|"
    r"open\s+externally|not\s+in[- ]?(the\s+)?rail"
    r")\b"
)


def wants_system_browser(hint: str | None = None, target: str | None = None) -> bool:
    """True only when the user/model explicitly asks for OS browser."""
    t = (target or "").strip().lower()
    # Never treat "remedy browser" / "rail" as system browser
    if wants_rail_browser(hint):
        return False
    if t in ("external", "system"):
        return True
    return bool(_SYSTEM_BROWSER_HINTS.search(hint or ""))


_RAIL_HINTS = re.compile(
    r"(?i)\b("
    r"rail|in[- ]?app|embedded|remedy\s*browser|browser\s*rail|"
    r"workspace\s*browser|side\s*browser|inside\s*remedy"
    r")\b"
)


def wants_rail_browser(hint: str | None = None) -> bool:
    """User asked for the in-app Browser rail specifically."""
    return bool(_RAIL_HINTS.search(hint or ""))


def looks_like_url(text: str | None) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    # Reject emails and multi-word phrases (user task text is not a URL)
    if "@" in t and not t.startswith(("http://", "https://")):
        return False
    if " " in t or "\n" in t or "\t" in t:
        return False
    # about: for in-rail blanks; never treat file:/javascript: as navigate URLs
    if t.startswith(("http://", "https://", "about:")):
        return True
    if t.startswith(("file:", "javascript:", "data:", "vbscript:")):
        return False
    return bool(_URL_RE.fullmatch(t) or re.match(
        r"^(?:www\.)?[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+(?:/.*)?$",
        t,
    ))


def is_valid_navigate_url(url: str | None) -> bool:
    """True only for real http(s)/about URLs safe to load in the Browser rail.

    Blocks leaked task text like \"gmail sign in, type user@…\" which used to
    become ``https://gmail sign in…`` and show in the address bar.
    """
    u = (url or "").strip()
    if not u:
        return False
    if any(c in u for c in (" ", "\n", "\t", ",", ";", '"', "'")):
        # commas/spaces never appear in a clean host path before query
        # allow query string commas rarely — reject host part
        host_part = u.split("?", 1)[0].split("#", 1)[0]
        if any(c in host_part for c in (" ", "\n", "\t", ",", '"', "'")):
            return False
    try:
        from urllib.parse import urlparse

        p = urlparse(u if "://" in u or u.startswith("about:") else f"https://{u}")
        if p.scheme not in ("http", "https", "about"):
            return False
        if p.scheme == "about":
            return True
        # Block credentials in URL userinfo (user:pass@host).
        # Use ``is not None`` — empty-string user/pass (https://:@host) still
        # means userinfo was present and must not land in the rail.
        if p.username is not None or p.password is not None:
            return False
        host = (p.hostname or "").strip().lower()
        if not host or " " in host or "," in host:
            return False
        # Cloud metadata / wildcard-DNS-to-IMDS (not just the literal IP).
        if _is_blocked_metadata_host(host):
            return False
        # Remedy's own local API — never load /api/settings or keys into the rail.
        if p.port == 7400:
            return False
        # Loopback (Comfy 8188, Ollama, LM Studio, …) is the owner's machine.
        # RFC1918 / unspecified stay out — the rail is not a LAN scanner.
        if host == "localhost" or host.endswith(".localhost"):
            return True
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            return host.startswith("127.")
        return bool(re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", host))
    except Exception:
        return False


def normalize_url(url: str) -> str:
    """Normalize nicknames/bare hosts to https URL; return \"\" if invalid."""
    u = (url or "").strip()
    if not u:
        return ""
    # Never promote task prose / emails to https://
    if "@" in u and not u.startswith(("http://", "https://")):
        # nickname path may still resolve (gmail alone has no @)
        try:
            from remedy.core.computer.browse_intent import resolve_site_alias

            alias = resolve_site_alias(u)
            if alias and is_valid_navigate_url(alias):
                return alias
        except Exception:
            pass
        return ""
    if any(c in u for c in (" ", "\n", "\t")) and not u.startswith("about:"):
        # Try alias on first token only
        first = u.split()[0].strip(",.;:")
        try:
            from remedy.core.computer.browse_intent import resolve_site_alias

            alias = resolve_site_alias(first)
            if alias and is_valid_navigate_url(alias):
                return alias
        except Exception:
            pass
        return ""
    if u.startswith(("http://", "https://", "about:")):
        return u if is_valid_navigate_url(u) else ""
    if u.startswith("www."):
        cand = "https://" + u
        return cand if is_valid_navigate_url(cand) else ""
    # bare domain
    if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", u):
        cand = "https://" + u
        return cand if is_valid_navigate_url(cand) else ""
    # Nicknames: gmail → mail.google.com
    try:
        from remedy.core.computer.browse_intent import resolve_site_alias

        alias = resolve_site_alias(u)
        if alias and is_valid_navigate_url(alias):
            return alias
    except Exception:
        pass
    return ""


def resolve_target(
    requested: str | None = None,
    *,
    url: str | None = None,
    hint: str | None = None,
    action: str | None = None,
) -> ComputerTarget:
    """Choose browser vs desktop for a computer action.

    *requested*: ``auto`` | ``browser`` | ``desktop`` (model argument).
    *url*: navigation URL if any.
    *hint*: free text from the tool call or last user intent.
    *action*: tool action name (navigate defaults toward browser when URL present).
    """
    req = (requested or "auto").strip().lower()
    if req in ("browser", "web", "rail"):
        return ComputerTarget.BROWSER
    if req in ("desktop", "os", "screen", "system"):
        return ComputerTarget.DESKTOP

    # auto
    blob = " ".join(x for x in (hint or "", url or "", action or "") if x)

    if looks_like_url(url) or looks_like_url(blob):
        # Explicit desktop override still wins via requested; here auto + URL → browser
        if _DESKTOP_HINTS.search(blob) and not _BROWSER_HINTS.search(blob):
            # "open this URL in the system browser" style → still browser target
            # for in-house rail unless clearly OS-only
            if action == "navigate" or looks_like_url(url):
                return ComputerTarget.BROWSER
        return ComputerTarget.BROWSER

    if _DESKTOP_HINTS.search(blob):
        return ComputerTarget.DESKTOP
    if _BROWSER_HINTS.search(blob):
        return ComputerTarget.BROWSER

    # navigate without URL still prefers browser (open rail / about:blank)
    if (action or "").lower() in ("navigate", "computer_navigate"):
        return ComputerTarget.BROWSER

    # Default: desktop for non-web computer actions (click/type on OS).
    # Web navigate / URL-ish work already returned BROWSER above.
    return ComputerTarget.DESKTOP


def host_label(target: ComputerTarget) -> str:
    return "browser" if target is ComputerTarget.BROWSER else "desktop"


# Snapshot / click refs: e1… = browser DOM; w1… windows + c1… UIA controls;
# o1… = OCR word boxes from a screenshot (rail or desktop — use last_elements target).
_REF_BROWSER_RE = re.compile(r"^e\d+$", re.IGNORECASE)
_REF_DESKTOP_RE = re.compile(r"^[wc]\d+$", re.IGNORECASE)
_REF_OCR_RE = re.compile(r"^o\d+$", re.IGNORECASE)


def infer_sticky_target(
    requested: str | None = None,
    *,
    action: str | None = None,
    ref: str | None = None,
    hint: str | None = None,
    url: str | None = None,
    last_target: str | None = None,
    last_elements_target: str | None = None,
) -> str:
    """Resolve ``auto`` using ref prefix + last successful drive target.

    Explicit ``browser`` / ``desktop`` always wins. ``eN`` is rail DOM;
    ``wN`` / ``cN`` are desktop UIA. After ``computer_app`` or a desktop
    snapshot, auto stays on desktop until a URL/navigate. Desktop hints
    (game / window / .exe) override a stale browser sticky so playing a
    compiled app is not sent to the in-app rail.
    """
    req = (requested or "auto").strip().lower()
    if req in ("browser", "web", "rail"):
        return "browser"
    if req in ("desktop", "os", "screen", "system", "external"):
        return "desktop"

    ref_s = (ref or "").strip()
    if _REF_DESKTOP_RE.match(ref_s):
        return "desktop"
    if _REF_BROWSER_RE.match(ref_s):
        return "browser"
    if _REF_OCR_RE.match(ref_s):
        last_el = (last_elements_target or "").strip().lower()
        if last_el in ("browser", "web", "rail"):
            return "browser"
        if last_el in ("desktop", "os", "screen"):
            return "desktop"

    act = (action or "").strip().lower()
    if act in ("app", "windows", "monitors"):
        return "desktop"
    if act in ("navigate", "computer_navigate", "page_text"):
        return "browser"

    blob = " ".join(x for x in (hint or "", url or "", act) if x)
    if looks_like_url(url) or looks_like_url((url or "").strip()):
        return "browser"
    if _DESKTOP_HINTS.search(blob) and not _BROWSER_HINTS.search(blob):
        return "desktop"
    if _BROWSER_HINTS.search(blob):
        return "browser"

    last = (last_target or last_elements_target or "").strip().lower()
    if last in ("desktop", "os", "screen"):
        return "desktop"
    if last in ("browser", "web", "rail"):
        return "browser"

    return "auto"
