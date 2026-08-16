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
