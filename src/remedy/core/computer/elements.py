"""Match interactive elements by text/name for click-by-text autonomy.

Inspired by research on GUI agents (OSWorld, ScreenSpot, Set-of-Mark):
prefer structured a11y/DOM labels + numbered marks over raw pixels.
"""

from __future__ import annotations

import re
from typing import Any

_STOP = frozenset(
    ["the", "a", "an", "to", "of", "in", "on", "at", "for", "and", "or", "my", "me", "it", "one", "some", "this", "that", "with", "from", "into", "your", "their"]
)
# Verbs that should land on a button/submit, not a same-named <a> in the nav.
_ACTION_VERBS = frozenset(
    {
        "post",
        "submit",
        "send",
        "tweet",
        "publish",
        "share",
        "continue",
        "next",
        "save",
        "create",
        "reply",
        "comment",
        "search",
        "go",
        "done",
        "apply",
        "confirm",
        "update",
    }
)
_COMPOSER_HINTS = (
    "happening",
    "what's on",
    "whats on",
    "write a",
    "write your",
    "compose",
    "post text",
    "title",
    "body",
    "caption",
    "message",
    "placeholder",
)
_BANNER_RE = re.compile(
    r"(?i)\b(view in .{0,24}app|get the app|download (the )?app|"
    r"open (in|the) app|install (the )?app|open in (chrome|safari|firefox))\b"
)
_CLICK_LANDED_RE = re.compile(
    r"(?i)ok:(\d+):([a-z0-9_-]*):([a-z0-9_-]*):(.*)$"
)


def _norm(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _meaningful_tokens(s: str) -> set[str]:
    """Tokens long enough to identify a control — never the letter 'a'."""
    return {t for t in re.findall(r"[a-z0-9]{3,}", _norm(s)) if t not in _STOP}


def element_label_blob(el: dict[str, Any]) -> str:
    """Searchable label only — not card context, not href.

    Context matching a sibling (composer placeholder on a GIF button) must
    not promote a different control. Href matching `/compose/post` must not
    make a nav <a> win over the submit button.
    """
    parts = [
        str(el.get("name") or ""),
        str(el.get("text") or ""),
        str(el.get("placeholder") or ""),
        str(el.get("aria") or ""),
        str(el.get("title") or ""),
        str(el.get("value") or ""),
    ]
    return _norm(" ".join(parts))


def element_search_blob(el: dict[str, Any]) -> str:
    parts = [
        element_label_blob(el),
        str(el.get("href") or ""),
        str(el.get("tag") or ""),
        str(el.get("role") or ""),
        str(el.get("type") or ""),
        # Card context (the enclosing store/result/product tile's text) lets a
        # query span control-label + surroundings so a generic "Set as store"
        # is found by the store's name/address in its card.
        str(el.get("context") or ""),
    ]
    return _norm(" ".join(parts))


def score_element(el: dict[str, Any], query: str) -> float:
    """Higher is better. Exact name match >> contains >> fuzzy tokens.

    Also boosts semantic field types (email/password) when the query asks for them.
    """
    q = _norm(query)
    if not q:
        return 0.0
    name = _norm(str(el.get("name") or ""))
    placeholder = _norm(str(el.get("placeholder") or ""))
    label = element_label_blob(el)
    blob = element_search_blob(el)
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or "").lower()
    itype = str(el.get("type") or el.get("input_type") or "").lower()
    score = 0.0
    if name == q or (placeholder and placeholder == q):
        score += 100.0
    elif q and (q in name or q in placeholder or q in label):
        score += 70.0
    elif name and len(name) >= 3 and name in q:
        score += 40.0
    # Substring-in-label only. Card context / href must not create a match
    # for a different control (GIF button whose card wraps "What's happening?").
    if q in label:
        score += 25.0
    # token overlap — meaningful tokens only, so a stopword ("to","the") or a
    # 1-char name token ("a" in "Add a GIF") does not fire a click.
    q_toks = _meaningful_tokens(q)
    name_toks = _meaningful_tokens(name + " " + placeholder)
    label_toks = _meaningful_tokens(label)
    if q_toks and label_toks:
        inter = q_toks & label_toks
        score += 15.0 * len(inter) / max(1, len(q_toks))
    # Card-context disambiguation: only when the LABEL already overlaps the
    # query (generic "Set as store") — never to promote a sibling control
    # whose card happens to contain the composer placeholder.
    context = _norm(str(el.get("context") or ""))
    if context and q_toks and (q_toks & name_toks):
        ctx_toks = _meaningful_tokens(context)
        missing = q_toks - name_toks
        in_ctx = missing & ctx_toks
        if in_ctx:
            score += 22.0 * len(in_ctx) / max(1, len(q_toks))
    if context and _BANNER_RE.search(context):
        score -= 40.0
    # Semantic boosts (login forms)
    if any(t in q for t in ("email", "username", "user name", "login", "e-mail")):
        if itype in ("email", "text") or "email" in blob or "user" in blob:
            score += 30.0
        if tag == "input" and itype != "password":
            score += 10.0
    if "password" in q or "passwd" in q:
        if itype == "password" or "password" in blob:
            score += 40.0
    if any(t in q for t in ("sign in", "log in", "login", "submit", "continue", "next")):
        if tag == "button" or role == "button" or itype == "submit":
            score += 20.0
    # "Post" / "Submit" / "Tweet" — prefer the button, not a nav <a> of the
    # same name (X compose: click 'Post' landed on a link, then typed into it).
    q_action = q_toks & _ACTION_VERBS
    if q_action:
        if tag == "button" or role == "button" or itype == "submit":
            score += 25.0
        elif tag == "a" or role == "link":
            score -= 20.0
    # Composer placeholders ("What's happening?") must land on the field,
    # not a toolbar GIF/image/poll control in the same card.
    if any(h in q or h in label for h in _COMPOSER_HINTS):
        if tag in ("textarea", "input") or role in ("textbox", "searchbox") or itype in (
            "text",
            "search",
        ):
            score += 30.0
        elif tag == "button" or role == "button":
            score -= 15.0
    # Prefer real controls over huge containers
    w = float(el.get("w") or 0)
    h = float(el.get("h") or 0)
    if 8 <= w <= 900 and 8 <= h <= 200:
        score += 5.0
    if w * h > 400_000:
        score -= 20.0
    if tag in ("button", "a", "input", "textarea", "summary") or role in (
        "button",
        "link",
        "tab",
        "menuitem",
        "textbox",
    ):
        score += 8.0
    return score


