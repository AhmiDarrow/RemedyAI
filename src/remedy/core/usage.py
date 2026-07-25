"""Token usage + estimated API cost for desktop tickers and session totals.

Estimates are intentionally transparent and conservative when the provider
does not return usage. Real API usage wins whenever present.
"""

from __future__ import annotations

import re
from typing import Any

# USD per 1M tokens — approximate public list prices (input, output).
# Update as catalogs change; ticker labels estimates as estimates.
_PRICE_TABLE: list[tuple[re.Pattern[str], float, float]] = [
    # xAI Grok
    (re.compile(r"grok-4|grok-3(?!-mini)", re.I), 3.0, 15.0),
    (re.compile(r"grok-3-mini|grok-2", re.I), 0.30, 0.50),
    # OpenAI
    (re.compile(r"gpt-4o-mini", re.I), 0.15, 0.60),
    (re.compile(r"gpt-4o|gpt-4\.1", re.I), 2.50, 10.0),
    (re.compile(r"o3-mini|o4-mini", re.I), 1.10, 4.40),
    (re.compile(r"o1|o3", re.I), 15.0, 60.0),
    # Anthropic
    (re.compile(r"claude-3-5-haiku|claude-3-haiku", re.I), 0.80, 4.0),
    (re.compile(r"claude-3-5-sonnet|claude-sonnet|claude-3-7", re.I), 3.0, 15.0),
    (re.compile(r"claude-3-opus|claude-opus", re.I), 15.0, 75.0),
    # DeepSeek
    (re.compile(r"deepseek-reasoner|deepseek-r1", re.I), 0.55, 2.19),
    (re.compile(r"deepseek", re.I), 0.14, 0.28),
    # Google
    (re.compile(r"gemini-2\.5|gemini-2\.0|gemini-1\.5", re.I), 0.35, 1.05),
    # Groq / free-ish
    (re.compile(r"llama-3|mixtral|gemma", re.I), 0.05, 0.08),
    # Demo / local
    (re.compile(r"demo|ollama|local", re.I), 0.0, 0.0),
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


def price_per_mtok(model: str | None, provider: str | None = None) -> tuple[float, float]:
    """Return (input_usd_per_1m, output_usd_per_1m) for model/provider."""
    blob = f"{provider or ''} {model or ''}".strip()
    if not blob:
        return _DEFAULT_IN, _DEFAULT_OUT
    # Local / demo free
    pl = (provider or "").lower()
    if pl in ("ollama", "demo", "custom") and "http" not in blob.lower():
        if pl in ("ollama", "demo"):
            return 0.0, 0.0
    for pat, pin, pout in _PRICE_TABLE:
        if pat.search(blob):
            return pin, pout
    return _DEFAULT_IN, _DEFAULT_OUT


def estimate_cost_usd(
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str | None = None,
    provider: str | None = None,
) -> float:
    pin, pout = price_per_mtok(model, provider)
    return (max(0, prompt_tokens) * pin + max(0, completion_tokens) * pout) / 1_000_000.0


def merge_usage(*parts: dict[str, Any] | None) -> dict[str, Any]:
    """Sum usage dicts; prefer provider-reported totals when present."""
    prompt = 0
    completion = 0
    total = 0
    source = "estimate"
    model = None
    provider = None
    for p in parts:
        if not p:
            continue
        prompt += int(p.get("prompt_tokens") or 0)
        completion += int(p.get("completion_tokens") or 0)
        total += int(p.get("total_tokens") or 0)
        if p.get("source") == "provider":
            source = "provider"
        model = p.get("model") or model
        provider = p.get("provider") or provider
    if total <= 0:
        total = prompt + completion
    cost = estimate_cost_usd(
        prompt_tokens=prompt,
        completion_tokens=completion,
        model=str(model) if model else None,
        provider=str(provider) if provider else None,
    )
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimated_cost_usd": round(cost, 6),
        "source": source,
        "model": model,
        "provider": provider,
    }


def usage_from_provider_payload(
    data: dict[str, Any] | None,
    *,
    model: str | None = None,
    provider: str | None = None,
) -> dict[str, Any] | None:
    """Extract OpenAI/Anthropic-style usage blobs."""
    if not isinstance(data, dict):
        return None
    u = data.get("usage")
    if not isinstance(u, dict):
        return None
    # OpenAI
    prompt = u.get("prompt_tokens") or u.get("input_tokens") or 0
    completion = u.get("completion_tokens") or u.get("output_tokens") or 0
    total = u.get("total_tokens") or (int(prompt) + int(completion))
    if int(prompt) + int(completion) + int(total) <= 0:
        return None
    return merge_usage(
        {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(total),
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
