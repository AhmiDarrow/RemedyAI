"""Organism vitals / CAS count after take_step."""

from __future__ import annotations

from pathlib import Path


def test_take_step_cas_count_survives_cycle(tmp_path: Path, monkeypatch) -> None:
    from remedy.core.metabolism import organism as org
    from remedy.core.metabolism.organism import (
        load_vitals,
        note_cas_write,
        organism_cycle,
        persist_vitals,
    )

    home = tmp_path / "org"
    home.mkdir()
    persist_vitals(
        {
            "ts": 1,
            "alive": True,
            "open_count": 1,
            "stalled": False,
            "last_drive_at": 1,
            "last_pulse_at": 1e18,
            "last_heartbeat_at": 1e18,
            "last_compact_at": 1e18,
            "cas_count": 0,
            "cas_durable": 0,
            "mood": "calm",
            "who": "Remedy",
            "life_title": "Write",
        },
        home,
    )

    def _step(_home):
        note_cas_write(_home, kind="life")
        return {"ok": True, "goal": "Write", "did": "Outlined", "next": "Draft"}

    monkeypatch.setattr(org, "organism_heartbeat", lambda *a, **k: {"recalled": 0})
    monkeypatch.setattr("remedy.memory.life_drive.take_step", _step)
    monkeypatch.setattr("remedy.memory.life_drive.drive_due", lambda *a, **k: True)
    monkeypatch.setattr("remedy.memory.life_goals.pulse_due", lambda *a, **k: False)

    out = organism_cycle(home, session_id="life")
    assert int(out["vitals"].get("cas_count") or 0) >= 1
    assert int(load_vitals(home).get("cas_count") or 0) >= 1
    assert int(out["vitals"].get("cas_durable") or 0) >= 1
