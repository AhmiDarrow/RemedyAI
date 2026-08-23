"""Token usage + estimated API cost for desktop tickers and session totals.

Estimates are intentionally transparent. Real API usage wins whenever present.
DeepSeek (and similar) context-cache hits are priced separately when the
provider reports ``prompt_cache_hit_tokens`` / ``cached_tokens`` — otherwise
costs look ~5–10× high on agent loops that re-send the same system/history.
"""

from __future__ import annotations

import re
from typing import Any

# USD per 1M tokens — approximate public list prices.
# DeepSeek: (input_cache_miss, output, input_cache_hit) — hit defaults ~1/50 of miss.
# Update as catalogs change; ticker labels estimates as estimates.
_PriceRow = tuple[re.Pattern[str], float, float, float | None]

_PRICE_TABLE: list[_PriceRow] = [
    # xAI Grok (specific before generic)
    (re.compile(r"grok-4\.5|grok-4-5", re.I), 2.0, 6.0, None),
    (re.compile(r"grok-4\.3|grok-4(?!\.)", re.I), 1.25, 2.5, None),
    (re.compile(r"grok-3-mini|grok-2", re.I), 0.30, 0.50, None),
    (re.compile(r"grok-3(?!-mini)|grok-4", re.I), 3.0, 15.0, None),
    (re.compile(r"grok", re.I), 1.0, 3.0, None),
    # OpenAI
    (re.compile(r"gpt-4o-mini", re.I), 0.15, 0.60, None),
    (re.compile(r"gpt-4o|gpt-4\.1", re.I), 2.50, 10.0, None),
    (re.compile(r"o3-mini|o4-mini", re.I), 1.10, 4.40, None),
    (re.compile(r"o1|o3", re.I), 15.0, 60.0, None),
    # Anthropic
    (re.compile(r"claude-3-5-haiku|claude-3-haiku", re.I), 0.80, 4.0, None),
    (re.compile(r"claude-3-5-sonnet|claude-sonnet|claude-3-7", re.I), 3.0, 15.0, None),
    (re.compile(r"claude-3-opus|claude-opus", re.I), 15.0, 75.0, None),
    # DeepSeek V4 — official cache miss / output / cache hit (api-docs 2026-07)
    # Pro promo-tier rates match public docs table; list rates are higher off-promo.
    (
        re.compile(r"deepseek-v4-pro|deepseek-reasoner|deepseek-r1", re.I),
        0.435,
        0.87,
        0.003625,
    ),
    (re.compile(r"deepseek", re.I), 0.14, 0.28, 0.0028),
    # Google
    (re.compile(r"gemini-2\.5|gemini-2\.0|gemini-1\.5", re.I), 0.35, 1.05, None),
    # Groq / free-ish
    (re.compile(r"llama-3|mixtral|gemma", re.I), 0.05, 0.08, None),
    # Demo / local
    (re.compile(r"demo|ollama|local", re.I), 0.0, 0.0, 0.0),
]

_DEFAULT_IN = 1.0
_DEFAULT_OUT = 3.0


