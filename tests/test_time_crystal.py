"""Time crystal persist / hydrate."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism.time_crystal import get_time_crystal, reset_time_crystal


def test_time_crystal_hydrates_full_facts(tmp_path: Path) -> None:
    reset_time_crystal("life")
    crystal = get_time_crystal("life", home=tmp_path)
    for i in range(12):
        crystal.admit(f"durable cooking fact number {i:02d} about Sundays", horizon="life")
    crystal.admit("session-only note about today's draft", horizon="session")
    path = crystal.persist(tmp_path)
    assert path is not None and path.is_file()
    reset_time_crystal("life")
    loaded = get_time_crystal("life", home=tmp_path)
    texts = [f.text for f in loaded.facts]
    assert any("durable cooking fact number 00" in t for t in texts)
    assert any("session-only note" in t for t in texts)
    assert len(loaded.facts) >= 13
    assert any(f.horizon == "life" for f in loaded.facts)
