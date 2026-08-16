"""Portable identity export merges live time crystals."""

from __future__ import annotations

from pathlib import Path

from remedy.core.metabolism.identity_export import collect_default_payload
from remedy.core.metabolism.time_crystal import get_time_crystal, reset_time_crystal


def test_identity_export_merges_life_and_live_sessions(tmp_path: Path) -> None:
    reset_time_crystal("life")
    reset_time_crystal("sess-a")
    reset_time_crystal("_export")
    life = get_time_crystal("life", home=tmp_path)
    life.admit("I cook on Sundays with the family", horizon="life")
    life.persist(tmp_path)
    sess = get_time_crystal("sess-a", home=tmp_path)
    sess.admit("This project uses SQLite for the ledger", horizon="project_week")
    sess.persist(tmp_path)
    reset_time_crystal("life")
    reset_time_crystal("sess-a")
    payload = collect_default_payload(tmp_path)
    rows = payload.get("time_crystal") or []
    texts = [str(r.get("text") or "") for r in rows if isinstance(r, dict)]
    assert any("cook" in t.lower() for t in texts)
    assert any("sqlite" in t.lower() for t in texts)
    assert not any("_export" in t for t in texts)
