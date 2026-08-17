"""Match interactive elements by text/name for click-by-text autonomy.

Inspired by research on GUI agents (OSWorld, ScreenSpot, Set-of-Mark):
prefer structured a11y/DOM labels + numbered marks over raw pixels.
"""

from __future__ import annotations

import re
from typing import Any


def _norm(s: str) -> str:
    t = (s or "").lower().strip()
    t = re.sub(r"\s+", " ", t)
    return t


def element_search_blob(el: dict[str, Any]) -> str:
    parts = [
        str(el.get("name") or ""),
        str(el.get("text") or ""),
        str(el.get("value") or ""),
        str(el.get("placeholder") or ""),
        str(el.get("href") or ""),
        str(el.get("aria") or ""),
        str(el.get("tag") or ""),
        str(el.get("role") or ""),
        str(el.get("title") or ""),
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
    blob = element_search_blob(el)
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or "").lower()
    itype = str(el.get("type") or el.get("input_type") or "").lower()
    score = 0.0
    if name == q:
        score += 100.0
    elif q in name:
        score += 70.0
    elif name and name in q:
        score += 40.0
    if q in blob:
        score += 25.0
    # token overlap
    q_toks = set(re.findall(r"[a-z0-9]{2,}", q))
    b_toks = set(re.findall(r"[a-z0-9]{2,}", blob))
    if q_toks and b_toks:
        inter = q_toks & b_toks
        score += 15.0 * len(inter) / max(1, len(q_toks))
    # Card-context disambiguation: query tokens the LABEL does not cover but
    # the enclosing card does (store name / address) break ties between N
    # identical controls — the whole point of the context field.
    context = _norm(str(el.get("context") or ""))
    if context and len(q_toks) > 1:
        name_toks = set(re.findall(r"[a-z0-9]{2,}", name))
        ctx_toks = set(re.findall(r"[a-z0-9]{2,}", context))
        missing = q_toks - name_toks
        in_ctx = missing & ctx_toks
        if in_ctx:
            score += 22.0 * len(in_ctx) / max(1, len(q_toks))
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
