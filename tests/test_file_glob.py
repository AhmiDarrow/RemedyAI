"""Jail-aware file_glob engine."""

from __future__ import annotations

import time
from pathlib import Path

from remedy.core.file_glob import (
    format_glob_hits,
    glob_files,
    glob_search,
    match_glob,
)


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


def test_format_truncated_says_so():
    msg = format_glob_hits(["a.py"], pattern="*.py", truncated=True)
    assert "truncated" in msg.lower()


def test_glob_search_stops_when_budget_elapses(tmp_path: Path, monkeypatch):
    """A home-sized walk must not occupy the worker until os.walk finishes."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "two.py").write_text("x\n", encoding="utf-8")
    ticks = {"n": 0}

    def fake_monotonic() -> float:
        ticks["n"] += 1
        # First call is the deadline; later calls during os.walk are past it.
        return 0.0 if ticks["n"] == 1 else 1000.0

    monkeypatch.setattr(time, "monotonic", fake_monotonic)
    result = glob_search(tmp_path, "*.py", time_budget_s=0.01)
    assert result.truncated is True


def test_glob_search_skips_huge_root_noise(tmp_path: Path, monkeypatch):
    junk = tmp_path / "AppData" / "nested"
    junk.mkdir(parents=True)
    (junk / "secret.py").write_text("x\n", encoding="utf-8")
    keep = tmp_path / "src"
    keep.mkdir()
    (keep / "app.py").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr("remedy.core.file_glob.is_huge_root", lambda _p: True)
    result = glob_search(tmp_path, "*.py")
    assert "src/app.py" in result.hits
    assert not any("AppData" in h for h in result.hits)
    assert result.truncated is False
