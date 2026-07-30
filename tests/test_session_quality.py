"""Session quality baselines + compress fact retention."""

from __future__ import annotations

from remedy.core.session_quality import (
    SessionQuality,
    get_session_quality,
    reset_session_quality,
)
from remedy.memory.harness.brief import SessionBrief
from remedy.memory.harness.quality import review_compress_quality


def test_re_explain_and_stuck_rates():
    reset_session_quality("t1")
    q = get_session_quality("t1")
    q.record_turn(user_text="hello")
    q.record_turn(user_text="I already told you to use TypeScript")
    q.record_turn(user_text="still stuck on the same error again")
    snap = q.snapshot()
    assert snap["turns"] == 3
    assert snap["re_explain_count"] >= 1
    assert snap["stuck_signal_count"] >= 1
    assert snap["re_explain_rate"] > 0


def test_compress_token_savings():
    q = SessionQuality(session_id="t2")
    q.record_compress(
        tokens_before=10_000,
        tokens_after=4_000,
        quality={"score": 0.9, "paths_kept": 5, "paths_lost": 0, "decisions_kept": 2, "decisions_lost": 0},
    )
    snap = q.snapshot()
    assert snap["tokens_saved_by_compress"] == 6_000
    assert snap["last_compress"]["quality_score"] == 0.9
    assert snap["avg_compress_quality"] == 0.9


def test_review_keeps_paths_in_brief():
    messages = [
        {
            "role": "user",
            "content": "Edit C:\\Users\\me\\proj\\src\\app.ts and decided to use vite",
        },
        {
            "role": "assistant",
            "content": "Updating src/app.ts as requested.",
        },
    ]
    brief = SessionBrief(
        intent="edit app",
        artifacts=["C:\\Users\\me\\proj\\src\\app.ts"],
        decisions=["use vite"],
        key_paths=["src/app.ts"],
    )
    r = review_compress_quality(
        messages_before=messages,
        brief=brief,
        tokens_before=5000,
        tokens_after=1200,
    )
    assert r["score"] >= 0.5
    assert r["paths_kept"] >= 1
    assert r["ok"] is True


def test_review_flags_lost_paths():
    messages = [
        {"role": "user", "content": "Please fix /home/u/secret/config.toml now"},
    ]
    brief = SessionBrief(intent="fix something", artifacts=[], decisions=[])
    r = review_compress_quality(
        messages_before=messages,
        brief=brief,
        tokens_before=2000,
        tokens_after=200,
    )
    assert r["paths_lost"] >= 1
    assert r["score"] < 1.0


def test_tool_fail_streak_counts_stuck():
    q = SessionQuality()
    q.record_tool_result(success=False)
    q.record_tool_result(success=False)
    q.record_tool_result(success=False)
    assert q.max_tool_fail_streak >= 3
    assert q.stuck_signal_count >= 1


def test_snapshot_running_aggregates_o1():
    """Compress savings/avg stay correct after many events (no full rewalk needed)."""
    q = SessionQuality(session_id="agg")
    for i in range(5):
        q.record_compress(
            tokens_before=1000 * (i + 2),
            tokens_after=500,
            quality={"score": 0.8},
        )
    snap = q.snapshot()
    assert snap["compress_count"] == 5
    # (2000-500)+(3000-500)+... = 1500+2500+3500+4500+5500 = 17500
    assert snap["tokens_saved_by_compress"] == 17_500
    assert snap["avg_compress_quality"] == 0.8
    assert snap["last_compress"]["tokens_before"] == 6000


def test_needs_remedy_signals_quiet_vs_stuck():
    q = SessionQuality(session_id="nr")
    assert q.needs_remedy_signals() is False
    q.record_turn(user_text="hello")
    assert q.needs_remedy_signals() is False
    q.record_turn(user_text="I already told you the path")
    assert q.needs_remedy_signals() is True


def test_begin_turn_reuses_session_quality_and_records_tier_metric():
    """Single quality handle path + cheap tier counter for accuracy dashboards."""
    from remedy.core.metabolism.turn import begin_turn_metabolism
    from remedy.core.metrics import default_registry

    reset_session_quality("sq_reuse_sess")
    before = default_registry.counter(
        "remedy_turn_tier_total", tier="L1_lean"
    ).value
    meta = begin_turn_metabolism(
        session_id="sq_reuse_sess",
        user_text="explain hashing briefly",
        intent="chat",
        tools_enabled=True,
    )
    assert int(meta["tier"]) == 1
    after = default_registry.counter(
        "remedy_turn_tier_total", tier="L1_lean"
    ).value
    assert after == before + 1
    q = get_session_quality("sq_reuse_sess")
    assert q.last_tier == 1
