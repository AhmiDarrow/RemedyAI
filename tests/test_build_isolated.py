"""Isolated overlay hops — failed units must not touch the live tree."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.build_isolated import OverlayRuntime, isolated_unit_hop, parallel_isolated_hops


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: (root / p) if not Path(p).is_absolute() else Path(p),
        config=SimpleNamespace(home_dir=root),
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
    )


def test_isolated_hop_does_not_merge_red(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    live = root / "widget.py"
    live.write_text("def helper():\n    return 1\n", encoding="utf-8")
    res = isolated_unit_hop(
        _rt(root),
        path="widget.py",
        symbol="helper",
        source="def helper(\n",  # syntax red
        use_llm=False,
    )
    assert res.get("ok") is False
    assert res.get("merged") is False
    assert live.read_text(encoding="utf-8") == "def helper():\n    return 1\n"


def test_isolated_hop_merges_green(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    live = root / "widget.py"
    live.write_text("def helper():\n    return 0\n", encoding="utf-8")
    res = isolated_unit_hop(
        _rt(root),
        path="widget.py",
        symbol="helper",
        source="def helper():\n    return 1\n",
        use_llm=False,
    )
    assert res.get("ok") is True
    assert res.get("merged") is True
    assert "return 1" in live.read_text(encoding="utf-8")


def test_isolated_hop_sibling_import_still_merges(tmp_path):
    """Overlay must not false-red a unit that imports a live sibling."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dep.py").write_text("VALUE = 1\n", encoding="utf-8")
    live = root / "widget.py"
    live.write_text("from dep import VALUE\n\ndef helper():\n    return VALUE\n", encoding="utf-8")
    res = isolated_unit_hop(
        _rt(root),
        path="widget.py",
        symbol="helper",
        source="from dep import VALUE\n\ndef helper():\n    return VALUE + 1\n",
        use_llm=False,
    )
    assert res.get("ok") is True, res
    assert res.get("merged") is True
    assert "VALUE + 1" in live.read_text(encoding="utf-8")


def test_parallel_hops_independent(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    hops = parallel_isolated_hops(
        _rt(root),
        [
            {"path": "a.py", "symbol": "a"},
            {"path": "b.py", "symbol": "b"},
        ],
        use_llm=False,
        max_workers=2,
    )
    assert len(hops) == 2
    assert all(h.get("ok") for h in hops)
    assert {h.get("path") for h in hops} == {"a.py", "b.py"}


def test_isolated_hop_refuses_parent_escape_path(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "widget.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    res = isolated_unit_hop(
        _rt(root),
        path="../outside.py",
        symbol="helper",
        source="def helper():\n    return 2\n",
        use_llm=False,
    )
    assert res.get("ok") is False
    assert not outside.exists()


def test_overlay_resolve_refuses_parent_escape(tmp_path):
    project = tmp_path / "proj"
    overlay = tmp_path / "overlay"
    project.mkdir()
    overlay.mkdir()
    shadow = OverlayRuntime(_rt(project), overlay, project)
    with pytest.raises(PermissionError, match="escapes overlay"):
        shadow.resolve_tool_path("../outside.py", for_write=True)
    assert not (tmp_path / "outside.py").exists()
