"""Drives, autobiography, outcomes, reach-out (opt-in), and approved reflection."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism import alive
from remedy.core.organism_knobs import resolve_bool, resolve_int


def test_alive_knob_defaults() -> None:
    assert resolve_bool("proactive_reach_out", {}) is False
    assert resolve_bool("drives_enabled", {}) is True
    assert resolve_int("autobiography_chars", {}) == 400
    assert resolve_bool("weekly_reflection", {}) is True
    assert resolve_bool("voice_drift_from_affect", {}) is True


def test_tick_drifts_and_picks_idle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(alive, "_knob_bool", lambda key, default=True: default if key != "weekly_reflection" else False)
    monkeypatch.setattr(alive, "_knob_float", lambda key, default=0.0: 0.08 if key == "drive_drift_rate" else default)
    out = alive.tick_alive(tmp_path, heavy=True)
    assert out["ok"] is True
    state = alive.load_alive(tmp_path)
    assert set(state["drives"]) == set(alive.DRIVE_KEYS)
    assert all(0.0 <= v <= 1.0 for v in state["drives"].values())
    assert state["last_idle"] in {"rest", "dream", "skills", "offer", "maintain"}
    # Heavy use raises rest.
    assert state["drives"]["rest"] > 0.45


def test_reach_out_off_by_default(tmp_path: Path, monkeypatch) -> None:
    state = alive.default_state()
    state["drives"]["social"] = 0.95
    alive.save_alive(state, tmp_path)
    monkeypatch.setattr(alive, "_knob_bool", lambda key, default=True: False if key == "proactive_reach_out" else default)
    monkeypatch.setattr(alive, "_knob_float", lambda key, default=0.0: 0.8 if key == "reach_out_threshold" else 0.0)
    alive._maybe_reach_out(state, 1_000_000.0)
    assert state["reach_out"]["pending"] is False
    # Inject must not include an offer while off.
    text = alive.build_alive_inject(tmp_path)
    assert "check in" not in text.lower()
    public = alive.public_alive(home=tmp_path)
    assert public["reach_out"]["pending"] is False


def test_reach_out_arms_once_when_opted_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        alive,
        "_knob_bool",
        lambda key, default=True: True if key == "proactive_reach_out" else default,
    )
    monkeypatch.setattr(
        alive,
        "_knob_float",
        lambda key, default=0.0: 0.8 if key == "reach_out_threshold" else 0.0,
    )
    state = alive.default_state()
    state["drives"]["curiosity"] = 0.9
    state["autobiography"] = "I am Remedy."
    alive.save_alive(state, tmp_path)
    loaded = alive.load_alive(tmp_path)
    alive._maybe_reach_out(loaded, 1_000_000.0)
    assert loaded["reach_out"]["pending"] is True
    assert loaded["reach_out"]["gloss"]
    alive.save_alive(loaded, tmp_path)
    text = alive.build_alive_inject(tmp_path)
    assert "look into" in text.lower()
    # Second inject does not nag.
    text2 = alive.build_alive_inject(tmp_path)
    assert "look into" not in text2.lower()


def test_autobiography_is_capped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(alive, "_knob_int", lambda key, default=0: 80)
    monkeypatch.setattr(alive, "_knob_bool", lambda key, default=True: False)
    state = alive.default_state()
    state["autobiography"] = "I am Remedy, a long partner. " * 20
    alive.save_alive(state, tmp_path)
    # Re-compose through consolidate with no field.
    alive.consolidate_from_dream(tmp_path, field=None)
    saved = alive.load_alive(tmp_path)
    assert len(saved["autobiography"]) <= 80
    inject = alive.build_alive_inject(tmp_path)
    assert len(inject) <= 120
    assert "Remedy" in inject


def test_outcomes_feed_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(alive, "_knob_bool", lambda key, default=True: True)
    before = alive.load_alive(tmp_path)["affect"]["valence"]
    out = alive.note_turn_outcome(tmp_path, user_text="Thanks, that worked perfectly.")
    assert out["kind"] == "accepted"
    mid = alive.load_alive(tmp_path)
    assert mid["affect"]["valence"] >= before
    assert mid["outcomes"][-1]["kind"] == "accepted"
    out2 = alive.note_turn_outcome(tmp_path, user_text="That's wrong, still broken.")
    assert out2["kind"] == "unhappy"
    after = alive.load_alive(tmp_path)
    assert after["affect"]["valence"] < mid["affect"]["valence"]


def test_weekly_reflection_requires_approval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(alive, "_knob_bool", lambda key, default=True: True)
    monkeypatch.setattr(alive, "_knob_float", lambda key, default=0.0: 1.0 if key == "weekly_reflection_s" else default)
    state = alive.default_state()
    state["drives"]["rest"] = 0.9
    state["last_reflection_ts"] = 0.0
    alive.save_alive(state, tmp_path)
    alive.tick_alive(tmp_path)
    loaded = alive.load_alive(tmp_path)
    pending = [p for p in loaded["pending_changes"] if p.get("status") == "pending"]
    assert pending, loaded
    cid = pending[0]["id"]
    # Not applied until approved.
    assert not any(str(b).startswith("Agreed:") for b in loaded["beliefs"])
    denied = alive.approve_self_change(tmp_path, cid, approve=False)
    assert denied["ok"] is True and denied["status"] == "denied"
    still = alive.load_alive(tmp_path)
    assert not any(str(b).startswith("Agreed:") for b in still["beliefs"])
    # Queue another and approve.
    still["last_reflection_ts"] = 0.0
    still["pending_changes"] = []
    alive.save_alive(still, tmp_path)
    alive.tick_alive(tmp_path)
    again = [p for p in alive.load_alive(tmp_path)["pending_changes"] if p.get("status") == "pending"]
    assert again
    ok = alive.approve_self_change(tmp_path, again[0]["id"], approve=True)
    assert ok["status"] == "approved"
    final = alive.load_alive(tmp_path)
    assert any(str(b).startswith("Agreed:") for b in final["beliefs"])


def test_choose_idle_prefers_rest_when_worn() -> None:
    assert alive.choose_idle_action({"curiosity": 0.9, "social": 0.9, "competence": 0.9, "rest": 0.85}) == "rest"
    assert alive.choose_idle_action({"curiosity": 0.8, "social": 0.2, "competence": 0.2, "rest": 0.2}) == "dream"
