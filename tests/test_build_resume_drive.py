"""Cross-turn autonomy: a red mid-ship build resumes and drives to green."""

from __future__ import annotations

from remedy.core.build_ledger import (
    BuildLedgerEntry,
    looks_like_continue,
    needs_resume_drive,
    resume_hint,
    save_ledger,
    should_auto_resume_drive,
)


def _red_entry(**kw) -> BuildLedgerEntry:
    e = BuildLedgerEntry(
        project_path=kw.get("project_path", "/proj"),
        goal="build the thing",
        phase="verify",
        last_verify_ok=False,
        write_set=["src/app.py"],
        write_steps=3,
    )
    for k, v in kw.items():
        setattr(e, k, v)
    return e


# --- needs_resume_drive predicate -----------------------------------------


def test_red_build_with_writes_needs_resume():
    assert needs_resume_drive(_red_entry()) is True


def test_green_build_does_not_need_resume():
    e = _red_entry(last_verify_ok=True, phase="done", write_set=[])
    assert needs_resume_drive(e) is False


def test_writeless_build_does_not_need_resume():
    assert needs_resume_drive(_red_entry(write_set=[])) is False


def test_unverified_build_does_not_need_resume():
    # last_verify_ok None (never verified) is not a *red* build.
    assert needs_resume_drive(_red_entry(last_verify_ok=None)) is False


def test_none_entry_safe():
    assert needs_resume_drive(None) is False


# --- continue-intent detection --------------------------------------------


def test_continue_signals():
    for m in ["", "continue", "keep going", "proceed", "carry on", "go on", "next"]:
        assert looks_like_continue(m) is True, m


def test_new_request_is_not_continue():
    for m in ["add a login page", "what's the weather", "refactor the parser to use async"]:
        assert looks_like_continue(m) is False, m


# --- should_auto_resume_drive (continue AND red) --------------------------


def test_auto_resume_on_continue_over_red_build(tmp_path):
    save_ledger(_red_entry(project_path=str(tmp_path)), home=tmp_path)
    assert should_auto_resume_drive("continue", str(tmp_path), home=tmp_path) is True
    assert should_auto_resume_drive("", str(tmp_path), home=tmp_path) is True


def test_no_auto_resume_on_new_request(tmp_path):
    # A fresh, unrelated request must NOT hijack into the old build.
    save_ledger(_red_entry(project_path=str(tmp_path)), home=tmp_path)
    assert (
        should_auto_resume_drive("build me a snake game", str(tmp_path), home=tmp_path)
        is False
    )


def test_no_auto_resume_when_green(tmp_path):
    save_ledger(
        _red_entry(project_path=str(tmp_path), last_verify_ok=True, phase="done", write_set=[]),
        home=tmp_path,
    )
    assert should_auto_resume_drive("continue", str(tmp_path), home=tmp_path) is False


# --- resume_hint directs continuation to green ----------------------------


def test_resume_hint_directs_drive_on_red(tmp_path):
    save_ledger(_red_entry(project_path=str(tmp_path)), home=tmp_path)
    hint = resume_hint(str(tmp_path), home=tmp_path)
    assert "RED" in hint and "build_drive" in hint
    assert "do not" in hint.lower() and "green" in hint.lower()


# --- Multi-goal in one project must not clobber (goal-keyed ledger) --------


def test_two_goals_same_project_do_not_clobber(tmp_path):
    """Two DIFFERENT goals built in the same directory each keep their own
    entry — neither's resume state is lost (the ledger-per-project clobber)."""
    from remedy.core.build_ledger import BuildLedgerEntry, load_ledger, save_ledger

    proj = str(tmp_path)
    a = BuildLedgerEntry(
        project_path=proj, goal="add the export feature", phase="verify",
        last_verify_ok=False, write_set=["src/export.py"], write_steps=4,
    )
    b = BuildLedgerEntry(
        project_path=proj, goal="fix the login bug", phase="verify",
        last_verify_ok=False, write_set=["src/auth.py"], write_steps=2,
    )
    save_ledger(a, home=tmp_path)
    save_ledger(b, home=tmp_path)  # must NOT overwrite a
    # Each goal's own entry survives intact.
    ga = load_ledger(proj, home=tmp_path, goal="add the export feature")
    gb = load_ledger(proj, home=tmp_path, goal="fix the login bug")
    assert ga is not None and ga.write_set == ["src/export.py"] and ga.write_steps == 4
    assert gb is not None and gb.write_set == ["src/auth.py"]
    # Bare load returns the active (most-recently-saved) build.
    active = load_ledger(proj, home=tmp_path)
    assert active is not None and active.goal == "fix the login bug"


def test_same_goal_accumulates_one_entry(tmp_path):
    """Turns of the SAME build (same goal) merge into one entry (resume works),
    not a new entry each turn."""
    from remedy.core.build_ledger import BuildLedgerEntry, load_ledger, save_ledger

    proj = str(tmp_path)
    for steps in (1, 3, 5):
        save_ledger(
            BuildLedgerEntry(project_path=proj, goal="build X", phase="verify",
                             last_verify_ok=False, write_steps=steps),
            home=tmp_path,
        )
    # One build entry, latest state.
    e = load_ledger(proj, home=tmp_path, goal="build X")
    assert e is not None and e.write_steps == 5
    # Bare load resumes it.
    assert (load_ledger(proj, home=tmp_path) or BuildLedgerEntry()).goal == "build X"


def test_legacy_flat_ledger_still_reads(tmp_path):
    """A pre-existing flat ledger.json (old format) is read as one build so
    an in-progress build survives the format upgrade (resume not broken)."""
    import json

    from remedy.core.build_ledger import BuildLedgerEntry, ledger_path, load_ledger

    proj = str(tmp_path)
    p = ledger_path(proj, home=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    legacy = BuildLedgerEntry(
        project_path=proj, goal="legacy build", phase="verify",
        last_verify_ok=False, write_set=["a.py"], write_steps=7,
    ).to_dict()
    p.write_text(json.dumps(legacy), encoding="utf-8")  # OLD flat format
    e = load_ledger(proj, home=tmp_path)
    assert e is not None and e.goal == "legacy build" and e.write_steps == 7
    assert e.write_set == ["a.py"]


def test_active_prefers_unfinished_over_done(tmp_path):
    """Resume returns a RED build even if a DONE build is newer in the same
    project (do not resume a finished build over unfinished work)."""
    import time as _t

    from remedy.core.build_ledger import BuildLedgerEntry, load_ledger, save_ledger

    proj = str(tmp_path)
    save_ledger(
        BuildLedgerEntry(project_path=proj, goal="red one", phase="verify",
                         last_verify_ok=False, write_set=["r.py"], write_steps=2),
        home=tmp_path,
    )
    _t.sleep(0.01)
    save_ledger(
        BuildLedgerEntry(project_path=proj, goal="green one", phase="done",
                         last_verify_ok=True),
        home=tmp_path,
    )
    active = load_ledger(proj, home=tmp_path)
    assert active is not None and active.goal == "red one"  # the unfinished one
