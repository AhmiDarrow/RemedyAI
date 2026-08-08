"""Muscle profile — capable provider sensing for builder agency."""

from __future__ import annotations

from remedy.core.muscle_profile import (
    TIER_FRONTIER,
    TIER_MID,
    TIER_TINY,
    builder_system_addendum,
    classify_muscle,
)


def test_grok_is_frontier():
    p = classify_muscle("xai", "grok-4")
    assert p.tier == TIER_FRONTIER
    assert p.builder_contract
    assert p.max_parallel_tools >= 20
    assert "build" in builder_system_addendum(p).lower()


def test_claude_sonnet_frontier():
    p = classify_muscle("anthropic", "claude-sonnet-4")
    assert p.is_frontier
    assert p.prefer_spread


def test_openai_default_frontier():
    p = classify_muscle("openai", "")
    assert p.tier == TIER_FRONTIER


def test_tiny_local_lean():
    p = classify_muscle("ollama", "qwen2.5-1.5b")
    assert p.tier <= TIER_TINY + 1
    assert not p.builder_contract
    assert builder_system_addendum(p) == ""
    assert p.max_parallel_tools <= 8


def test_mid_flash():
    p = classify_muscle("google", "gemini-flash")
    assert p.tier == TIER_MID
    assert p.is_capable
