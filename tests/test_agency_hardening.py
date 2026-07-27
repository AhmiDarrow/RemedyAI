"""Agency hardening: recovery, work roots, write locks, symbols, gitignore."""

from pathlib import Path

from remedy.core.react_policy import (
    batch_has_empty_search,
    tool_content_is_error,
)
from remedy.core.repo_search import format_hits, search_repo, symbol_search_patterns
from remedy.core.work_roots import discover_work_root, note_work_path


def test_tool_content_empty_search_is_error():
    assert tool_content_is_error('No matches for "foo" (engine=python).\nRecover: x')
    assert tool_content_is_error("Error [NOT_FOUND:file_read]: file not found: x")
    assert tool_content_is_error("exit_code=1\ncwd=C:\\\nstderr")
    assert not tool_content_is_error("Found 2 match(es) for 'x' (engine=rg):")


def test_batch_has_empty_search():
    msgs = [
        {"role": "tool", "content": 'No matches for "abc" (engine=python).\nhint'},
        {"role": "assistant", "content": "ok"},
    ]
    assert batch_has_empty_search(msgs)


def test_discover_work_root_godot(tmp_path: Path):
    (tmp_path / "project.godot").write_text(";g\n", encoding="utf-8")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "a.gd").write_text("x\n", encoding="utf-8")
    root = discover_work_root(scripts / "a.gd")
    assert root == tmp_path.resolve()


def test_note_work_path_on_runtime(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")

    class R:
        pass

    r = R()
    note_work_path(r, tmp_path / "package.json")
    assert getattr(r, "_work_roots")
    assert str(tmp_path.resolve()) in r._work_roots[0] or tmp_path.as_posix() in r._work_roots[
        0
    ].replace("\\", "/")


def test_symbol_patterns_nonempty():
    pats = symbol_search_patterns("WorldGenerator")
    assert any("class_name" in p for p in pats)


def test_python_search_gitignore_dir(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("secret_dir\n", encoding="utf-8")
    (tmp_path / "secret_dir").mkdir()
    (tmp_path / "secret_dir" / "x.py").write_text("FINDME\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text("FINDME\n", encoding="utf-8")
    hits, eng = search_repo(tmp_path, "FINDME", force_python=True)
    assert eng == "python"
    assert any("ok.py" in h.path for h in hits)
    assert not any("secret_dir" in h.path for h in hits)


def test_format_hits_empty_triggers_metrics_shape():
    msg = format_hits([], engine="python", pattern="zzz")
    assert "No matches" in msg
    assert "Recover" in msg
