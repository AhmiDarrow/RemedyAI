"""Hybrid target router: browser rail vs full desktop."""

from __future__ import annotations

import re
from enum import Enum
from urllib.parse import urlparse


class ComputerTarget(str, Enum):
    BROWSER = "browser"
    DESKTOP = "desktop"


_URL_RE = re.compile(
    r"(?i)\b((?:https?://|www\.)[^\s<>\"']+|(?:[a-z0-9-]+\.)+(?:com|org|net|io|dev|ai|app|co|uk|edu|gov)(?:/[^\s<>\"']*)?)"
)

# Phrases that usually need OS control beyond the in-rail browser.
_DESKTOP_HINTS = re.compile(
    r"(?i)\b("
    r"desktop|start\s*menu|taskbar|explorer|file\s*explorer|"
    r"installer|setup\.exe|msi\b|notepad|calculator|calc\.exe|"
    r"settings\s*app|control\s*panel|system\s*settings|"
    r"discord\s*app|slack\s*app|steam|photoshop|word\b|excel\b|"
    r"native\s*app|other\s*window|outside\s*the\s*browser|"
    r"whole\s*screen|primary\s*monitor|click\s*on\s*the\s*desktop"
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
    if t.startswith(("http://", "https://", "about:", "file:")):
        return True
    return bool(_URL_RE.search(t))


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith(("http://", "https://", "about:", "file:")):
        return u
    if u.startswith("www."):
        return "https://" + u
    # bare domain
    if re.match(r"^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/.*)?$", u):
        return "https://" + u
    return u


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
