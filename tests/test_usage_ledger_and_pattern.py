"""Multiprovider usage ledger + session-scoped pattern isolation."""

from __future__ import annotations

from pathlib import Path

from remedy.core.usage_ledger import record_usage_event, series, summary
from remedy.nanoswarm.pattern_nanobot import PatternNanobot
from remedy.nanoswarm.token_nanobot import (
    encoding_family,
    estimate_text_tokens,
    get_token_nanobot,
    resolve_context_window,
)


def test_pattern_sessions_are_isolated():
    bot = PatternNanobot()
    bot.on_tool_step("bash_exec", success=False, session_id="sess-a")
    bot.on_tool_step("bash_exec", success=False, session_id="sess-a")
    bot.on_tool_step("file_read", success=True, session_id="sess-b")
    a = bot.for_session("sess-a").snapshot()
    b = bot.for_session("sess-b").snapshot()
    assert a["step_count"] == 2
    assert b["step_count"] == 1
    assert a["success_rate"] == 0.0
    assert b["success_rate"] == 1.0


def test_token_remeasure_on_provider_change():
    bot = get_token_nanobot()
    msgs = [{"role": "user", "content": "hello " * 50}]
    out = bot.on_provider_changed(
        "openai",
        "gpt-4o-mini",
        session_id="s1",
        messages=msgs,
        old_provider="xai",
        old_model="grok-4",
    )
    assert out["provider"] == "openai"
    assert out["remeasured"] is True
    assert out["token_estimate"] > 0
    assert out["context_window"] > 0
    assert encoding_family("anthropic", "claude-3") == "anthropic"
    assert resolve_context_window("anthropic", "claude-3-5-sonnet") >= 100_000
    assert estimate_text_tokens("abc") > 0


def test_pack_nanobot_aggressive_when_full():
    from remedy.nanoswarm.pack_nanobot import PackNanobot

    pack = PackNanobot()
    out = pack.pack_for_turn(
        messages=[{"role": "tool", "content": "x"} for _ in range(8)],
        fill_pct=0.9,
        pattern_recent=["file_read", "bash_exec"],
        intent="tool",
    )
    assert out["aggressive"] is True
    assert out["keep_recent_tool_pairs"] <= 3
    assert "Pack" in (out.get("system_hint") or "")


def test_usage_ledger_summary(tmp_path: Path):
    home = tmp_path / "remedy-home"
    home.mkdir()
    record_usage_event(
        session_id="s1",
        provider="xai",
        model="grok-4.5",
        prompt_tokens=100,
        completion_tokens=50,
        source="provider",
        home=home,
    )
    record_usage_event(
        session_id="s1",
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=40,
        completion_tokens=10,
        source="provider",
        home=home,
    )
    s = summary(range_days=7, home=home)
    assert s["totals"]["total_tokens"] == 200
    assert len(s["by_provider"]) == 2
    ser = series(range_days=7, group="provider", home=home)
    assert "points" in ser
