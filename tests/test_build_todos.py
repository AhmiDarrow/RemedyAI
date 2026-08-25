"""Turn-local build todos + DONE gate."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState, build_blocks_final_answer
from remedy.core.build_todos import (
    format_todos_block,
    load_todos,
    open_todo_count,
    seed_drive_todos,
    take_todos_event,
    todos_event_token,
    upsert_todos,
)


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        config=SimpleNamespace(home_dir=root),
        _build_turn=None,
    )


def test_upsert_merge_and_replace(tmp_path):
    rt = _rt(tmp_path)
    a = upsert_todos(
        rt,
        [
            {"id": "1", "content": "explore", "status": "pending"},
            {"id": "2", "content": "implement", "status": "in_progress"},
        ],
        merge=False,
    )
    assert [t.id for t in a] == ["1", "2"]
    b = upsert_todos(
        rt,
        [{"id": "2", "content": "implement greet", "status": "completed"}],
        merge=True,
    )
    assert len(b) == 2
    assert next(t for t in b if t.id == "2").status == "completed"
    assert next(t for t in b if t.id == "2").content == "implement greet"
    c = upsert_todos(rt, [{"id": "x", "content": "only", "status": "pending"}], merge=False)
    assert len(c) == 1
    assert (tmp_path / ".remedy-build" / "todos.json").is_file()
    loaded = load_todos(rt)
    assert loaded[0].id == "x"


def test_verify_rows_are_not_feature_work():
    from remedy.core.build_todos import TodoItem, open_feature_todo_count, todo_is_verify_row

    assert todo_is_verify_row("npm test green")
    assert todo_is_verify_row("run tests")
    assert not todo_is_verify_row("Verify critical fixes")
    assert not todo_is_verify_row("Audio: HPSS/lead emphasis + stronger pitch path")
    items = [
        TodoItem(id="1", content="Audio: HPSS", status="in_progress"),
        TodoItem(id="11", content="npm test green", status="pending"),
        TodoItem(id="6", content="README refresh", status="completed"),
    ]
    assert open_feature_todo_count(items) == 1


def test_open_count_and_format():
    rt = SimpleNamespace(effective_project_path=lambda: None, config=None)
    items = upsert_todos(
        rt,
        [
            {"id": "a", "content": "one", "status": "pending"},
            {"id": "b", "content": "two", "status": "completed"},
            {"id": "c", "content": "three", "status": "in_progress"},
        ],
        merge=False,
    )
    assert open_todo_count(items) == 2
    block = format_todos_block(items)
    assert "[ ]" in block and "[x]" in block and "[>]" in block
    assert "2 open" in block
    closed = upsert_todos(
        rt,
        [
            {"id": "a", "content": "one", "status": "completed"},
            {"id": "b", "content": "two", "status": "cancelled"},
        ],
        merge=False,
    )
    assert open_todo_count(closed) == 0
    assert format_todos_block(closed) == ""


def test_closed_checklist_clears_disk(tmp_path):
    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [{"id": "1", "content": "explore", "status": "pending"}],
        merge=False,
    )
    fp = tmp_path / ".remedy-build" / "todos.json"
    assert fp.is_file()
    upsert_todos(
        rt,
        [{"id": "1", "content": "explore", "status": "completed"}],
        merge=False,
    )
    assert not fp.is_file()
    assert load_todos(rt) == []


def test_seed_drive_todos(tmp_path):
    rt = _rt(tmp_path)
    items = seed_drive_todos(
        rt,
        units=[{"path": "hello.py", "symbol": "greet"}],
        goal="implement greet",
    )
    ids = [t.id for t in items]
    assert "spec" in ids and "tdd" in ids and "verify" in ids
    assert any("greet" in t.content for t in items)


def test_todos_event_is_session_scoped():
    """Sibling streams must not pop each other's @@todos token."""
    rt = SimpleNamespace(
        effective_project_path=lambda: None,
        config=None,
        _session_id="sess-a",
    )
    upsert_todos(
        rt,
        [{"id": "a", "content": "A checklist", "status": "pending"}],
        merge=False,
    )
    rt._session_id = "sess-b"
    upsert_todos(
        rt,
        [{"id": "b", "content": "B checklist", "status": "in_progress"}],
        merge=False,
    )
    b = take_todos_event(rt, session_id="sess-b")
    a = take_todos_event(rt, session_id="sess-a")
    assert a and "A checklist" in a
    assert b and "B checklist" in b
    assert take_todos_event(rt, session_id="sess-a") is None
    assert take_todos_event(rt, session_id="sess-b") is None


def test_mem_todos_do_not_leak_across_sessions():
    rt = SimpleNamespace(
        effective_project_path=lambda: None,
        config=None,
        _session_id="sess-a",
    )
    upsert_todos(
        rt,
        [{"id": "a", "content": "only A", "status": "pending"}],
        merge=False,
    )
    rt._session_id = "sess-b"
    assert load_todos(rt) == []


def test_todos_event_token_and_take():
    rt = SimpleNamespace(effective_project_path=lambda: None, config=None)
    items = upsert_todos(
        rt,
        [{"id": "a", "content": "read AGENTS.md", "status": "in_progress"}],
        merge=False,
    )
    tok = take_todos_event(rt)
    assert tok and tok.startswith("@@todos:")
    payload = json.loads(tok[len("@@todos:") :])
    assert payload["open"] == 1
    assert payload["todos"][0]["content"] == "read AGENTS.md"
    assert take_todos_event(rt) is None
    ev = todos_event_token(items)
    assert '"in_progress"' in ev


