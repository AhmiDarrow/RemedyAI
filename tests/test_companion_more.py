"""Taste, inbox, away mode, visual observe, inject budget."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.away_mode import looks_like_away_request
from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    can_machine_inject,
    keep_agency_after_green,
)
from remedy.core.companion_inbox import format_inbox_block, poll_new_drops
from remedy.core.companion_observe import maybe_visual_observe, write_set_looks_visual
from remedy.core.companion_taste import (
    extract_taste,
    format_taste_block,
    load_taste,
    remember_taste,
)


def test_away_phrases():
    assert looks_like_away_request("I'm stepping away, finish this")
    assert looks_like_away_request("work alone on the API")
    assert looks_like_away_request("take it from here")
    assert not looks_like_away_request("thanks")


def test_begin_build_stamps_away(tmp_path):
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        effective_project_path=lambda: tmp_path,
    )
    st = begin_build_turn(rt, "I'm stepping away — finish without me")
    assert st is not None
    assert st.away_mode is True
    assert st.max_serial_explore == 1


def test_taste_roundtrip(tmp_path):
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    remember_taste("Prefer 8px spacing and Inter", rt)
    remember_taste("Prefer 8px spacing and Inter", rt)  # de-dupe
    items = load_taste(rt)
    assert len(items) == 1
    block = format_taste_block(items)
    assert "8px" in block
    assert extract_taste("I prefer dark mode and 8px spacing.")


def test_inbox_new_drop(tmp_path, monkeypatch):
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (desk / "mock.png").write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path / ".remedy"))
    (tmp_path / ".remedy").mkdir()
    first = poll_new_drops(rt, extra_roots=[desk], mark_seen=True)
    assert any(d["name"] == "mock.png" for d in first)
    second = poll_new_drops(rt, extra_roots=[desk], mark_seen=True)
    assert second == []
    assert "mock.png" in format_inbox_block(first)


def test_visual_write_set():
    assert write_set_looks_visual(["src/App.tsx"], "")
    assert write_set_looks_visual(["a.py"], "pygame window")
    assert not write_set_looks_visual(["a.py"], "add helper")


def test_maybe_visual_observe_once():
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="fix the landing")
    rt = SimpleNamespace(config=None)
    first = maybe_visual_observe(rt, st)
    assert first is not None
    assert st.visual_observe_ran is True
    assert maybe_visual_observe(rt, st) is None


def test_inject_budget_and_keep_agency_ui():
    st = BuildTurnState(active=True, write_set=["App.tsx"], goal="landing")
    assert can_machine_inject(st, cap=2, consume=False) is True
    assert st.machine_injects == 0
    assert can_machine_inject(st, cap=2) is True
    assert can_machine_inject(st, cap=2) is True
    assert can_machine_inject(st, cap=2) is False
    assert can_machine_inject(st, cap=2, consume=False) is False
    st.visual_observe_ran = False
    assert keep_agency_after_green(st) is True
    st.visual_observe_ran = True
    # UI writes already observed — play regex may still be false
    assert keep_agency_after_green(st) is False
    st.away_mode = True
    st.open_todo_count = 2
    assert keep_agency_after_green(st) is True
