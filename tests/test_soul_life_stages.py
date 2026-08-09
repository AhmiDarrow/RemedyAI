"""Life stages 1–5: enrich, soma, missions, portable, continuity self-inject."""

from __future__ import annotations

from remedy.memory.soul.continuity_metrics import measure_continuity, primary_self_inject_focus
from remedy.memory.soul.dream import reset_dream_cooldown
from remedy.memory.soul.field import clear_soul_cache, load_soul_field
from remedy.memory.soul.missions_bridge import arm_soul_missions, collect_soul_mission_candidates
from remedy.memory.soul.portable import (
    export_soul_plain,
    import_soul_file,
    soul_export_payload,
)
from remedy.memory.soul.somatic import compute_soma, refresh_soma
from remedy.memory.soul.update import update_soul_after_turn


def test_soma_mood_from_bond(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="No that's wrong — just fix it, no fluff.",
        assistant_text="Fixed.",
        session_id="soma-1",
        home=tmp_path,
    )
    snap = compute_soma(tmp_path, muscle_label="frontier", muscle_provider="xai")
    assert snap.mood in ("strained", "focused", "recovering", "calm", "playful")
    assert snap.emoji
    assert "Remedy" in snap.tray_tooltip
    pub = refresh_soma(tmp_path, muscle_label="frontier", muscle_provider="xai")
    assert pub["mood"] == snap.mood
    assert (tmp_path / "soul" / "soma.json").is_file()


def test_mission_candidates_from_pledges(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="From now on we always ship with tests green before release.",
        assistant_text="Understood.",
        session_id="m1",
        home=tmp_path,
    )
    sf = load_soul_field(tmp_path)
    sf.relational.open_threads.append("finish soul field organism life stages")
    from remedy.memory.soul.field import save_soul_field

    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    cands = collect_soul_mission_candidates(tmp_path, limit=5)
    assert cands
    assert any(len(c["goal"]) >= 10 for c in cands)
    res = arm_soul_missions(home=tmp_path, session_id="m1", max_new=1, auto=True)
    assert res["ok"]
    # Second call should skip if active
    res2 = arm_soul_missions(home=tmp_path, session_id="m1", max_new=1, auto=True)
    assert res2.get("skipped") in ("active_mission", "all_duplicates", "") or res2.get("armed") is not None


def test_portable_soul_roundtrip(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="Remember we pair program and ship it.",
        assistant_text="Yes.",
        session_id="p1",
        home=tmp_path,
        provider="xai",
        model="grok-4",
    )
    dest = tmp_path / "exports" / "soul.json"
    path = export_soul_plain(dest, home=tmp_path)
    assert path.is_file()
    pack = soul_export_payload(tmp_path)
    assert pack["format"] == "remedy-soul-field"
    assert "soul" in pack

    # Fresh home merge
    home2 = tmp_path / "other"
    home2.mkdir()
    clear_soul_cache()
    res = import_soul_file(path, home=home2, merge=True)
    assert res["ok"]
    sf = load_soul_field(home2)
    assert sf.relational.turns_together >= 1 or sf.episodes or sf.pledges or sf.self_habits


def test_continuity_metrics_suggest_targets(tmp_path):
    clear_soul_cache()
    # Empty-ish field → low score + targets
    score = measure_continuity(tmp_path)
    assert 0.0 <= score.overall <= 1.0
    assert score.suggested_targets
    focus = primary_self_inject_focus(tmp_path)
    assert "focus" in focus
    assert focus["focus"].get("path", "").startswith("src/")


def test_identity_payload_includes_soul(tmp_path):
    clear_soul_cache()
    update_soul_after_turn(
        user_text="Call me Ahmi and we always be real.",
        assistant_text="Got it Ahmi.",
        session_id="id1",
        home=tmp_path,
    )
    # Point home so soul is under tmp
    import os

    from remedy.core.metabolism.identity_export import collect_default_payload

    os.environ["REMEDY_HOME"] = str(tmp_path)
    try:
        clear_soul_cache()
        payload = collect_default_payload(tmp_path)
        assert isinstance(payload.get("soul"), dict) or payload.get("soul") is None
        # After turns, soul should be present
        assert payload.get("soul") is not None
        assert "relational" in payload["soul"]
    finally:
        os.environ.pop("REMEDY_HOME", None)
        clear_soul_cache()


def test_local_enrich_graceful_without_server(tmp_path):
    """Local enrich fails soft when no llama server — dream still ok."""
    clear_soul_cache()
    reset_dream_cooldown()
    for i in range(3):
        update_soul_after_turn(
            user_text=f"Continue the organism work {i} please later.",
            assistant_text="Continuing.",
            session_id="enr",
            home=tmp_path,
        )
    from remedy.memory.soul.dream import dream_cycle
    from remedy.memory.soul.field import load_soul_field, save_soul_field

    sf = load_soul_field(tmp_path)
    for ep in sf.episodes:
        ep.open_thread = "finish organism life stages pack"
    save_soul_field(sf, tmp_path)
    clear_soul_cache()
    reset_dream_cooldown()
    result = dream_cycle(
        home=tmp_path,
        force=True,
        use_local=True,
        local_base_url="http://127.0.0.1:9/v1",  # closed port
    )
    assert result.get("ok")
    # local_enrich may fail ok=False but dream continues
    assert "local_enrich" in result
