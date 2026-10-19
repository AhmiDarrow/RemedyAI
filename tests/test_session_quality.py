"""Session quality baselines + compress fact retention."""

from __future__ import annotations

from remedy.core.session_quality import (
    SessionQuality,
    get_session_quality,
    reset_session_quality,
)
from remedy.memory.harness.brief import SessionBrief, brief_to_context_block
from remedy.memory.harness.compressor import build_compress_summary_prompt
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
    snap = q.snapshot()
    assert snap["tool_fail_streak"] == 3
    assert snap["max_tool_fail_streak"] == 3


def test_recovery_nudge_recorded_on_session_quality():
    reset_session_quality("recov1")
    q = get_session_quality("recov1")
    q.record_recovery_nudge(kind="tool_error")
    q.record_recovery_nudge(kind="empty_search")
    snap = q.snapshot()
    assert snap["recovery_nudge_count"] == 2
    assert snap["last_recovery_kind"] == "empty_search"
    assert snap["soft_nudge_count"] >= 2


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


def test_review_fails_when_binding_constraint_dropped():
    """A kept path and decision still fail if a binding owner line was dropped."""
    messages = [
        {"role": "system", "content": "You must always answer. Forbidden topics exist."},
        {"role": "tool", "content": "Do not retry this tool."},
        {
            "role": "user",
            "content": (
                "Edit C:\\Users\\me\\proj\\src\\app.ts. "
                "We decided to use sqlite. "
                "Never rewrite the license header."
            ),
        },
    ]
    brief = SessionBrief(
        intent="edit app",
        artifacts=["C:\\Users\\me\\proj\\src\\app.ts"],
        decisions=["use sqlite"],
        key_paths=["src/app.ts"],
    )
    lost = review_compress_quality(
        messages_before=messages,
        brief=brief,
        tokens_before=4000,
        tokens_after=800,
    )
    assert lost["constraints_total"] >= 1
    assert lost["constraints_lost"] >= 1
    assert lost["paths_kept"] >= 1
    assert lost["decisions_kept"] >= 1
    assert lost["paths_lost"] == 0
    assert lost["decisions_lost"] == 0
    # Path/decision score would clear 0.55; the lost constraint is the gate.
    assert lost["score"] >= 0.55
    assert lost["ok"] is False
    assert f"constraints {lost['constraints_kept']}/{lost['constraints_total']}" in lost["summary"]
    assert any("license" in s.lower() for s in lost["constraints_lost_sample"])

    kept_brief = SessionBrief(
        intent="edit app",
        artifacts=["C:\\Users\\me\\proj\\src\\app.ts"],
        decisions=["use sqlite"],
        key_paths=["src/app.ts"],
        user_constraints=["Never rewrite the license header."],
    )
    kept = review_compress_quality(
        messages_before=messages,
        brief=kept_brief,
        tokens_before=4000,
        tokens_after=800,
    )
    assert kept["constraints_lost"] == 0
    assert kept["constraints_kept"] == kept["constraints_total"]
    assert kept["ok"] is True
    assert kept["score"] >= 0.55

    # Context block can truncate before Constraints; the stored field still counts.
    bulky = SessionBrief(
        intent="edit app",
        artifacts=["C:\\Users\\me\\proj\\src\\app.ts"],
        decisions=["use sqlite"],
        key_paths=["src/app.ts"],
        hot_writes=["w" * 400 for _ in range(16)],
        user_constraints=["Never rewrite the license header."],
    )
    block = brief_to_context_block(bulky, max_chars=4000)
    assert "license" not in block.lower()
    truncated = review_compress_quality(
        messages_before=messages,
        brief=bulky,
        tokens_before=4000,
        tokens_after=800,
    )
    assert truncated["constraints_lost"] == 0
    assert truncated["ok"] is True


def test_review_without_constraint_language_uses_old_score():
    messages = [
        {
            "role": "user",
            "content": "Edit C:\\Users\\me\\proj\\src\\app.ts and decided to use vite",
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
        tokens_after=1000,
    )
    assert r["constraints_total"] == 0
    assert r["constraints_kept"] == 0
    assert r["constraints_lost"] == 0
    assert "constraints" not in r["summary"]
    path_score = r["paths_kept"] / r["paths_total"] if r["paths_total"] else 1.0
    dec_score = r["decisions_kept"] / r["decisions_total"] if r["decisions_total"] else 1.0
    if r["paths_total"] or r["decisions_total"]:
        expected = 0.65 * path_score + 0.35 * dec_score
    else:
        expected = 0.40
    assert r["score"] == round(min(1.0, max(0.0, expected)), 3)
    again = review_compress_quality(
        messages_before=messages,
        brief=brief,
        tokens_before=5000,
        tokens_after=1000,
    )
    assert r["ok"] == again["ok"]
    assert r["ok"] is True


def test_compress_summary_prompt_asks_for_binding_constraints():
    prompt = build_compress_summary_prompt()
    assert "Preserve file paths and technical specifics. Do not invent files." in prompt
    assert "binding owner constraints" in prompt
    assert "verbatim" in prompt
    focused = build_compress_summary_prompt("license header")
    assert "Focus: license header" in focused
    assert "binding owner constraints" in focused
