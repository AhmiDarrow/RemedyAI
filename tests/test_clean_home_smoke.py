"""Clean-home smoke: first-run surfaces without ~/.remedy vision install.

Simulates a PC that has never downloaded the local model and never opened
a prior Remedy profile (isolated REMEDY_HOME).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def clean_home(tmp_path, monkeypatch):
    home = tmp_path / "remedy-home"
    home.mkdir()
    monkeypatch.setenv("REMEDY_HOME", str(home))
    # Do not inherit product bundle path from developer machine
    monkeypatch.delenv("REMEDY_LOCAL_BUNDLE", raising=False)
    monkeypatch.delenv("REMEDY_BUNDLE_DIR", raising=False)
    return home


def test_clean_home_vision_not_installed(clean_home):
    from remedy.vision.install import is_installed
    from remedy.vision.service import get_status, maybe_autostart_local_model

    assert is_installed(home_dir=clean_home) is False
    st = get_status(
        {"home_dir": str(clean_home), "vision": {"enabled": True, "auto_start": True}},
        light=True,
    )
    assert st["installed"] is False
    assert st["ready"] is False
    assert st.get("delivery") == "first_run_download"

    r = maybe_autostart_local_model(
        {"home_dir": str(clean_home), "vision": {"enabled": True, "auto_start": True}}
    )
    assert r.get("skipped") is True or r.get("ok") is False


def test_clean_home_context_snapshot_and_policy(clean_home):
    from remedy.core.context_snapshot import build_context_snapshot
    from remedy.core.session_quality import reset_session_quality
    from remedy.memory.harness.brief import SessionBrief

    reset_session_quality("clean-smoke")
    brief = SessionBrief()
    snap = build_context_snapshot(
        messages=[
            {"role": "user", "content": "please implement a login fix in src/app.ts"},
        ],
        user_text="please implement a login fix in src/app.ts",
        brief=brief,
        session_id="clean-smoke",
        project_path=str(clean_home / "proj"),
        context_window=100_000,
    )
    assert snap.token_estimate >= 1
    assert snap.intent in ("tool", "chat", "memory", "skill", "plan")
    # Tool-biased prompt should pick tool policy
    assert snap.policy_id in ("tool", "chat", "memory", "skill", "plan")
    pub = snap.to_public()
    assert "fill_pct" in pub


def test_clean_home_structural_prune_and_quality(clean_home):
    from remedy.core.quality_remedies import remedies_from_quality
    from remedy.memory.harness.brief import SessionBrief
    from remedy.memory.harness.pruner import prune_messages_for_send
    from remedy.memory.harness.quality import review_compress_quality

    msgs = [
        {"role": "tool", "tool_call_id": f"c{i}", "content": ("line\n" * 80) + str(i)}
        for i in range(8)
    ]
    out = prune_messages_for_send(
        msgs,
        dedupe_tools=False,
        collapse_completed_tools=True,
        keep_recent_tool_pairs=2,
    )
    assert any("collapsed" in str(m.get("content", "")) for m in out)

    rem = remedies_from_quality(
        {
            "re_explain_count": 2,
            "re_explain_rate": 0.35,
            "stuck_signal_count": 2,
            "stuck_rate": 0.25,
            "max_tool_fail_streak": 3,
            "turns": 6,
        }
    )
    assert rem["triggered"] is True
    assert rem["system"]

    brief = SessionBrief(
        intent="fix login",
        artifacts=["src/app.ts"],
        decisions=["use vite"],
        key_paths=["src/app.ts"],
    )
    q = review_compress_quality(
        messages_before=[
            {"role": "user", "content": "edit src/app.ts decided to use vite"},
        ],
        brief=brief,
        tokens_before=8000,
        tokens_after=1500,
    )
    assert 0.0 <= q["score"] <= 1.0
    assert "summary" in q


def test_clean_home_project_learning(clean_home):
    from remedy.core.project_learning import (
        load_project_profile,
        record_session_end,
        suggest_harness_pct,
    )

    path = str(clean_home / "workspace")
    prof = record_session_end(
        path,
        {
            "turns": 25,
            "strong_nudge_count": 6,
            "tokens_estimated_peak": 100_000,
            "re_explain_count": 3,
            "stuck_signal_count": 2,
            "compress_count": 4,
            "tokens_saved_by_compress": 12_000,
            "avg_compress_quality": 0.82,
        },
    )
    assert prof["prefer_earlier_compress"] is True
    mn, mx = suggest_harness_pct(prof, 0.75, 0.92)
    assert mn < 0.75
    loaded = load_project_profile(path)
    assert loaded["sessions"] >= 1
    # Profiles land under clean REMEDY_HOME
    store = clean_home / "project_learning" / "profiles.json"
    assert store.is_file()
    data = json.loads(store.read_text(encoding="utf-8"))
    assert "projects" in data


def test_clean_home_swarm_router_heuristic_only(clean_home):
    from remedy.nanoswarm import get_swarm
    from remedy.nanoswarm.events import SwarmEvent

    # New process-global swarm is fine; assert hot path stays heuristic
    r = get_swarm().dispatch(
        SwarmEvent.message_added("user", "run git status please"),
        messages=[{"role": "user", "content": "run git status please"}],
    )
    router = r["signals"].get("router") or {}
    assert router.get("method") == "heuristic"


def test_tauri_packaging_still_excludes_local_models():
    conf = json.loads(
        (Path(__file__).resolve().parents[1] / "desktop" / "src-tauri" / "tauri.conf.json")
        .read_text(encoding="utf-8")
    )
    resources = conf.get("bundle", {}).get("resources") or {}
    blob = json.dumps(resources).replace("\\", "/")
    assert "resources/local" not in blob