def parse_click_landed(message: str | None, detail: str | None = None) -> dict[str, str]:
    """Parse host click result ``ok:score:tag:itype:name`` (name may contain colons)."""
    for raw in (detail, message):
        blob = str(raw or "").strip()
        if not blob:
            continue
        # Host may wrap: "Clicked text=… (ok:27:button:button:Add a GIF)"
        inner = blob
        if "(" in blob and blob.endswith(")"):
            inner = blob[blob.rfind("(") + 1 : -1]
        m = _CLICK_LANDED_RE.search(inner) or _CLICK_LANDED_RE.search(blob)
        if m:
            return {
                "score": m.group(1),
                "tag": m.group(2),
                "itype": m.group(3),
                "name": m.group(4).strip(),
            }
        # Snapshot-ref form: "Clicked text='Post' → e3 (Post text)"
        arrow = re.search(
            r"→\s*e\d+\s*\(([^)]+)\)",
            blob,
        )
        if arrow:
            return {"score": "", "tag": "", "itype": "", "name": arrow.group(1).strip()}
    return {}


def label_matches_query(name: str, query: str) -> bool:
    """True when a clicked control is actually the one the query asked for.

    Used after click-by-text so ``computer_act`` does not type into a GIF
    search that scored a weak fuzzy hit on "What's happening?".
    """
    q = _norm(query)
    n = _norm(name)
    if not q or not n:
        return False
    if n == q or q in n or (len(n) >= 3 and n in q):
        return True
    q_toks = _meaningful_tokens(q)
    n_toks = _meaningful_tokens(n)
    if not q_toks or not n_toks:
        return False
    return bool(q_toks & n_toks)


def looks_like_field_prompt(query: str) -> bool:
    """Composer / field labels — a click here must not navigate away."""
    q = _norm(query)
    if not q:
        return False
    if q.endswith("?"):
        return True
    return any(h in q for h in _COMPOSER_HINTS)


