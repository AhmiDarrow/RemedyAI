"""repo_search: language-agnostic python path, absolute paths, rg parse."""

from pathlib import Path

from remedy.core.repo_search import (
    EMPTY_SEARCH_HINT,
    _parse_rg_line,
    format_hits,
    search_repo,
)
from remedy.core.text_files import is_probably_text, should_search_file


def test_search_python_finds_pattern(tmp_path: Path):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nope\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "def hello", max_matches=10, force_python=True)
    assert engine == "python"
    assert any("hello" in h.text for h in hits)
    assert "a.py" in format_hits(hits, engine=engine, pattern="def hello")


def test_search_no_match(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "DOES_NOT_EXIST_XYZ", force_python=True)
    assert hits == []
    msg = format_hits(hits, engine=engine, pattern="DOES_NOT_EXIST_XYZ")
    assert "No matches" in msg
    assert "Recover" in msg or EMPTY_SEARCH_HINT[:20] in msg


def test_search_gdscript_without_allowlist(tmp_path: Path):
    """GDScript must be searchable without rg and without extension allowlist."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "WorldGenerator.gd").write_text(
        "class_name WorldGenerator\nextends Node\nfunc build():\n    pass\n",
        encoding="utf-8",
    )
    hits, engine = search_repo(
        tmp_path, "class_name WorldGenerator", max_matches=10, force_python=True
    )
    assert engine == "python"
    assert len(hits) >= 1
    assert any("WorldGenerator" in h.path or "WorldGenerator" in h.text for h in hits)


def test_search_extensionless_makefile(tmp_path: Path):
    (tmp_path / "Makefile").write_text("all:\n\techo hi\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "echo hi", force_python=True)
    assert engine == "python"
    assert any("Makefile" in h.path for h in hits)


def test_search_skips_binary_png(tmp_path: Path):
    # Minimal PNG-like binary with NUL
    (tmp_path / "sprite.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00binary")
    (tmp_path / "note.txt").write_text("sprite marker text\n", encoding="utf-8")
    hits, engine = search_repo(tmp_path, "sprite", force_python=True)
    assert engine == "python"
    assert all(not h.path.endswith(".png") for h in hits)
    assert any("note.txt" in h.path for h in hits)


def test_search_absolute_path_outside_focus(tmp_path: Path):
    """Absolute path search works when default root is elsewhere (no project cage)."""
    focus = tmp_path / "homeish"
    other = tmp_path / "FallenEarth"
    focus.mkdir()
    other.mkdir()
    (other / "main.gd").write_text("func ready():\n    pass\n", encoding="utf-8")
    hits, engine = search_repo(
        focus,
        "func ready",
        path=str(other),
        force_python=True,
    )
    assert engine == "python"
    assert len(hits) >= 1


def test_is_probably_text_and_should_search(tmp_path: Path):
    t = tmp_path / "a.gd"
    t.write_text("extends Node\n", encoding="utf-8")
    assert is_probably_text(t)
    assert should_search_file(t)
    b = tmp_path / "x.bin"
    b.write_bytes(b"\x00\x01\x02\x03" * 100)
    assert not is_probably_text(b)
    assert not should_search_file(b)


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
