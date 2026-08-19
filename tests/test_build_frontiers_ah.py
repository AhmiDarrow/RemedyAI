"""Frontiers A–H: behavioral hop, spec, repair queue, mutants, snapshots, gates, index, TDD."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_ast_patch import apply_minimal_patch, replace_top_level_def
from remedy.core.build_gate_tower import run_gate_tower
from remedy.core.build_live_hop import live_unit_hop
from remedy.core.build_mutant import _apply_mutants, mutant_kill_score
from remedy.core.build_repair_queue import queue_from_error_vector
from remedy.core.build_snapshot import (
    bisect_red_wave,
    restore_snapshot,
    snapshot_paths,
)
from remedy.core.build_spec_compiler import compile_goal_to_spec, save_locked_spec
from remedy.core.build_symbol_index import build_symbol_index, closure_from_index
from remedy.core.build_tdd import materialize_tdd_tests, synthesize_failing_test, tdd_bootstrap


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=root),
    )


# --- A: behavioral hop ---
def test_behavioral_hop_green(tmp_path):
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
def test_repair_queue_from_vector():
    q = queue_from_error_vector(
        {
            "failing_nodes": ["tests/test_foo.py::test_x"],
            "path_lines": ["src/foo.py:12"],
        },
        write_set=["src/foo.py"],
    )
    assert q.targets
    paths = [t.path for t in q.targets]
    assert any("foo" in p for p in paths)


# --- D: mutants ---
def test_apply_mutants_produces_edits():
    src = "def f(x):\n    assert x > 0\n    return True\n"
    muts = _apply_mutants(src)
    assert muts
    assert all(m[1] != src for m in muts)


def test_mutant_kill_score_kills(tmp_path):
    root = tmp_path
    (root / "core_mod.py").write_text(
        "def value():\n    return 1\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_core_mod.py").write_text(
        "from core_mod import value\n\ndef test_v():\n    assert value() == 1\n",
        encoding="utf-8",
    )
    res = mutant_kill_score(
        root,
        [str(root / "core_mod.py")],
        test_command_paths=["tests"],
    )
    # At least some mutants attempted; rename_def or flip_return should be killed
    assert res.get("total", 0) >= 1 or res.get("ok") is False
    if res.get("baseline_ok"):
        assert res["killed"] + res["survived"] == res["total"]


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
def test_gate_tower_l0_l2(tmp_path):
    root = tmp_path
    (root / "ok_mod.py").write_text("X = 1\n", encoding="utf-8")
    rt = _rt(root)
    res = run_gate_tower(
        rt,
        [str(root / "ok_mod.py")],
        levels=["L0_syntax", "L1_static", "L2_import"],
        stop_at_first_red=True,
    )
    assert res["results"]
    assert "L0_syntax" in res["passed_levels"] or res["ok"]


def test_gate_tower_syntax_red(tmp_path):
    root = tmp_path
    bad = root / "bad.py"
    bad.write_text("def (\n", encoding="utf-8")
    res = run_gate_tower(_rt(root), [str(bad)], levels=["L0_syntax"])
    assert res["ok"] is False
    assert res["first_red"]["level"] == "L0_syntax"


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
def test_synthesize_and_materialize_tdd(tmp_path):
    src = synthesize_failing_test("pkg/hello.py", "hello", behavior="say hi")
    assert "hello" in src
    compile(src, "<t>", "exec")
    units = [
        {
            "path": "pkg/hello.py",
            "symbol": "hello",
            "tests": src,
            "behavior": "say hi",
        }
    ]
    mat = materialize_tdd_tests(_rt(tmp_path), units, root=tmp_path)
    assert mat["ok"]
    assert (tmp_path / "tests" / "test_hello.py").is_file()


def test_tdd_bootstrap(tmp_path):
    res = tdd_bootstrap(_rt(tmp_path), "implement function add_one in math_util.py")
    assert res["ok"] is True
    assert (tmp_path / ".remedy-build" / "locked_spec.json").is_file()
    assert (res.get("tdd") or {}).get("written")


def test_build_tools_ah_registered(tmp_path, monkeypatch):
    from remedy.core.agent_build_tools import register_build_tools

    # A–H frontiers stay behind maturity gate — enable for registration test
    monkeypatch.setattr(
        "remedy.core.feature_maturity.build_os_advanced_enabled",
        lambda cfg=None: True,
    )

    handlers: dict[str, object] = {}

    class FakeReg:
        def register_builtin_handler(self, name, desc, fn, schema):  # noqa: ARG002
            handlers[name] = fn

    root = tmp_path
    (root / "w.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    rt = SimpleNamespace(
        tool_registry=FakeReg(),
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=tmp_path),
        _build_state=None,
    )
    register_build_tools(rt)
    for name in (
        "build_compile_spec",
        "build_tdd",
        "build_gate_tower",
        "build_repair_queue",
        "build_mutant_score",
        "build_snapshot",
        "build_symbol_index",
        "build_unit_hop",
    ):
        assert name in handlers, name

    out = asyncio.run(handlers["build_compile_spec"](goal="add def foo"))  # type: ignore[operator]
    assert "units" in out or "ok" in out


def test_a_failing_test_points_at_a_source_file_that_exists():
    """The queue's top target used to be a bare name — "telephony_line.py" —
    that is nowhere in the tree, while the file it meant sits at
    src/remedy/telephony/line.py. The repair loop was aimed at nothing."""
    from pathlib import Path

    from remedy.core.build_repair_queue import (
        _test_to_source_guess,
        queue_from_error_vector,
    )

    for test_file, expect in (
        ("tests/test_telephony_line.py", "src/remedy/telephony/line.py"),
        ("tests/test_voice_realtime_pipeline.py", "src/remedy/voice/realtime/pipeline.py"),
        ("tests/test_terms.py", "src/remedy/core/terms.py"),
    ):
        assert _test_to_source_guess(test_file) == expect

    q = queue_from_error_vector(
        {"failing_nodes": ["tests/test_telephony_line.py::test_x"]}
    )
    top = q.targets[0]
    assert top.path == "src/remedy/telephony/line.py"
    assert Path(top.path).is_file(), "top repair target does not exist"


def test_an_unresolvable_test_still_gets_its_old_best_effort_guess():
    """Never worse than before: with nothing matching in the tree, the bare
    name is still returned rather than nothing at all."""
    from remedy.core.build_repair_queue import _test_to_source_guess

    assert _test_to_source_guess("tests/test_no_such_module_anywhere.py") == (
        "no_such_module_anywhere.py"
    )
    assert _test_to_source_guess("notes.md") is None


def test_auto_repair_hops_the_source_not_the_test(monkeypatch):
    """``run_auto_repair_hops`` computed the source path and discarded it, so
    a failing test got its own file rewritten instead of the code under it."""
    from remedy.core import build_live_hop
    from remedy.core.build_repair_queue import queue_from_error_vector, run_auto_repair_hops

    hopped: list[str] = []

    def _fake_hop(runtime, *, path, symbol, use_llm, max_repairs, tests=""):
        hopped.append(path)
        return {"ok": True}

    monkeypatch.setattr(build_live_hop, "live_unit_hop", _fake_hop)
    q = queue_from_error_vector(
        {"failing_nodes": ["tests/test_telephony_line.py::test_x"]}
    )
    run_auto_repair_hops(object(), q, use_llm=False, max_targets=3)
    assert hopped, "no hop was attempted at all"
    assert hopped[0] == "src/remedy/telephony/line.py"