def test_open_todos_block_done_after_writes():
    st = BuildTurnState(
        active=True,
        write_steps=2,
        last_verify_ok=True,
        write_set=[],
        open_todo_count=2,
    )
    assert build_blocks_final_answer(st) is True
    st.open_todo_count = 0
    assert build_blocks_final_answer(st) is False


def test_done_summary_blocks_on_open_list_not_on_unverified_writes():
    from remedy.core.build_engine import build_blocks_done_summary

    open_list = BuildTurnState(active=True, write_steps=2, open_todo_count=1)
    open_list.last_verify_ok = True
    assert build_blocks_done_summary(open_list) is True
    unverified = BuildTurnState(active=True, write_steps=2, open_todo_count=0)
    unverified.last_verify_ok = None
    assert build_blocks_done_summary(unverified) is False


def test_open_todos_block_a_scout_only_done():
    """A checklist with no writes yet is not 'say go' — it is unfinished work."""
    st = BuildTurnState(active=True, write_steps=0, open_todo_count=3)
    assert build_blocks_final_answer(st) is True
    from remedy.core.build_engine import unfinished_green_gate_message

    msg = unfinished_green_gate_message(st)
    assert "say go" in msg["content"].lower() or "TODO GATE" in msg["content"]


def test_done_phase_does_not_fake_complete_unrelated_rows(tmp_path):
    """Session 765c: phase=done + green tests crossed off work that was not done."""
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [
            {"id": "a", "content": "Verify critical fixes", "status": "in_progress"},
            {"id": "b", "content": "double-check export margins", "status": "pending"},
            {"id": "c", "content": "npm test green", "status": "pending"},
        ],
        merge=False,
    )
    state = BuildTurnState(goal="fix issues 1-10", project_path=str(tmp_path))
    state.phase = "done"
    state.last_verify_ok = True
    state.write_steps = 2
    items = sync_todos_with_build(rt, state)
    by_id = {t.id: t for t in items}
    assert by_id["a"].status == "in_progress"
    assert by_id["b"].status == "pending"
    assert by_id["c"].status == "completed"
    assert open_todo_count(items) == 2


def test_done_without_green_verify_keeps_open_rows(tmp_path):
    """phase="done" but verify NOT green → do not claim heuristic-miss rows
    done (the checklist must not report unverified work as accomplished)."""
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [{"id": "a", "content": "RemedyPDF update polish pass", "status": "in_progress"}],
        merge=False,
    )
    state = BuildTurnState(goal="remedypdf update", project_path=str(tmp_path))
    state.phase = "done"
    state.last_verify_ok = False
    items = sync_todos_with_build(rt, state)
    assert open_todo_count(items) == 1


def test_filename_stem_completes_matching_todo(tmp_path):
    """``audioToMidi.ts`` must check off a row that names audioToMidi.

    Session 765c: the Build list stayed on 'Read audioToMidi…' while she
    edited that file — the matcher required the extension in the row text.
    """
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [
            {"id": "1", "content": "Read audioToMidi, theory fretting, tabEdit", "status": "in_progress"},
            {"id": "2", "content": "Patch audioToMidi quantize", "status": "pending"},
            {"id": "3", "content": "Melody-band + octave repair", "status": "pending"},
            {"id": "5", "content": "npm test green", "status": "pending"},
        ],
        merge=False,
    )
    state = BuildTurnState(goal="do all of those things", project_path=str(tmp_path))
    state.write_steps = 1
    state.write_set = [str(tmp_path / "src" / "lib" / "audioToMidi.ts")]
    state.paths_touched = list(state.write_set)
    items = sync_todos_with_build(rt, state)
    by_id = {t.id: t for t in items}
    assert by_id["1"].status == "completed"  # "Read …" prefix + filename stem
    assert by_id["2"].status == "completed"  # filename stem only, no "read"
    # Next unmatched open row becomes in_progress so the live list moves.
    assert by_id["3"].status == "in_progress"
    assert by_id["5"].status == "pending"


def test_verify_green_completes_the_test_row_not_the_whole_list(tmp_path):
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [
            {"id": "2", "content": "Melody-band + octave repair", "status": "in_progress"},
            {"id": "5", "content": "npm test green", "status": "pending"},
        ],
        merge=False,
    )
    state = BuildTurnState(goal="g", project_path=str(tmp_path))
    state.phase = "implement"
    state.last_verify_ok = True
    items = sync_todos_with_build(rt, state)
    by_id = {t.id: t for t in items}
    assert by_id["5"].status == "completed"
    assert by_id["2"].status == "in_progress"


def test_sync_with_runtime_queues_a_live_todos_event(tmp_path):
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [
            {"id": "1", "content": "Read audioToMidi helpers", "status": "in_progress"},
            {"id": "2", "content": "Melody-band pass", "status": "pending"},
        ],
        merge=False,
    )
    take_todos_event(rt)  # drop the upsert event
    state = BuildTurnState(goal="g", project_path=str(tmp_path))
    state.write_steps = 2
    state.write_set = ["src/lib/audioToMidi.ts"]
    sync_todos_with_build(rt, state)
    tok = take_todos_event(rt)
    assert tok and tok.startswith("@@todos:")
    assert "completed" in tok
    assert "Melody-band" in tok


def test_unfinished_build_keeps_open_rows(tmp_path):
    """Anything short of phase="done" must NOT sweep the checklist."""
    from remedy.core.build_todos import sync_todos_with_build

    rt = _rt(tmp_path)
    upsert_todos(
        rt,
        [{"id": "a", "content": "polish the export pass", "status": "pending"}],
        merge=False,
    )
    state = BuildTurnState(goal="g", project_path=str(tmp_path))
    state.phase = "verify"
    items = sync_todos_with_build(rt, state)
    assert open_todo_count(items) == 1
