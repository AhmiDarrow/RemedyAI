"""Unit tests for remedy.core.usage (token/cost estimates)."""

from __future__ import annotations

from remedy.core.usage import (
    estimate_tokens_text,
    estimate_turn_usage,
    format_cost,
    merge_usage,
    price_per_mtok,
    usage_from_provider_payload,
)


def test_zero_text_tokens():
    assert estimate_tokens_text("") == 0
    assert estimate_tokens_text(None) == 0  # type: ignore[arg-type]


def test_price_table_hits():
    pin, pout, _hit = price_per_mtok("gpt-4o-mini", "openai")
    assert pin < pout
    pin2, pout2, _ = price_per_mtok("unknown-model-xyz", "custom")
    assert pin2 >= 0 and pout2 >= 0
    assert price_per_mtok("llama3", "ollama") == (0.0, 0.0, 0.0)


def test_deepseek_cache_aware_cost():
    from remedy.core.usage import estimate_cost_usd

    # All cache miss ≈ list rate
    miss = estimate_cost_usd(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        model="deepseek-v4-flash",
        provider="deepseek",
    )
    assert abs(miss - 0.14) < 0.001
    # 90% cache hit should be far cheaper
    mixed = estimate_cost_usd(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        model="deepseek-v4-flash",
        provider="deepseek",
        cache_hit_tokens=900_000,
        cache_miss_tokens=100_000,
    )
    assert mixed < miss * 0.2
    assert mixed > 0


def test_deepseek_usage_payload_cache_fields():
    u = usage_from_provider_payload(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 10,
                "total_tokens": 1010,
                "prompt_cache_hit_tokens": 800,
                "prompt_cache_miss_tokens": 200,
            }
        },
        model="deepseek-v4-flash",
        provider="deepseek",
    )
    assert u is not None
    assert u["cache_hit_tokens"] == 800
    assert u["cache_miss_tokens"] == 200
    # Cheaper than full miss on 1000
    full_miss = usage_from_provider_payload(
        {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 10,
                "total_tokens": 1010,
            }
        },
        model="deepseek-v4-flash",
        provider="deepseek",
    )
    assert full_miss is not None
    assert u["estimated_cost_usd"] < full_miss["estimated_cost_usd"]


def test_merge_stream_updates_not_double():
    a = {
        "prompt_tokens": 1000,
        "completion_tokens": 10,
        "total_tokens": 1010,
        "source": "provider",
    }
    b = {
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "total_tokens": 1050,
        "source": "provider",
    }
    m = merge_usage(a, b)
    # Same-call stream update → take later, not sum
    assert m["prompt_tokens"] == 1000
    assert m["completion_tokens"] == 50


def test_format_cost_bands():
    assert format_cost(0) == "$0.00"
    assert format_cost(0.0001).startswith("$")
    assert format_cost(0.5).startswith("$")
    assert format_cost(2.5).startswith("$")


def test_estimate_turn_usage_structure():
    u = estimate_turn_usage(
        user_text="hello world " * 20,
        assistant_text="answer " * 30,
        thinking_text="think " * 10,
        model="grok-3-mini",
        provider="xai",
    )
    assert u["prompt_tokens"] > 0
    assert u["completion_tokens"] > 0
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]
    assert "estimated_cost_usd" in u
    assert u["source"] == "estimate"


def test_usage_payload_anthropic_style():
    u = usage_from_provider_payload(
        {"usage": {"input_tokens": 40, "output_tokens": 10}},
        model="claude-3-5-sonnet",
        provider="anthropic",
    )
    assert u is not None
    assert u["prompt_tokens"] == 40
    assert u["completion_tokens"] == 10


def test_usage_payload_missing():
    assert usage_from_provider_payload({}) is None
    assert usage_from_provider_payload({"usage": {}}) is None


def test_merge_sums():
    a = merge_usage({"prompt_tokens": 10, "completion_tokens": 5, "source": "estimate"})
    b = merge_usage(a, {"prompt_tokens": 3, "completion_tokens": 2, "source": "provider"})
    assert b["prompt_tokens"] == 13
    assert b["completion_tokens"] == 7
    assert b["source"] == "provider"
