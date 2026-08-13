"""Machine-owned build_drive: spec → TDD → hops, auto-implement/repair."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_drive import (
    drive_build,
    goal_wants_machine_implement,
    maybe_auto_implement,
    maybe_auto_repair,
    review_write_set,
    should_use_live_llm,
)
from remedy.core.build_engine import BuildTurnState, begin_build_turn


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


def test_should_use_live_llm_false_under_pytest():
    assert should_use_live_llm(None) is False


def test_goal_wants_machine_implement():
    assert goal_wants_machine_implement("implement parse_csv in module io_utils.py")
    assert goal_wants_machine_implement("fix the bug in auth")
    assert goal_wants_machine_implement("review the auth module and fix bugs")
    assert goal_wants_machine_implement("review the auth module") is False
    assert goal_wants_machine_implement("explain how sessions work") is False


def test_drive_build_tdd_without_llm(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "tests").mkdir()
    rt = _rt(root)
    res = drive_build(
        rt,
        goal="implement function greet in module hello.py",
        use_llm=False,
    )
    assert res.get("compiled", {}).get("ok") is True
    tdd = res.get("tdd") or {}
    written = tdd.get("written") or []
    assert written, res
    assert any(Path(root / w).is_file() for w in written)
    assert rt._build_turn is not None
    assert rt._build_turn.auto_drive_ran is True
    todos = getattr(rt, "_build_todos", None) or []
    assert todos


def test_drive_build_hops_existing_source(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "hello.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    rt = _rt(root)
    res = drive_build(
        rt,
        goal="implement function greet in module hello.py",
        use_llm=False,
    )
    hops = res.get("hops") or []
    assert hops
    assert any(h.get("path") == "hello.py" for h in hops)
    review = res.get("review") or {}
    assert "hello.py" in (review.get("paths") or []) or review.get("message")


def test_maybe_auto_implement_after_explore_thrash(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    rt = _rt(root)
    st = begin_build_turn(rt, "implement function greet in module hello.py")
    assert st is not None
    st.serial_explore_streak = st.max_serial_explore
    st.write_steps = 0
    driven = maybe_auto_implement(rt, st, use_llm=False)
    assert driven is not None
    assert st.auto_drive_ran is True
    # Second call is a no-op
    assert maybe_auto_implement(rt, st, use_llm=False) is None


def test_maybe_auto_implement_skips_review_only(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    rt = _rt(root)
    st = BuildTurnState(
        active=True,
        goal="review the auth module",
        serial_explore_streak=5,
        max_serial_explore=2,
    )
    rt._build_turn = st
    assert maybe_auto_implement(rt, st, use_llm=False) is None
    assert st.auto_drive_ran is False


def test_maybe_auto_repair_hops_existing_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "widget.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    rt = _rt(root)
    st = begin_build_turn(rt, "implement helper")
    assert st is not None
    st.last_verify_ok = False
    st.write_set = ["widget.py"]
    st.last_error_vector = {"path_lines": ["widget.py:1"], "failing_nodes": []}
    hopped = maybe_auto_repair(rt, st, use_llm=False)
    assert hopped is not None
    assert hopped.get("ran", 0) >= 1
    assert st.auto_repair_cycles == 1
    # Cap
    st.max_auto_repair_cycles = 1
    st.last_verify_ok = False
    capped = maybe_auto_repair(rt, st, use_llm=False)
    assert capped is not None
    assert capped.get("capped") is True


def test_review_write_set_maps_tests(tmp_path):
    root = tmp_path
    (root / "mylib.py").write_text("x=1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_mylib.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )
    rev = review_write_set(_rt(root), ["mylib.py"])
    assert any("test_mylib" in t for t in (rev.get("tests") or []))
