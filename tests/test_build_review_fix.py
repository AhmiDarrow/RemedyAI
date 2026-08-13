"""Review-fix second pass."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.build_engine import BuildTurnState
from remedy.core.build_review_fix import (
    collect_diff_findings,
    maybe_review_fix,
    review_fix_pass,
)


def _rt(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        effective_project_path=lambda: root,
        resolve_tool_path=lambda p, **k: root / p,
        config=SimpleNamespace(home_dir=root),
    )


def test_finds_bare_except_and_todo(tmp_path):
    (tmp_path / "mod.py").write_text(
        "def f():\n    try:\n        return 1\n    except:\n        pass\n# TODO: finish\n",
        encoding="utf-8",
    )
    findings = collect_diff_findings(_rt(tmp_path), ["mod.py"])
    kinds = {f["kind"] for f in findings}
    assert "bare_except" in kinds
    assert "todo" in kinds


def test_review_fix_pass_ok_on_clean(tmp_path):
    (tmp_path / "clean.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_clean.py").write_text(
        "def test_f():\n    from clean import f\n    assert f() == 1\n",
        encoding="utf-8",
    )
    res = review_fix_pass(_rt(tmp_path), ["clean.py"], use_llm=False)
    assert res["errors"] == 0


def test_maybe_review_fix_once():
    st = BuildTurnState(active=True, write_set=["a.py"])
    rt = SimpleNamespace(
        effective_project_path=lambda: None,
        _build_turn=st,
    )
    # no project → empty findings, but still marks ran
    first = maybe_review_fix(rt, st, use_llm=False)
    assert first is not None
    assert st.review_fix_ran is True
    assert maybe_review_fix(rt, st, use_llm=False) is None
