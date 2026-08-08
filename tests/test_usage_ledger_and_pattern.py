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
    assert encoding_family("anthropic", "claude-3") == "anthropic-like"
    assert resolve_context_window("anthropic", "claude-3-5-sonnet") >= 100_000
    assert estimate_text_tokens("abc") > 0


def test_local_models_get_conservative_window():
    """Small local models must never be budgeted like a 128k cloud endpoint."""
    from remedy.nanoswarm.token_nanobot import (
        cache_context_window,
        clear_context_window_cache,
        is_local_model,
    )

    clear_context_window_cache()
    # The old bug: a local deepseek-r1:7b hit the 'deepseek' cloud rule → 128k.
    assert resolve_context_window("ollama", "deepseek-r1:7b") == 16_384
    assert resolve_context_window("ollama", "llama3.2") == 8_192
    assert resolve_context_window("ollama", "qwen2.5:3b") == 8_192
    assert resolve_context_window("ollama", "qwen2.5:1b") == 4_096
    # A llama.cpp server behind a custom URL must also get the tight budget.
    assert resolve_context_window("custom", "qwen2.5:7b") == 16_384
    # RMB lattices (underscore size + .rmb4) — not 128k.
    assert is_local_model(
        "custom", "qwen25_coder_7b.rmb4", base_url="http://127.0.0.1:8787/v1"
    )
    rmb_win = resolve_context_window(
        "custom",
        "qwen25_coder_7b.rmb4",
        base_url="http://127.0.0.1:8787/v1",
    )
    assert 4_096 <= rmb_win <= 16_384
    # Live discovery from GET /v1/models overrides heuristics.
    cache_context_window("http://127.0.0.1:8787/v1", "qwen25_coder_7b.rmb4", 6144)
    assert (
        resolve_context_window(
            "custom",
            "qwen25_coder_7b.rmb4",
            base_url="http://127.0.0.1:8787/v1",
        )
        == 6144
    )
    clear_context_window_cache()


def test_cloud_models_keep_big_window():
    assert resolve_context_window("openai", "gpt-4o") == 128_000
    assert resolve_context_window("anthropic", "claude-3-5-sonnet") >= 100_000
    # Cloud serving a local-family model name stays large (e.g. Groq llama 70b).
    assert resolve_context_window("groq", "llama-3.3-70b-versatile") == 128_000


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


def test_guard_nanobot_scores_destructive_shell():
    from remedy.nanoswarm.guard_nanobot import GuardNanobot

    g = GuardNanobot()
    low = g.assess(tool_name="file_read", command="README.md")
    high = g.assess(tool_name="bash_exec", command="rm -rf / && shutdown -h now")
    assert high["score"] > low["score"]
    assert high["level"] in ("high", "critical")
    reason = g.enrich_ask_reason(
        "Shell execution requires approval",
        tool_name="bash_exec",
        command="git reset --hard",
    )
    assert reason and "Guard" in reason


def test_helper_offline_help_and_error():
    from remedy.nanoswarm.helper_nanobot import HelperNanobot

    h = HelperNanobot()
    help_out = h.draft_help("provider switch")
    assert help_out["ok"] is True
    assert "markdown" in help_out
    err = h.explain_error("Error 401 Unauthorized from API")
    assert err["ok"] is True
    assert any("key" in x.lower() or "auth" in x.lower() for x in err["hints"])


def test_goal_scout_health_nanobots(tmp_path: Path):
    from remedy.nanoswarm.goal_nanobot import GoalNanobot
    from remedy.nanoswarm.health_nanobot import HealthNanobot
    from remedy.nanoswarm.scout_nanobot import ScoutNanobot
    from remedy.nanoswarm.token_tables import list_families, resolved_weights

    g = GoalNanobot()

    class _Brief:
        open_tasks = ["Ship provider switch", "Write tests"]

    g.sync_from_brief(_Brief(), session_id="s1")
    for _ in range(8):
        g.on_tool_step("list_dir", success=True, session_id="s1")
    snap = g.snapshot("s1")
    assert snap["stale"] is True
    assert "Ship" in g.system_hint("s1") or "goal" in g.system_hint("s1").lower()

    scout = ScoutNanobot()
    out = scout.scout("debug the pytest failure", intent="tool")
    assert out["active"] is True
    assert out["suggest_tools"]
    # warm project (cheap list_dir)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='t'\n", encoding="utf-8")
    warm = scout.warm_project(str(tmp_path), user_text="run tests")
    assert warm.get("ok") is True
    assert "pyproject.toml" in " ".join(warm.get("top_entries") or [])
    assert "python" in (warm.get("markers") or [])
    scout2 = scout.scout("run pytest", intent="tool", project_path=str(tmp_path))
    assert scout2.get("warm") is True

    health = HealthNanobot()
    health.report(provider="xai", model="grok", ok=False, error="429 rate limit")
    health.report(provider="xai", model="grok", ok=False, error="429 rate limit")
    h = health.snapshot(provider="xai", model="grok")
    assert h["rate_limit_hits"] >= 2
    assert h["flaky"] is True
    fo = health.failover_suggestion(
        provider="xai",
        model="grok",
        connected_providers=["xai", "ollama", "demo"],
    )
    assert fo["suggest_switch"] is True
    assert fo["suggested_provider"] in ("ollama", "demo")

    assert any(f["id"] == "cl100k" for f in list_families())
    w = resolved_weights("anthropic")
    assert "ascii_word" in w and w["ascii_word"] > 0


def test_usage_ledger_summary(tmp_path: Path):
    from remedy.core.usage_ledger import close_conn

    home = tmp_path / "remedy-home"
    home.mkdir()
    close_conn()
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
    close_conn()


def test_usage_ledger_delete_session_and_cascade(tmp_path: Path):
    """Session delete cascade must drop usage.db rows for that session only."""
    from remedy.core.session_reset import purge_session_disk_artifacts
    from remedy.core.usage_ledger import close_conn, delete_session_events, session_usage

    home = tmp_path / "remedy-home"
    home.mkdir()
    close_conn()
    record_usage_event(
        session_id="drop-me",
        provider="xai",
        model="grok",
        prompt_tokens=50,
        completion_tokens=10,
        home=home,
    )
    record_usage_event(
        session_id="keep-me",
        provider="openai",
        model="gpt",
        prompt_tokens=20,
        completion_tokens=5,
        home=home,
    )
    n = delete_session_events("drop-me", home=home)
    assert n == 1
    left = session_usage("drop-me", home=home)
    assert left["totals"]["events"] == 0
    kept = session_usage("keep-me", home=home)
    assert kept["totals"]["events"] == 1
    assert kept["totals"]["total_tokens"] == 25

    # Re-seed and purge via session cascade
    record_usage_event(
        session_id="cascade-sid",
        provider="xai",
        model="g",
        prompt_tokens=1,
        completion_tokens=1,
        home=home,
    )
    stats = purge_session_disk_artifacts("cascade-sid", home)
    assert stats.get("usage_events_deleted", 0) >= 1
    assert session_usage("cascade-sid", home=home)["totals"]["events"] == 0
    close_conn()
