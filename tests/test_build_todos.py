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
