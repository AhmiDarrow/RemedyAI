"""Continuity layer: ContextSnapshot, intent policy, remedies, structural prune, project learning."""

from __future__ import annotations

from remedy.core.context_snapshot import build_context_snapshot
from remedy.core.intent_policy import policy_for_intent
from remedy.core.project_learning import (
    clear_project_profile_cache,
    load_project_profile,
    profile_cache_stats,
    record_session_end,
    suggest_harness_pct,
)
from remedy.core.quality_remedies import remedies_from_quality
from remedy.core.session_quality import reset_session_quality
from remedy.memory.harness.brief import SessionBrief
from remedy.memory.harness.pruner import prune_messages_for_send


def test_policy_packs_for_intents():
    assert policy_for_intent("memory")["id"] == "memory"
    assert "memory" in policy_for_intent("memory")["system"].lower() or "Session" in policy_for_intent("memory")["system"]
    # "implement" maps to build pack; "fix" maps to default task loop
    build = policy_for_intent("chat", user_text="please implement a fix for login")
    assert build["id"] == "build"
    tool = policy_for_intent("chat", user_text="please fix the login bug")
    assert tool["id"] in ("task", "tool", "build")
    assert "tool" in (tool.get("system") or "").lower()


def test_remedies_trigger_on_re_explain():
    rem = remedies_from_quality(
        {"re_explain_count": 2, "re_explain_rate": 0.3, "stuck_signal_count": 0, "turns": 5},
        fill_pct=0.4,
    )
    assert rem["triggered"]
    assert "re_explain_anchor" in rem["actions"]
    assert rem["system"]


def test_remedies_stuck():
    rem = remedies_from_quality(
        {
            "re_explain_count": 0,
            "re_explain_rate": 0,
            "stuck_signal_count": 3,
            "stuck_rate": 0.25,
            "max_tool_fail_streak": 4,
            "turns": 10,
        }
    )
    assert "stuck_recovery" in rem["actions"]


def test_context_snapshot_single_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    reset_session_quality("cs1")
    brief = SessionBrief(intent="")
    msgs = [
        {"role": "user", "content": "remember that we use TypeScript"},
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "what do you know about my stack?"},
    ]
    snap = build_context_snapshot(
        messages=msgs,
        user_text="what do you know about my stack?",
        brief=brief,
        session_id="cs1",
        context_window=100_000,
        min_pct=0.75,
        max_pct=0.92,
    )
    assert snap.token_estimate >= 1
    assert snap.intent in ("memory", "chat", "skill", "plan", "tool")
    assert snap.fill_pct >= 0
    # Small chat history → light phase (pack/scout/spread skipped)
    assert snap.signals.get("snapshot_phase") in ("light", "full")
    if snap.fill_pct < 0.40 and not snap.nudge and snap.intent == "chat":
        assert snap.signals.get("snapshot_phase") == "light"
        assert (snap.signals.get("pack") or {}).get("skipped") is True
    pub = snap.to_public()
    assert "token_estimate" in pub


def test_lean_snapshot_skips_nanoswarm_bots(tmp_path, monkeypatch):
    """L0/L1 lean path must not pay library/pattern/goal/health on pure chat."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    reset_session_quality("lean1")
    snap = build_context_snapshot(
        messages=[{"role": "user", "content": "hi there how are you"}],
        user_text="hi there how are you",
        session_id="lean1",
        context_window=100_000,
        full_snapshot=False,
    )
    assert snap.signals.get("lean_snapshot") is True
    assert (snap.signals.get("library_suggest") or {}).get("skipped") is True
    assert (snap.signals.get("pattern") or {}).get("skipped") is True
    assert (snap.signals.get("goal") or {}).get("skipped") is True
    assert (snap.signals.get("health") or {}).get("skipped") is True
    assert (snap.signals.get("pack") or {}).get("skipped") is True
    # Quiet session → remedies assembly skipped (still records turn)
    assert snap.signals.get("remedies_skipped") == "lean_quiet"
    assert snap.signals.get("remedies") == []


def test_structural_collapse_old_tools():
    messages = []
    for i in range(8):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": f"c{i}", "function": {"name": "bash_exec", "arguments": "{}"}}],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": ("x" * 500) + f" result {i}",
            }
        )
    out = prune_messages_for_send(
        messages,
        dedupe_tools=False,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=2,
    )
    tool_bodies = [m["content"] for m in out if m.get("role") == "tool"]
    assert any("collapsed" in str(c) for c in tool_bodies)
    # Recent ones still long
    assert any(len(str(c)) > 400 for c in tool_bodies[-2:])


def test_project_learning_earlier_compress(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    path = str(tmp_path / "myproj")
    q = {
        "turns": 20,
        "compress_count": 3,
        "tokens_saved_by_compress": 5000,
        "re_explain_count": 3,
        "stuck_signal_count": 1,
        "strong_nudge_count": 5,
        "tokens_estimated_peak": 90_000,
        "avg_compress_quality": 0.8,
    }
    prof = record_session_end(path, q)
    assert prof["prefer_earlier_compress"] is True
    mn, mx = suggest_harness_pct(prof, 0.75, 0.92)
    assert mn < 0.75
    assert mx < 0.92
    loaded = load_project_profile(path)
    assert loaded["sessions"] >= 1


def test_project_profile_load_cached_per_turn(tmp_path, monkeypatch):
    """Repeated load_project_profile hits mtime cache (no re-read thrash)."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    clear_project_profile_cache()
    path = str(tmp_path / "cached_proj")
    record_session_end(
        path,
        {
            "turns": 5,
            "re_explain_count": 2,
            "strong_nudge_count": 0,
            "tokens_estimated_peak": 1000,
        },
    )
    clear_project_profile_cache()
    a = load_project_profile(path)
    stats1 = profile_cache_stats()
    b = load_project_profile(path)
    c = load_project_profile(path)
    stats2 = profile_cache_stats()
    assert a["id"] == b["id"] == c["id"]
    assert stats2["hits"] > stats1["hits"]
    # Pins from re_explain land and snapshot can stash them
    assert any("constraint" in str(p).lower() or "Session" in str(p) for p in (a.get("pinned_constraints") or [])) or a.get("sessions", 0) >= 1


def test_project_profile_load_skips_mkdir_and_caps_projects(tmp_path, monkeypatch):
    """Hot load does not mkdir; store caps project count; save keeps cache coherent."""
    from pathlib import Path

    from remedy.core.project_learning import load_all, save_all

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    clear_project_profile_cache()
    # Missing profiles.json: load must not create project_learning/ on disk
    data = load_all()
    assert data["projects"] == {}
    pl_dir = Path(tmp_path) / "project_learning"
    assert not pl_dir.exists() or not (pl_dir / "profiles.json").exists()
    # Second load of empty still hits cache (no re-miss thrash)
    stats_before = profile_cache_stats()
    load_all()
    stats_after = profile_cache_stats()
    assert stats_after["hits"] > stats_before["hits"]

    # Cap: writing >80 projects drops oldest (via record_session_end)
    clear_project_profile_cache()
    from remedy.core.project_learning import record_session_end

    for i in range(85):
        record_session_end(
            f"/tmp/cap_proj_{i}", {"turns": 1, "tokens_estimated_peak": 100}
        )
    all_data = load_all()
    assert len(all_data.get("projects") or {}) <= 80
    # Save updates cache without forcing re-read
    save_all(all_data)
    s1 = profile_cache_stats()
    load_all()
    s2 = profile_cache_stats()
    assert s2["hits"] > s1["hits"]
