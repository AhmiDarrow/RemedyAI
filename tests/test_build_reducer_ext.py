"""Build reducer reliability — termination, repair caps, and an end-to-end
project that passes real pytest."""

from __future__ import annotations

from pathlib import Path

from remedy.core.builds import (
    BuildSpec,
    PytestOracle,
    Signature,
    UnitSpec,
    build_project,
    demo_model,
    demo_weak_model,
    extract_markdown_fence,
    materialize,
    run_oracle,
    run_project_tests,
)
from remedy.core.builds.demo import demo_correct_model, demo_project_spec, run_demo


def _calc_spec() -> BuildSpec:
    return BuildSpec(
        units=[
            UnitSpec(
                id="math",
                path="mathutil.py",
                imports=[],
                declare=[Signature("clamp", "v, lo, hi", "float", "mathutil.py")],
                requires=[],
            ),
            UnitSpec(
                id="calc",
                path="calc.py",
                imports=["mathutil"],
                declare=[Signature("add", "a, b", "int", "calc.py")],
                requires=["clamp"],
            ),
        ]
    )


def test_always_bad_model_terminates_and_reports_failure():
    def never_fix(unit, closure, errors):
        return "def broken(:\n"  # always a SyntaxError; never repairs

    result = build_project(_calc_spec(), never_fix, context_budget=2000, max_repairs=2)
    assert result.ok is False
    assert len(result.failures) >= 1
    assert result.failures[0].unit_id == "math"
    assert result.failures[0].attempts == 3  # 1 initial + 2 repairs, then dropped
    # Loop still built the other unit (repair cap does not starve siblings).
    assert "calc.py" in result.files


def test_repair_cap_bounds_per_unit_and_reports_all():
    def sometimes_fix(unit, closure, errors):
        # never fixes calc, always fixes math
        if unit.id == "calc" or not errors:
            return "def broken(:\n"
        return "def clamp(v, lo, hi):\n    return max(lo, min(v, hi))\n"

    result = build_project(_calc_spec(), sometimes_fix, context_budget=2000, max_repairs=2)
    assert result.ok is False
    assert [f.unit_id for f in result.failures] == ["calc"]
    assert result.files["mathutil.py"].startswith("def clamp")  # math converged


def test_mock_model_builds_multifile_project():
    result = build_project(_calc_spec(), demo_model, context_budget=2000)
    assert result.ok is True
    assert "mathutil.py" in result.files and "calc.py" in result.files
    for path in result.files:
        assert run_oracle(spec_unit(_calc_spec(), path), result.files[path]) == []


def test_demo_end_to_end_mock_passes_real_pytest(tmp_path: Path):
    result = run_demo(mode="mock", out_dir=tmp_path, context_budget=2000)
    assert result.ok is True
    ok, summary, fails = run_project_tests(result.files, tmp_path)
    assert ok, summary
    assert fails == []


def test_demo_weak_converges_under_structural_oracle():
    result = build_project(
        demo_project_spec(),
        demo_weak_model(defect_rate=0.9, seed=7),
        context_budget=2000,
    )
    assert result.ok is True
    assert result.repaired >= 1


def test_demo_weak_converges_behaviorally_with_test_oracle(tmp_path: Path):
    """A weak model (buggy first pass, repairs from the oracle error) still ends
    with a project whose real pytest passes — behavior, not just structure."""
    weak = demo_weak_model(defect_rate=0.9, seed=11, repair=demo_correct_model)
    result = build_project(
        demo_project_spec(),
        weak,
        oracle=PytestOracle(tmp_path),
        context_budget=2000,
    )
    assert result.ok is True
    assert result.repaired >= 1
    ok, summary, fails = run_project_tests(result.files, tmp_path)
    assert ok, summary
    assert fails == []


def test_extract_markdown_fence_strips_code_blocks():
    raw = 'Here is the file:\n```python\ndef f():\n    return 1\n```\nDone.'
    assert extract_markdown_fence(raw) == "def f():\n    return 1"
    assert extract_markdown_fence("def g():\n    return 2") == "def g():\n    return 2"


def test_materialize_and_run_project_tests(tmp_path: Path):
    files = {
        "pkg/__init__.py": "",
        "pkg/calc.py": "def add(a, b):\n    return a + b\n",
        "tests/test_calc.py": (
            "from pkg.calc import add\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        ),
    }
    root = materialize(files, tmp_path)
    ok, _summary, fails = run_project_tests(files, root)
    assert ok is True
    assert fails == []


def spec_unit(spec: BuildSpec, path: str) -> UnitSpec:
    for u in spec.units:
        if u.path == path:
            return u
    raise KeyError(path)
