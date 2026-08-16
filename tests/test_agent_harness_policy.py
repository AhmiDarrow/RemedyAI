"""Local harness vs frontier “let them be smart” policy."""

from __future__ import annotations

from remedy.core.local_agent_optimize import (
    is_frontier_binding,
    is_local_binding,
    needs_agent_harness,
)


def test_rmb_needs_harness():
    assert needs_agent_harness("rmb", "qwen", "http://127.0.0.1:8787/v1") is True
    assert is_local_binding("rmb", None, "http://127.0.0.1:8787/v1") is True
    assert is_frontier_binding("rmb", None, "http://127.0.0.1:8787/v1") is False


def test_grok_xai_is_frontier():
    assert needs_agent_harness("xai", "grok-4", "https://api.x.ai/v1") is False
    assert is_frontier_binding("xai", "grok-4", "https://api.x.ai/v1") is True
    assert is_frontier_binding("openai", "gpt-4o", "https://api.openai.com/v1") is True
    assert is_frontier_binding(
        "anthropic", "claude-sonnet-4", "https://api.anthropic.com"
    ) is True


def test_ollama_needs_harness():
    assert needs_agent_harness("ollama", "qwen2.5-coder", "http://127.0.0.1:11434") is True
