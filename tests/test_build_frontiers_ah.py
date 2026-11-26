"""Frontiers A–H: behavioral hop, spec, repair queue, mutants, snapshots, gates, index, TDD."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.build_ast_patch import apply_minimal_patch, replace_top_level_def
from remedy.core.build_live_hop import live_unit_hop
from remedy.core.build_snapshot import (
    bisect_red_wave,
    restore_snapshot,
    snapshot_paths,
)
from remedy.core.build_spec_compiler import compile_goal_to_spec, save_locked_spec
from remedy.core.build_symbol_index import build_symbol_index, closure_from_index


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=root),
    )


# --- A: behavioral hop ---
def test_behavioral_hop_green(tmp_path):
    from tests.conftest import core_available

    if not core_available():
        pytest.skip("remedy_core not built in this environment")

    root = tmp_path
    tests = (
        "def test_ok():\n"
        "    from widget import helper\n"
        "    assert helper() == 1\n"
    )
    # Put module on path for pytest oracle temp tree — PytestOracle materializes state
    res = live_unit_hop(
        _rt(root),
        path="widget.py",
        symbol="helper",
        source="def helper():\n    return 1\n",
        tests=tests,
        use_llm=False,
        require_behavior=True,
    )
    assert res["ok"] is True
    assert res.get("behavioral") is True
    assert (root / "widget.py").is_file()


def test_behavioral_hop_red(tmp_path):
    from tests.conftest import core_available

    if not core_available():
        pytest.skip("remedy_core not built in this environment")

    tests = (
        "def test_ok():\n"
        "    from widget import helper\n"
        "    assert helper() == 99\n"
    )
    res = live_unit_hop(
        _rt(tmp_path),
        path="widget.py",
        symbol="helper",
        source="def helper():\n    return 1\n",
        tests=tests,
        use_llm=False,
    )
    assert res["ok"] is False
    assert any("test" in e.lower() for e in (res.get("errors") or []))


# --- B: spec compiler ---
def test_compile_goal_to_spec():
    c = compile_goal_to_spec("implement function parse_csv in module io_utils.py")
    assert c["ok"] is True
    assert c["lock"]
    assert any("parse" in str(u.get("symbol", "")).lower() or "io_utils" in u.get("path", "")
               for u in c["units"])


def test_compile_and_save(tmp_path):
    c = compile_goal_to_spec("add def greet")
    p = save_locked_spec(tmp_path, c)
    assert p.is_file()


# --- C: repair queue ---


# --- D: mutants ---


# --- E: snapshots ---
def test_snapshot_restore_bisect(tmp_path):
    root = tmp_path
    f = root / "a.py"
    f.write_text("v = 1\n", encoding="utf-8")
    s1 = snapshot_paths(root, ["a.py"], note="v1")
    f.write_text("v = 2\n", encoding="utf-8")
    s2 = snapshot_paths(root, ["a.py"], note="v2")
    assert s1["snap_id"] != s2["snap_id"]
    r = restore_snapshot(root, s1["snap_id"])
    assert r["ok"] is True
    assert f.read_text(encoding="utf-8") == "v = 1\n"
    b = bisect_red_wave(root)
    assert b.get("ok") is True or "snap" in b


# --- F: gate tower ---


# --- G: symbol index + AST patch ---
def test_symbol_index(tmp_path):
    (tmp_path / "lib.py").write_text("def alpha(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "use.py").write_text("from lib import alpha\nalpha(1)\n", encoding="utf-8")
    idx = build_symbol_index(tmp_path)
    assert idx.lookup("alpha")
    ctx = closure_from_index(idx, path="use.py", requires=["alpha"])
    assert "alpha" in ctx


def test_ast_minimal_patch():
    base = "def a():\n    return 1\n\ndef b():\n    return 2\n"
    pr = replace_top_level_def(base, "a", "def a():\n    return 99\n")
    assert pr.ok
    assert "return 99" in pr.source
    assert "def b" in pr.source
    pr2 = apply_minimal_patch(base, symbol="b", patch_source="def b():\n    return 0\n")
    assert pr2.ok and pr2.method == "ast_replace"


# --- H: TDD ---


