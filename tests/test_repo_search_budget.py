"""repo_search never walks a huge root on the event loop, and the walk is bounded."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from remedy.core import repo_search as rs


def _tree(tmp_path: Path, n: int = 30) -> Path:
    for i in range(n):
        (tmp_path / f"f{i}.py").write_text(f"def hello_{i}():\n    return 'NEEDLE'\n")
    return tmp_path


def test_huge_root_detection(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert rs.is_huge_root(tmp_path)
    sub = tmp_path / "proj"
    sub.mkdir()
    assert not rs.is_huge_root(sub)
    assert rs.is_huge_root(Path(tmp_path.anchor))


def test_huge_root_without_rg_is_refused_not_walked(monkeypatch, tmp_path):
    _tree(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    import remedy.core.rg_binary as rgb

    monkeypatch.setattr(rgb, "find_rg", lambda *a, **k: (None, "none"))
    monkeypatch.setattr(rgb, "schedule_ensure_rg", lambda *a, **k: None)

    walked = []
    real = rs._search_python

    def _spy(*a, **k):
        walked.append(1)
        return real(*a, **k)

    monkeypatch.setattr(rs, "_search_python", _spy)
    hits, engine = rs.search_repo(tmp_path, "NEEDLE")
    assert hits == []
    assert engine.startswith("error: ")
    assert "ripgrep" in engine and "path=" in engine
    assert not walked, "a huge root must never be walked in pure Python"
    out = rs.format_hits(hits, engine=engine, pattern="NEEDLE")
    assert out.startswith("Error:") and "specific project directory" in out


def test_huge_root_skips_os_dirs_in_python_walk(monkeypatch, tmp_path):
    _tree(tmp_path, 3)
    for d in ("AppData", "Windows", "node_modules", "_MEI1234", ".cargo"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.py").write_text("NEEDLE\n")
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "y.py").write_text("NEEDLE\n")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    hits, note = rs._search_python(
        tmp_path, tmp_path, "NEEDLE", glob=None, max_matches=100, case_insensitive=False
    )
    paths = {h.path for h in hits}
    assert "proj/y.py" in paths
    assert not any(p.split("/")[0] in {"AppData", "Windows", "node_modules", "_MEI1234", ".cargo"} for p in paths)


def test_python_walk_honours_time_budget(monkeypatch, tmp_path):
    _tree(tmp_path, 200)
    # Clock that jumps past the deadline on the second read.
    ticks = iter([0.0, 0.0, 1000.0, 1000.0, 1000.0] + [1000.0] * 1000)
    monkeypatch.setattr(rs.time, "monotonic", lambda: next(ticks))
    hits, note = rs._search_python(
        tmp_path,
        tmp_path,
        "NEEDLE",
        glob=None,
        max_matches=500,
        case_insensitive=False,
        time_budget_s=1.0,
    )
    assert note == "truncated:time-budget"
    assert len(hits) < 200
    out = rs.format_hits(hits, engine="python+" + note, pattern="NEEDLE")
    assert "time budget exhausted" in out


def test_python_walk_file_cap_is_reported(monkeypatch, tmp_path):
    _tree(tmp_path, 12)
    monkeypatch.setattr(rs, "_MAX_PYTHON_FILES", 5)
    hits, note = rs._search_python(
        tmp_path, tmp_path, "NEEDLE", glob=None, max_matches=500, case_insensitive=False
    )
    assert note == "truncated:file-cap"
    assert 0 < len(hits) <= 6
    hits2, engine = rs.search_repo(tmp_path, "NEEDLE", force_python=True, max_matches=500)
    assert engine == "python+truncated:file-cap"
    assert "partial" in rs.format_hits(hits2, engine=engine, pattern="NEEDLE")


@pytest.mark.asyncio
async def test_async_search_keeps_the_loop_free(monkeypatch, tmp_path):
    _tree(tmp_path, 5)
    real = rs.search_repo

    def _slow(*a, **k):
        time.sleep(0.3)
        return real(*a, **k)

    monkeypatch.setattr(rs, "search_repo", _slow)
    ticks = 0

    async def _heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    hb = asyncio.create_task(_heartbeat())
    hits, engine = await rs.search_repo_async(tmp_path, "NEEDLE", force_python=True)
    hb.cancel()
    assert hits
    assert ticks >= 5, "the event loop was blocked while the search ran"
