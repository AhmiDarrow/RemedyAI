"""On-PC symbol map."""

from __future__ import annotations

from pathlib import Path

from remedy.core.code_map import build_code_map, format_code_map


def test_code_map_finds_python_defs(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "class Shop:\n    def add_milk(self):\n        return 1\n",
        encoding="utf-8",
    )
    hits = build_code_map(tmp_path)
    names = {h.name for h in hits}
    assert "Shop" in names
    assert "add_milk" in names
    filtered = build_code_map(tmp_path, query="Shop")
    assert any(h.name == "Shop" for h in filtered)
    assert format_code_map(hits).startswith("code_map:")


def test_code_map_skips_node_modules(tmp_path: Path):
    junk = tmp_path / "node_modules" / "pkg"
    junk.mkdir(parents=True)
    (junk / "index.js").write_text("function hidden() {}\n", encoding="utf-8")
    keep = tmp_path / "src"
    keep.mkdir()
    (keep / "ok.py").write_text("def visible():\n    pass\n", encoding="utf-8")
    hits = build_code_map(tmp_path)
    assert any(h.name == "visible" for h in hits)
    assert not any(h.name == "hidden" for h in hits)
