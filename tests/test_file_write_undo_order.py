"""The undo trail has to exist before the write that needs it.

``file_write`` / ``file_edit`` / ``file_edit_batch`` recorded the previous
content *after* ``write_text``, inside ``except Exception: pass``. By then the
old content was already gone from disk, so a failure to record it — a full
disk, a permission problem on the undo directory — lost the owner's work with
nobody told.
"""

from __future__ import annotations

import inspect

import pytest

from remedy.core.workspace_tools import files as files_mod


def test_the_helper_exists_and_is_documented():
    assert hasattr(files_mod, "_record_undo")
    assert "before" in (files_mod._record_undo.__doc__ or "").lower()


def _call_lines(fn_name: str) -> tuple[list[int], list[int]]:
    """(record_undo lines, write_text lines) inside one tool, by AST."""
    import ast
    import textwrap

    src = textwrap.dedent(inspect.getsource(files_mod.register_files_tools))
    tree = ast.parse(src)
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            target = node
            break
    assert target is not None, f"{fn_name} not found"

    records, writes = [], []
    for n in ast.walk(target):
        if not isinstance(n, ast.Call):
            continue
        name = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
        if name == "_record_undo":
            records.append(n.lineno)
        elif name == "write_text":
            writes.append(n.lineno)
    return records, writes


@pytest.mark.parametrize("tool", ["file_write", "file_edit", "file_edit_batch"])
def test_undo_is_recorded_before_the_write(tool):
    """In each tool the ``_record_undo`` call must come before the
    ``write_text`` it protects."""
    records, writes = _call_lines(tool)
    assert records, f"{tool} no longer records an undo entry"
    assert writes, f"{tool} no longer writes"
    assert min(records) < min(writes), (
        f"{tool} records the undo trail after the write — by then the previous "
        "content is already gone from disk"
    )


def test_a_failing_undo_log_does_not_block_the_write():
    """Refusing would take away a capability over a bookkeeping failure."""

    class _Runtime:
        config = None
        _session_id = "s1"
        _active_message_id = None

    warn = files_mod._record_undo(
        _Runtime(),
        sid="s1",
        target=None,  # unusable target: forces the log to fail
        previous="old",
        existed=True,
        new_size=3,
    )
    assert isinstance(warn, str)


def test_the_owner_is_told_when_undo_is_unavailable(monkeypatch):
    class _Boom:
        def __init__(self, *_a, **_kw):
            raise OSError("undo directory is read-only")

    monkeypatch.setattr("remedy.core.time_travel.SessionUndoLog", _Boom)

    class _Runtime:
        config = None
        _session_id = "s1"
        _active_message_id = None

    warn = files_mod._record_undo(
        _Runtime(), sid="s1", target=None, previous="old", existed=True, new_size=3
    )
    assert "no undo trail" in warn
    assert "read-only" in warn


def test_a_working_undo_log_adds_no_noise(tmp_path):
    class _Cfg:
        home_dir = str(tmp_path)

    class _Runtime:
        config = _Cfg()
        _session_id = "s1"
        _active_message_id = None

    warn = files_mod._record_undo(
        _Runtime(),
        sid="s1",
        target=tmp_path / "note.txt",
        previous="old",
        existed=True,
        new_size=3,
    )
    assert warn == ""
