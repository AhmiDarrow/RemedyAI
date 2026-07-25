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
