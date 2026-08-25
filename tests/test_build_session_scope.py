"""Build-active state is per chat session — a fresh tab never inherits a sibling's build."""

from __future__ import annotations

import time
from types import SimpleNamespace

from remedy.core.build_engine import (
    CROSS_SESSION_RESUME_WINDOW_S,
    BuildTurnState,
    begin_build_turn,
    build_state_owned_by,
    deactivate_build_for_session,
    get_build_state,
    ledger_resume_allowed,
)
from remedy.core.build_ledger import BuildLedgerEntry, save_ledger
from remedy.core.turn_context import begin_turn, end_turn


def _led(sid: str, age_s: float = 0.0) -> BuildLedgerEntry:
    return BuildLedgerEntry(
        goal="build the guitar tuner",
        phase="implement",
        session_id=sid,
        updated_ts=time.time() - age_s,
    )


def test_ledger_resume_allowed_rules():
    # Same session: always.
    assert ledger_resume_allowed(_led("a"), session_id="a", generic_continuation=False)
    # Different session + specific goal: never.
    assert not ledger_resume_allowed(_led("a"), session_id="b", generic_continuation=False)
    # Different session + bare "continue" inside the window: yes.
    assert ledger_resume_allowed(_led("a", 60), session_id="b", generic_continuation=True)
    # ... but not once it has gone stale.
    assert not ledger_resume_allowed(
        _led("a", CROSS_SESSION_RESUME_WINDOW_S + 5),
        session_id="b",
        generic_continuation=True,
    )
    # Anonymous runtime / legacy unstamped ledger: old behaviour.
    assert ledger_resume_allowed(_led("a"), session_id="", generic_continuation=False)
    assert ledger_resume_allowed(_led(""), session_id="b", generic_continuation=False)
    assert not ledger_resume_allowed(None, session_id="b", generic_continuation=True)


def test_build_state_owned_by():
    st = BuildTurnState(active=True, session_id="a")
    assert build_state_owned_by(st, "a")
    assert not build_state_owned_by(st, "b")
    assert build_state_owned_by(st, "")  # anonymous caller
    assert build_state_owned_by(st, "_anon")
    assert build_state_owned_by(BuildTurnState(active=True), "b")  # unstamped legacy
    assert not build_state_owned_by(None, "a")


def _rt(tmp_path, proj):
    return SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=tmp_path),
        _project_path_raw=str(proj),
        effective_project_path=lambda: proj,
    )


def _seed_ledger(tmp_path, proj, sid: str, age_s: float = 0.0):
    save_ledger(
        BuildLedgerEntry(
            goal="build the guitar tuner",
            phase="implement",
            project_path=str(proj),
            session_id=sid,
            write_steps=3,
            last_verify_ok=False,
            verify_command="pytest -q",
        ),
        home=tmp_path,
    )
    if age_s:
        # save_ledger stamps now; age it on disk for the stale case.
        import json

        from remedy.core.build_ledger import ledger_path

        p = ledger_path(str(proj), home=tmp_path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        builds = raw.get("builds") if isinstance(raw, dict) else None
        if isinstance(builds, dict):
            for v in builds.values():
                if isinstance(v, dict):
                    v["updated_ts"] = time.time() - age_s
        elif isinstance(raw, dict):
            raw["updated_ts"] = time.time() - age_s
        p.write_text(json.dumps(raw), encoding="utf-8")


def test_new_session_new_question_does_not_resume_siblings_build(tmp_path):
    """Yesterday's failure: a fresh chat asking 'can you fix this <pasted error>'
    picked up the other tab's implement-phase build and was driven as a build turn."""
    proj = tmp_path / "ExampleProject"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='g'\n", encoding="utf-8")
    _seed_ledger(tmp_path, proj, "sess-build")
    rt = _rt(tmp_path, proj)

    t = begin_turn("sess-fresh", project_raw=str(proj), active_path=str(proj))
    try:
        st = begin_build_turn(
            rt, "can you fix this Couldn't patch build_lang_oracle.py (outside project write jail)"
        )
        assert st is not None  # it IS a fix request — but its own, at scout
        assert st.session_id == "sess-fresh"
        assert st.resumed is False
        assert st.phase == "scout"
        assert st.write_steps == 0
        # Same project → same oracle is still fine to carry.
        assert st.verify_command == "pytest -q"
        assert get_build_state(rt) is st
    finally:
        end_turn("sess-fresh", *t)


def test_same_session_still_resumes_its_own_build(tmp_path):
    proj = tmp_path / "own"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='o'\n", encoding="utf-8")
    _seed_ledger(tmp_path, proj, "sess-build")
    rt = _rt(tmp_path, proj)
    t = begin_turn("sess-build", project_raw=str(proj), active_path=str(proj))
    try:
        st = begin_build_turn(rt, "now add the tuner UI", force=True)
        assert st is not None
        assert st.resumed is True
        assert st.phase == "repair"  # led.last_verify_ok=False → repair
    finally:
        end_turn("sess-build", *t)


def test_new_session_bare_continue_resumes_only_recent_build(tmp_path):
    proj = tmp_path / "cont"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='c'\n", encoding="utf-8")
    rt = _rt(tmp_path, proj)

    _seed_ledger(tmp_path, proj, "sess-build")
    t = begin_turn("sess-new", project_raw=str(proj), active_path=str(proj))
    try:
        st = begin_build_turn(rt, "continue", force=True)
        assert st is not None and st.resumed is True
    finally:
        end_turn("sess-new", *t)

    _seed_ledger(tmp_path, proj, "sess-build", age_s=CROSS_SESSION_RESUME_WINDOW_S + 120)
    t = begin_turn("sess-new-2", project_raw=str(proj), active_path=str(proj))
    try:
        st = begin_build_turn(rt, "continue", force=True)
        assert st is not None
        assert st.resumed is False
        assert st.phase == "scout"
    finally:
        end_turn("sess-new-2", *t)


def test_deactivate_build_for_session():
    rt = SimpleNamespace(
        _build_turns={
            "sess-a": BuildTurnState(active=True, session_id="sess-a"),
            "sess-b": BuildTurnState(active=True, session_id="sess-b"),
        }
    )
    assert deactivate_build_for_session(rt, "sess-a") is True
    assert rt._build_turns["sess-a"].active is False
    assert rt._build_turns["sess-b"].active is True
    assert deactivate_build_for_session(rt, "sess-a") is False


def test_ledger_resume_blocked_when_source_session_stopped():
    from remedy.core.session_continuity import (
        clear_all_continuity_caches,
        note_session_stopped,
    )

    clear_all_continuity_caches()
    led = _led("sess-build", age_s=60)
    assert ledger_resume_allowed(led, session_id="sess-new", generic_continuation=True)
    note_session_stopped("sess-build", reason="stop")
    assert not ledger_resume_allowed(
        led, session_id="sess-new", generic_continuation=True
    )
    # Same session may still continue after Stop.
    assert ledger_resume_allowed(
        led, session_id="sess-build", generic_continuation=True
    )
    clear_all_continuity_caches()


def test_get_build_state_rejects_foreign_legacy_slot():
    """Pre-map runtimes stamp one slot; a stamped state from another session is invisible."""
    rt = SimpleNamespace(_build_turn=BuildTurnState(active=True, session_id="sess-a"))
    t = begin_turn("sess-b", project_raw=None, active_path=".")
    try:
        assert get_build_state(rt) is None
    finally:
        end_turn("sess-b", *t)
    t = begin_turn("sess-a", project_raw=None, active_path=".")
    try:
        assert get_build_state(rt) is rt._build_turn
    finally:
        end_turn("sess-a", *t)
