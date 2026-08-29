"""Goal + URL + vault → drive steps. The model does not invent this JSON.

``life_drive(recipe=shop, url=…, query=milk, vault=card-visa)`` (or a short
goal like "buy milk on instacart") expands here. Pay / password / send stay
checkpoints — they are listed so the owner sees the stop, never auto-run.
"""

from __future__ import annotations

import re
from typing import Any

from remedy.core.build_oracle import coerce_text_arg

_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
_ON_SITE_RE = re.compile(
    r"(?is)\s+(?:on|from|at)\s+(?P<site>[a-z0-9][a-z0-9 .'-]{1,40})\s*$"
)
_BUY_RE = re.compile(
    r"(?is)\b(?:buy|order|get|shop(?:\s+for)?|add)\s+"
    r"(?:me\s+|some\s+|a\s+|an\s+|the\s+)?"
    r"(?P<item>.+)"
)
_SEARCH_RE = re.compile(
    r"(?is)\b(?:search(?:\s+for)?|look\s+up|find|google)\s+"
    r"(?:for\s+)?(?P<q>.+)"
)
_VAULT_TOKEN_RE = re.compile(r"\{\{\s*vault:([a-zA-Z0-9._-]+)\s*\}\}")
_HANDLE_RE = re.compile(r"^[a-zA-Z0-9._-]{2,40}$")

_RECIPES = frozenset({"open", "search", "shop", "fill", "sign_in"})

# Vault kind → visible field label the fill hand looks up.
_KIND_LABEL = {
    "card": "Card number",
    "password": "Password",
    "identity": "Email",
    "address": "Address",
    "note": "Notes",
}


def infer_recipe(goal: str) -> str:
    g = coerce_text_arg(goal).lower()
    if re.search(r"\b(sign\s*in|log\s*in|login|password)\b", g):
        return "sign_in"
    if re.search(r"\b(fill|form|checkout)\b", g) and not re.search(
        r"\b(buy|order|shop|purchase)\b", g
    ):
        return "fill"
    if re.search(r"\b(buy|order|shop|purchase|cart|grocer)\b", g):
        return "shop"
    if re.search(r"\b(search|look\s+up|find|google)\b", g):
        return "search"
    return "open"


def _site_url(name: str) -> str:
    raw = coerce_text_arg(name)
    if not raw:
        return ""
    m = _URL_RE.search(raw)
    if m:
        return m.group(0).rstrip(".,)")
    try:
        from remedy.core.computer.browse_intent import resolve_site_alias

        hit = resolve_site_alias(raw)
        if hit:
            return hit
    except Exception:
        pass
    return ""


def _query_from_goal(goal: str, recipe: str) -> str:
    g = coerce_text_arg(goal)
    if not g:
        return ""
    rest = _ON_SITE_RE.sub("", g).strip()
    if recipe == "shop":
        m = _BUY_RE.search(rest)
        if m:
            return coerce_text_arg(m.group("item")).strip(" .")
    if recipe == "search":
        m = _SEARCH_RE.search(rest)
        if m:
            return coerce_text_arg(m.group("q")).strip(" .")
    return ""