def estimate_tokens_text(text: str | None) -> int:
    """In-house class-weighted token estimate (TokenNanobot); char fallback."""
    if not text:
        return 0
    try:
        from remedy.nanoswarm.token_nanobot import estimate_text_tokens

        return estimate_text_tokens(text)
    except Exception:
        return max(0, (len(text) + 3) // 4)


def observe_provider_usage(
    estimated: int,
    actual: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Feed provider usage into TokenNanobot calibrator when available."""
    try:
        from remedy.nanoswarm.token_nanobot import get_token_nanobot

        get_token_nanobot().observe_usage(
            estimated, actual, provider=provider, model=model
        )
    except Exception:
        pass


def _endpoint_price(
    provider: str, model: str | None
) -> tuple[float, float, float | None] | None:
    """OpenRouter's ``/models`` quotes USD per token as strings; prefer it."""
    if provider != "openrouter":
        return None
    try:
        from remedy.interfaces.model_discovery import live_known_models

        row = live_known_models(provider).get((model or "").strip())
    except Exception:
        return None
    pricing = row.get("pricing") if isinstance(row, dict) else None
    if not isinstance(pricing, dict):
        return None
    try:
        raw_in = pricing.get("prompt")
        raw_out = pricing.get("completion")
        if raw_in is None or raw_out is None:
            return None
        pin = float(raw_in) * 1_000_000.0
        pout = float(raw_out) * 1_000_000.0
    except (TypeError, ValueError):
        return None
    if pin < 0 or pout < 0:
        return None
    return pin, pout, None


def price_per_mtok(
    model: str | None, provider: str | None = None
) -> tuple[float, float, float | None]:
    """Return (input_usd_per_1m, output_usd_per_1m, cache_hit_usd_per_1m|None)."""
    blob = f"{provider or ''} {model or ''}".strip()
    if not blob:
        return _DEFAULT_IN, _DEFAULT_OUT, None
    pl = (provider or "").lower()
    if pl in ("ollama", "demo"):
        return 0.0, 0.0, 0.0
    # Poe uses subscription points; rough mid-tier USD estimate for UI only.
    if pl == "poe" or "api.poe.com" in blob.lower():
        return 3.0, 15.0, None
    live = _endpoint_price(pl, model)
    if live is not None:
        return live
    for pat, pin, pout, phit in _PRICE_TABLE:
        if pat.search(blob):
            return pin, pout, phit
    return _DEFAULT_IN, _DEFAULT_OUT, None


def estimate_cost_usd(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str | None = None,
    provider: str | None = None,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int | None = None,
) -> float:
    """Estimate USD. When cache hit/miss split is known, apply cache rates.

    Without a hit count we bill *all* prompt tokens at the cache-miss rate
    (conservative upper bound — agent UIs used to look high vs DeepSeek console
    because loops re-send cached context).
    """
    pin, pout, phit = price_per_mtok(model, provider)
    pt = max(0, int(prompt_tokens or 0))
    ct = max(0, int(completion_tokens or 0))
    hit = max(0, int(cache_hit_tokens or 0))
    if cache_miss_tokens is not None:
        miss = max(0, int(cache_miss_tokens))
    elif hit > 0:
        # Prefer explicit miss; else remainder of prompt after hits.
        miss = max(0, pt - hit)
    else:
        miss = pt
        hit = 0
    # Cap hit to prompt size
    if hit > pt:
        hit = pt
        miss = 0
    if miss + hit > pt and pt > 0:
        # Normalize if provider over-reports
        miss = max(0, pt - hit)

    if phit is not None and hit > 0:
        input_cost = (miss * pin + hit * phit) / 1_000_000.0
    else:
        input_cost = (pt * pin) / 1_000_000.0
    return input_cost + (ct * pout) / 1_000_000.0


def _cost_from_usage_dict(d: dict[str, Any]) -> float:
    return estimate_cost_usd(
        prompt_tokens=int(d.get("prompt_tokens") or 0),
        completion_tokens=int(d.get("completion_tokens") or 0),
        model=str(d["model"]) if d.get("model") else None,
        provider=str(d["provider"]) if d.get("provider") else None,
        cache_hit_tokens=int(d.get("cache_hit_tokens") or 0),
        cache_miss_tokens=(
            int(d["cache_miss_tokens"])
            if d.get("cache_miss_tokens") is not None
            else None
        ),
    )


def merge_usage(*parts: dict[str, Any] | None) -> dict[str, Any]:
    """Sum usage dicts across distinct LLM rounds.

    When both sides are provider snapshots and the new one looks like a
    *stream update of the same call* (prompt stable, completion non-decreasing,
    total non-decreasing), take the later snapshot instead of summing — avoids
    multi-chunk usage double-counts.
    """
    prompt = 0
    completion = 0
    total = 0
    cache_hit = 0
    cache_miss: int | None = 0
    source = "estimate"
    model = None
    provider = None
    acc: dict[str, Any] | None = None

    for p in parts:
        if not p:
            continue
        if acc is None:
            acc = dict(p)
            continue
        # Stream re-report of same call?
        if (
            str(p.get("source") or "") == "provider"
            and str(acc.get("source") or "") == "provider"
            and _looks_like_same_call_update(acc, p)
        ):
            acc = _prefer_later_snapshot(acc, p)
            continue
        # Distinct rounds → sum
        acc = {
            "prompt_tokens": int(acc.get("prompt_tokens") or 0)
            + int(p.get("prompt_tokens") or 0),
            "completion_tokens": int(acc.get("completion_tokens") or 0)
            + int(p.get("completion_tokens") or 0),
            "total_tokens": int(acc.get("total_tokens") or 0)
            + int(p.get("total_tokens") or 0),
            "cache_hit_tokens": int(acc.get("cache_hit_tokens") or 0)
            + int(p.get("cache_hit_tokens") or 0),
            "cache_miss_tokens": (
                (
                    int(acc["cache_miss_tokens"])
                    if acc.get("cache_miss_tokens") is not None
                    else 0
                )
                + (
                    int(p["cache_miss_tokens"])
                    if p.get("cache_miss_tokens") is not None
                    else 0
                )
            ),
            "source": (
                "provider"
                if "provider"
                in (str(acc.get("source") or ""), str(p.get("source") or ""))
                else "estimate"
            ),
            "model": p.get("model") or acc.get("model"),
            "provider": p.get("provider") or acc.get("provider"),
        }

    if acc is None:
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_hit_tokens": 0,
            "cache_miss_tokens": None,
            "estimated_cost_usd": 0.0,
            "source": "estimate",
            "model": None,
            "provider": None,
        }

    prompt = int(acc.get("prompt_tokens") or 0)
    completion = int(acc.get("completion_tokens") or 0)
    total = int(acc.get("total_tokens") or 0)
    if total <= 0:
        total = prompt + completion
    cache_hit = int(acc.get("cache_hit_tokens") or 0)
    cache_miss = int(acc["cache_miss_tokens"]) if acc.get("cache_miss_tokens") is not None else None
    source = str(acc.get("source") or "estimate")
    model = acc.get("model")
    provider = acc.get("provider")
    cost = estimate_cost_usd(
        prompt_tokens=prompt,
        completion_tokens=completion,
        model=str(model) if model else None,
        provider=str(provider) if provider else None,
        cache_hit_tokens=cache_hit,
        cache_miss_tokens=cache_miss,
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
        "estimated_cost_usd": round(cost, 6),
        "source": source,
        "model": model,
        "provider": provider,
    }


def _looks_like_same_call_update(prev: dict[str, Any], nxt: dict[str, Any]) -> bool:
    """Heuristic: stream re-sent usage for the same completion."""
    pp = int(prev.get("prompt_tokens") or 0)
    np_ = int(nxt.get("prompt_tokens") or 0)
    pc = int(prev.get("completion_tokens") or 0)
    nc = int(nxt.get("completion_tokens") or 0)
    pt = int(prev.get("total_tokens") or 0)
    nt = int(nxt.get("total_tokens") or 0)
    if pp <= 0 or np_ <= 0:
        return False
    # Same (or nearly same) prompt size; completion/total only grow or stay.
    if abs(pp - np_) > max(32, int(0.02 * max(pp, np_))):
        return False
    return not (nc < pc and nt < pt)


def _prefer_later_snapshot(prev: dict[str, Any], nxt: dict[str, Any]) -> dict[str, Any]:
    out = dict(prev)
    for k in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "source",
        "model",
        "provider",
    ):
        if k in nxt and nxt[k] is not None:
            if k in ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens"):
                out[k] = max(int(prev.get(k) or 0), int(nxt.get(k) or 0))
            else:
                out[k] = nxt[k]
    return out