def urls_path_diverged(pre_url: str, post_url: str) -> bool:
    """True when the path (not just query-string) changed after an action."""
    def _path(u: str) -> str:
        s = (u or "").strip().lower()
        if "://" in s:
            s = s.split("://", 1)[1]
        s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
        # drop host
        if "/" in s:
            return "/" + "/".join(s.split("/")[1:])
        return "/"

    a, b = _path(pre_url), _path(post_url)
    return bool(a and b and a != b)


_MODAL_URL_RE = re.compile(
    r"(?i)(/i/foundmedia|/gif|gif.?search|/download|interstitial|"
    r"play\.google|apps\.apple|/app-store|cookie.?consent)"
)
_MODAL_TITLE_RE = re.compile(
    r"(?i)(gif search|categories — gif|before you continue|enable cookies|"
    r"not a robot|captcha|verify (you|it.s you)|log in to x|"
    r"view in .{0,20}app)"
)
_MODAL_NAME_RE = re.compile(
    r"(?i)^(close|dismiss|got it|accept (all|cookies)|reject all|"
    r"not now|no thanks|maybe later|continue in (browser|app)|"
    r"open (in )?app|view in .{0,20}app)$"
)
_COMPOSE_PATH_RE = re.compile(
    r"(?i)(/compose|/submit/?$|/submit/|/posts/new|/create[-_/]?post|"
    r"/create|/status/compose)"
)
_PUBLISH_VERBS = frozenset(
    {"post", "submit", "tweet", "publish", "send", "share", "reply"}
)


def is_compose_url(url: str) -> bool:
    """True when the URL is still a compose/submit editor, not a live post."""
    s = (url or "").strip().lower()
    if "://" in s:
        s = s.split("://", 1)[1]
    path = "/" + "/".join(s.split("/")[1:]) if "/" in s else s
    path = path.split("?", 1)[0].split("#", 1)[0]
    return bool(_COMPOSE_PATH_RE.search(path))


def looks_like_publish_verb(query: str) -> bool:
    """Click labels that mean 'send this' — Post / Submit / Tweet / Publish."""
    return bool(_meaningful_tokens(query) & _PUBLISH_VERBS)


def detect_modal_obstacle(
    *,
    elements: list[dict[str, Any]] | None = None,
    url: str = "",
    title: str = "",
    text: str = "",
) -> dict[str, str] | None:
    """A dialog/popup is in front of the task (GIF picker, app banner, cookie).

    Callers should stop and handle this surface instead of typing into the
    page underneath or claiming Post succeeded.
    """
    u = (url or "").strip()
    ttl = (title or "").strip()
    body = (text or "").strip()
    if _MODAL_URL_RE.search(u):
        return {
            "kind": "url",
            "detail": u[:120],
            "hint": "You left the form for a picker/interstitial. Back or close, then snapshot.",
        }
    if _MODAL_TITLE_RE.search(ttl):
        return {
            "kind": "title",
            "detail": ttl[:80],
            "hint": "A dialog is in front. Dismiss or complete it, then snapshot the form.",
        }
    hay = f"{ttl}\n{body}"[:800]
    if _MODAL_TITLE_RE.search(hay):
        return {
            "kind": "text",
            "detail": ttl[:80] or hay[:80],
            "hint": "A dialog is in front. Dismiss or complete it, then snapshot the form.",
        }
    for el in list(elements or [])[:40]:
        if not isinstance(el, dict):
            continue
        name = str(el.get("name") or el.get("text") or "").strip()
        ctx = str(el.get("context") or "")
        banner = bool(_BANNER_RE.search(ctx or "") or _BANNER_RE.search(name))
        if banner and (
            _MODAL_NAME_RE.match(name)
            or _norm(name) in ("continue", "open", "ok", "next")
        ):
            return {
                "kind": "control",
                "detail": f"{name} in {ctx[:60]}",
                "hint": (
                    f"{name!r} is an app/cookie banner, not the form. "
                    "Close/dismiss it (or ignore it) and snapshot the real composer."
                ),
            }
        role = str(el.get("role") or "").lower()
        if role in ("dialog", "alertdialog") and name:
            return {
                "kind": "dialog",
                "detail": name[:80],
                "hint": "A dialog role is in the snapshot. Handle it before the form.",
            }
    return None


