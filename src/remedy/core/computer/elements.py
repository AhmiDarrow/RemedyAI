"""Match interactive elements by text/name for click-by-text autonomy."""

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
    ]
    return _norm(" ".join(parts))


def score_element(el: dict[str, Any], query: str) -> float:
    """Higher is better. Exact name match >> contains >> fuzzy tokens."""
    q = _norm(query)
    if not q:
        return 0.0
    name = _norm(str(el.get("name") or ""))
    blob = element_search_blob(el)
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
    # Prefer real controls over huge containers
    w = float(el.get("w") or 0)
    h = float(el.get("h") or 0)
    if 8 <= w <= 900 and 8 <= h <= 200:
        score += 5.0
    if w * h > 400_000:
        score -= 20.0
    tag = str(el.get("tag") or "").lower()
    role = str(el.get("role") or "").lower()
    if tag in ("button", "a", "input", "summary") or role in (
        "button",
        "link",
        "tab",
        "menuitem",
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
