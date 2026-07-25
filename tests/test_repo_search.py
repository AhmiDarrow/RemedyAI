"""repo_search pure-python path."""

from pathlib import Path

from remedy.core.repo_search import _parse_rg_line, format_hits, search_repo


def test_search_python_finds_pattern(tmp_path: Path):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nope\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "def hello", max_matches=10)
    assert engine in ("rg", "python")
    assert any("hello" in h.text for h in hits)
    assert "a.py" in format_hits(hits, engine=engine, pattern="def hello")


def test_search_no_match(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "DOES_NOT_EXIST_XYZ")
    assert hits == []
    assert "No matches" in format_hits(hits, engine=engine, pattern="x")


def test_parse_rg_line_windows_drive():
    p, line, text = _parse_rg_line(r"C:\Users\x\file.py:42: hello world")
    assert p.endswith("file.py")
    assert line == 42
    assert "hello" in text


def test_parse_rg_line_unix():
    p, line, text = _parse_rg_line("src/foo.py:7:def bar():")
    assert p == "src/foo.py"
    assert line == 7
    assert "bar" in text
