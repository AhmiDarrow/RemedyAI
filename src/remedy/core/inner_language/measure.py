"""Token estimates using the same estimator Remedy routes with."""

from __future__ import annotations

from typing import Any

FAMILIES = ("cl100k", "anthropic", "local")


def measure_tokens(text: str, *, prefer_bpe: bool = True) -> dict[str, int]:
    from remedy.nanoswarm.token_nanobot import estimate_text_tokens

    out: dict[str, int] = {}
    route = {"cl100k": "openai-compat", "anthropic": "anthropic-like", "local": "local"}
    for fam in FAMILIES:
        try:
            out[fam] = int(
                estimate_text_tokens(
                    text or "", family=route[fam], prefer_bpe=prefer_bpe
                ) or 0
            )
        except Exception:
            out[fam] = max(1, (len(text or "") + 3) // 4)
    return out


def mean_tokens(text: str, **kwargs: Any) -> float:
    vals = list(measure_tokens(text, **kwargs).values()) or [0]
    return sum(vals) / len(vals)