def _vault_handles(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        parts = [coerce_text_arg(x) for x in raw]
    else:
        text = coerce_text_arg(raw)
        tokens = _VAULT_TOKEN_RE.findall(text)
        if tokens:
            return [t.strip().lower() for t in tokens if t.strip()]
        parts = re.split(r"[\s,;]+", text)
    out: list[str] = []
    for p in parts:
        h = p.strip().lower().removeprefix("vault:")
        if h.startswith("{{"):
            m = _VAULT_TOKEN_RE.search(h)
            h = m.group(1).lower() if m else ""
        if h and _HANDLE_RE.match(h) and h not in out:
            out.append(h)
    return out[:12]


def _vault_fields(handles: list[str], *, home: Any = None) -> list[dict[str, str]]:
    meta: dict[str, dict[str, Any]] = {}
    if handles:
        try:
            from remedy.core import vault

            for it in vault.vault_list(home) or []:
                if isinstance(it, dict) and it.get("handle"):
                    meta[str(it["handle"]).lower()] = it
        except Exception:
            meta = {}
    fields: list[dict[str, str]] = []
    for h in handles:
        info = meta.get(h) or {}
        kind = str(info.get("kind") or "").lower()
        label = str(info.get("label") or "") or _KIND_LABEL.get(kind) or h
        if kind in _KIND_LABEL and not str(info.get("label") or "").strip():
            label = _KIND_LABEL[kind]
        elif kind in _KIND_LABEL:
            # Prefer a human field name over the owner's private label.
            label = _KIND_LABEL[kind]
        fields.append({"text": label, "value": "{{vault:" + h + "}}"})
    return fields


def expand_recipe(
    *,
    goal: str = "",
    recipe: str = "",
    url: str = "",
    query: str = "",
    vault: Any = "",
    home: Any = None,
) -> list[dict[str, Any]]:
    """Build a drive plan from a recipe + URL + vault handles. Never empty-guess."""
    g = coerce_text_arg(goal)
    rec = coerce_text_arg(recipe).lower().replace("-", "_").replace(" ", "_")
    if rec not in _RECIPES:
        rec = infer_recipe(g)
    site = coerce_text_arg(url) or ""
    if not site and g:
        on = _ON_SITE_RE.search(g)
        if on:
            site = _site_url(on.group("site")) or site
        if not site:
            site = _site_url(g)
    q = coerce_text_arg(query) or _query_from_goal(g, rec)
    handles = _vault_handles(vault)
    if not handles and g:
        handles = _vault_handles(g)

    steps: list[dict[str, Any]] = []
    if rec in {"open", "search", "shop", "sign_in", "fill"} and site:
        steps.append(
            {
                "title": f"Open {site}",
                "action": "navigate",
                "url": site,
            }
        )
    elif rec == "open" and not site:
        return []

    if rec == "open":
        steps.append({"title": "Check the page", "action": "snapshot"})
        return steps

    if rec == "search":
        if q:
            steps.append(
                {
                    "title": f"Search for {q}",
                    "action": "type",
                    "text": q,
                    "query": "Search",
                }
            )
            steps.append(
                {
                    "title": "Submit search",
                    "action": "key",
                    "key": "enter",
                }
            )
        steps.append({"title": "Check the results", "action": "snapshot"})
        return steps

    if rec == "shop":
        if q:
            steps.append(
                {
                    "title": f"Search for {q}",
                    "action": "type",
                    "text": q,
                    "query": "Search",
                }
            )
            steps.append(
                {
                    "title": "Submit search",
                    "action": "key",
                    "key": "enter",
                }
            )
        steps.append({"title": "See what is on screen", "action": "snapshot"})
        if handles:
            steps.append(
                {
                    "title": "Fill stored payment details",
                    "action": "fill",
                    "fields": _vault_fields(handles, home=home),
                }
            )
        steps.append(
            {
                "title": "Place order",
                "action": "click",
                "text": "Place order",
                "checkpoint": True,
            }
        )
        return steps

    if rec == "fill":
        if not steps:
            steps.append({"title": "Read the form", "action": "snapshot"})
        if handles:
            steps.append(
                {
                    "title": "Fill stored details",
                    "action": "fill",
                    "fields": _vault_fields(handles, home=home),
                }
            )
        else:
            steps.append({"title": "Read the form", "action": "snapshot"})
        steps.append(
            {
                "title": "Submit",
                "action": "click",
                "text": "Submit",
                "checkpoint": True,
            }
        )
        return steps

    if rec == "sign_in":
        if handles:
            steps.append(
                {
                    "title": "Fill stored sign-in details",
                    "action": "fill",
                    "fields": _vault_fields(handles, home=home),
                }
            )
        else:
            steps.append({"title": "Find the sign-in fields", "action": "snapshot"})
        steps.append(
            {
                "title": "Sign in",
                "action": "click",
                "text": "Sign in",
                "kind": "password",
                "checkpoint": True,
            }
        )
        return steps

    return steps


def recipe_names() -> tuple[str, ...]:
    return tuple(sorted(_RECIPES))
