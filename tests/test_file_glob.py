"""Jail-aware file_glob engine."""

from __future__ import annotations

from pathlib import Path

from remedy.core.file_glob import format_glob_hits, glob_files, match_glob


def test_match_glob_basename_recursive():
    assert match_glob("src/foo.py", "*.py")
    assert match_glob("a/b/c.py", "*.py")
    assert not match_glob("src/foo.ts", "*.py")


def test_match_glob_double_star():
    assert match_glob("src/pkg/x.ts", "src/**/*.ts")
    assert match_glob("src/x.ts", "src/**/*.ts")
    assert not match_glob("lib/x.ts", "src/**/*.ts")


def test_glob_files_skips_junk(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "src" / "app.ts").write_text("x\n", encoding="utf-8")
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "index.py").write_text("no\n", encoding="utf-8")
    hits = glob_files(tmp_path, "*.py")
    assert "src/app.py" in hits
    assert not any("node_modules" in h for h in hits)
    assert "src/app.ts" not in hits


def test_format_empty_has_recovery():
    msg = format_glob_hits([], pattern="*.zig")
    assert "0 hits" in msg
    assert "list_dir" in msg