def _extract_cache_tokens(u: dict[str, Any], prompt: int) -> tuple[int, int | None]:
    """Return (cache_hit_tokens, cache_miss_tokens|None) from provider usage blob."""
    hit = 0
    miss: int | None = None

    # DeepSeek OpenAI-compat
    for key in (
        "prompt_cache_hit_tokens",
        "cache_hit_tokens",
        "cached_tokens",
    ):
        if u.get(key) is not None:
            hit = max(hit, int(u.get(key) or 0))
    for key in (
        "prompt_cache_miss_tokens",
        "cache_miss_tokens",
    ):
        if u.get(key) is not None:
            miss = int(u.get(key) or 0)

    # OpenAI-style nested details
    details = u.get("prompt_tokens_details") or u.get("input_tokens_details")
    if isinstance(details, dict):
        if details.get("cached_tokens") is not None:
            hit = max(hit, int(details.get("cached_tokens") or 0))
        if details.get("cache_write_tokens") is not None and miss is None:
            # not miss, but leave miss as remainder
            pass

    if hit > 0 and miss is None and prompt > 0:
        miss = max(0, prompt - hit)
    return hit, miss


def usage_from_provider_payload(
    data: dict[str, Any] | None,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """Extract OpenAI/Anthropic/DeepSeek-style usage blobs."""
    if not isinstance(data, dict):
        return None
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    prompt = u.get("prompt_tokens") or u.get("input_tokens") or 0
    completion = u.get("completion_tokens") or u.get("output_tokens") or 0
    total = u.get("total_tokens") or (int(prompt) + int(completion))
    if int(prompt) + int(completion) + int(total) <= 0:
        return None
    hit, miss = _extract_cache_tokens(u, int(prompt))
    return merge_usage(
        {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
            "cache_hit_tokens": hit,
            "cache_miss_tokens": miss,
            "source": "provider",
            "model": model,
            "provider": provider,
        }
    )


def estimate_turn_usage(
    *,
    user_text: str = "",
    assistant_text: str = "",
    thinking_text: str = "",
    model: str | None = None,
    provider: str | None = None,
    system_overhead: int = 800,
) -> dict[str, Any]:
    """Heuristic usage when the API did not report tokens."""
    prompt = system_overhead + estimate_tokens_text(user_text)
    completion = estimate_tokens_text(assistant_text) + estimate_tokens_text(thinking_text)
    return merge_usage(
        {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "source": "estimate",
            "model": model,
            "provider": provider,
        }
    )


def format_cost(usd: float) -> str:
    if usd <= 0:
        return "$0.00"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"