def draft_still_on_page(typed: str, page_text: str) -> bool:
    """True when a distinctive slice of what we typed is still in page text."""
    blob = re.sub(r"\s+", " ", (typed or "")).strip()
    hay = (page_text or "")
    if len(blob) < 12 or not hay:
        return False
    # Mid-slice avoids the greeting the page already shows as a placeholder.
    start = min(12, max(0, len(blob) - 16))
    needle = blob[start : start + 28].strip()
    if len(needle) < 12:
        needle = blob[:28]
    return needle.lower() in hay.lower()


def find_best_elements(
    elements: list[dict[str, Any]],
    query: str,
    *,
    top_k: int = 8,
    min_score: float = 15.0,
) -> list[dict[str, Any]]:
    """Rank elements by query; attach match_score."""
    ranked: list[tuple[float, dict[str, Any]]] = []
    for el in elements or []:
        if not isinstance(el, dict):
            continue
        s = score_element(el, query)
        if s >= min_score:
            item = dict(el)
            item["match_score"] = round(s, 1)
            ranked.append((s, item))
    ranked.sort(key=lambda x: -x[0])
    return [e for _, e in ranked[: max(1, top_k)]]


def find_best_element(
    elements: list[dict[str, Any]],
    query: str,
    *,
    min_score: float = 20.0,
) -> dict[str, Any] | None:
    hits = find_best_elements(elements, query, top_k=1, min_score=min_score)
    return hits[0] if hits else None


def format_som_list(
    elements: list[dict[str, Any]],
    *,
    limit: int = 40,
    query: str = "",
) -> str:
    """Set-of-Mark style numbered list for the model (OSWorld / SoM practice).

    Compact, scannable: [e3] button "Sign in" @ (120,40)
    """
    els = list(elements or [])[: max(1, limit)]
    if query:
        ranked = find_best_elements(els, query, top_k=limit, min_score=5.0)
        if ranked:
            # Put matches first, then rest
            seen = {str(r.get("ref")) for r in ranked}
            rest = [e for e in els if str(e.get("ref")) not in seen]
            els = ranked + rest
            els = els[:limit]
    lines: list[str] = []
    for el in els:
        ref = str(el.get("ref") or "?")
        tag = str(el.get("tag") or el.get("role") or "el")
        name = str(el.get("name") or el.get("text") or el.get("placeholder") or "").strip()
        name = re.sub(r"\s+", " ", name)[:60]
        x, y = el.get("x"), el.get("y")
        score = el.get("match_score")
        extra = f" score={score}" if score is not None else ""
        # aria-pressed/selected/checked state — a store already chosen, an
        # active tab, a ticked box — so the model does not re-toggle it.
        state = str(el.get("state") or "").strip()
        if state == "true":
            extra += " [selected]"
        elif state == "false":
            extra += " [not-selected]"
        # Card context distinguishes N identical controls (which store's
        # "Set as store"). Only shown when it adds signal beyond the label.
        context = re.sub(r"\s+", " ", str(el.get("context") or "").strip())
        ctx_out = ""
        if context and _norm(context) != _norm(name):
            ctx_out = f' · in: "{context[:80]}"'
        lines.append(f'[{ref}] {tag} "{name}" @({x},{y}){extra}{ctx_out}')
    if not lines:
        return "(no interactive elements)"
    header = "Elements (Set-of-Mark — click with computer_click ref= or text=):\n"
    if query:
        header = f"Elements ranked for {query!r} (Set-of-Mark):\n"
    return header + "\n".join(lines)


def extract_typed_credentials(message: str) -> dict[str, str]:
    """Pull email/username hints from a user message for login flows."""
    out: dict[str, str] = {}
    msg = message or ""
    emails = re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        msg,
    )
    if emails:
        out["email"] = emails[0]
        out["username"] = emails[0]
    # "username X" / "user name: X"
    m = re.search(
        r"(?i)\b(?:user\s*name|username|login|email)\s*(?:is|=|:)?\s*([^\s,;]+)",
        msg,
    )
    if m and "@" in m.group(1):
        out["email"] = m.group(1).strip()
        out["username"] = m.group(1).strip()
    elif m and "username" not in out:
        out["username"] = m.group(1).strip().strip("\"'")
    return out
