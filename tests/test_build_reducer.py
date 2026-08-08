"""Build reducer — prove a weak stateless model converges on a project
via minimal dependency-closure context + a real falsification oracle."""

from __future__ import annotations

from remedy.core.builds import (
    BuildSpec,
    Signature,
    SymbolRegistry,
    UnitSpec,
    build_project,
    run_oracle,
)


def _build_correct(unit: UnitSpec) -> str:
    """A 'good' edit: imports + a cross-file consumer + all declared defs."""
    lines: list[str] = []
    for imp in unit.imports:
        lines.append(f"import {imp}")
    if unit.requires:
        deps = ", ".join(unit.requires)
        lines.append(f"def _uses({deps}):")
        lines.append("    return " + " + ".join(unit.requires))
        lines.append("")
    for sig in unit.declare:
        ret = f" -> {sig.returns}" if sig.returns else ""
        lines.append(f"def {sig.symbol}({sig.params}){ret}:")
        lines.append("    return 0")
    return "\n".join(lines)


def _remove_def(src: str, symbol: str) -> str:
    out: list[str] = []
    skip = False
    for ln in src.splitlines():
        if ln.startswith(f"def {symbol}("):
            skip = True
            continue
        if skip and (ln.startswith("def ") or ln.startswith("import ") or ln.startswith("class ")):
            skip = False
        if not skip:
            out.append(ln)
    return "\n".join(out)


def _calculator_spec() -> BuildSpec:
    return BuildSpec(
        units=[
            UnitSpec(
                id="math",
                path="mathutil.py",
                imports=["math"],
                declare=[
                    Signature("clamp", "v: float, lo: float, hi: float", "float", "mathutil.py"),
                ],
                requires=["math"],
                behavior="clamp v into [lo, hi]",
            ),
            UnitSpec(
                id="calc",
                path="calc.py",
                imports=["mathutil"],
                declare=[
                    Signature("add", "a: int, b: int", "int", "calc.py"),
                    Signature("avg", "xs: list", "float", "calc.py"),
                ],
                requires=["clamp"],
                behavior="add returns a+b; avg uses clamp",
            ),
        ]
    )


def test_order_puts_dependencies_first():
    order = _calculator_spec().order()
    assert order[0].id == "math"  # defines clamp, which calc requires
    assert order[1].id == "calc"


def test_minimal_context_excludes_unrelated_project():
    spec = _calculator_spec()
    reg = SymbolRegistry()
    for u in spec.order():
        for s in u.declare:
            reg.declare(s)
    ctx = reg.closure_text(spec.units[1], budget=3000)  # calc
    assert "calc.py" in ctx
    assert "clamp" in ctx            # dependency signature present
    assert "mathutil.py" in ctx      # dependency's defining file noted
    assert "avg" in ctx              # own contract
    # The context is the contract + the dep's signature only — never a full file.
    assert len(ctx) < 1000


def test_weak_model_converges_via_missing_definition_repair():
    """A model that drops a declared symbol on first pass is caught by the
    oracle (missing definition) and converges on the repair pass."""
    def weak(unit, closure, errors):
        src = _build_correct(unit)
        if errors:
            return src  # oracle error in context → fix
        return _remove_def(src, unit.declare[-1].symbol)  # first pass is weak

    result = build_project(_calculator_spec(), weak, context_budget=2000)
    assert result.ok is True
    assert result.repaired >= 1  # falsification drove at least one repair
    # Both files are real, compilable Python defining their declared symbols.
    for path in ("mathutil.py", "calc.py"):
        assert path in result.files
        assert run_oracle(spec_unit(_calculator_spec(), path), result.files[path]) == []
    # The calc unit's body references its dependency symbol (cross-file
    # consistency came from the registry, not from seeing the other file).
    reg = SymbolRegistry()
    for u in _calculator_spec().order():
        for s in u.declare:
            reg.declare(s)
    assert "clamp" in reg.references(result.files["calc.py"])


def test_weak_model_converges_via_syntax_error_repair():
    """A model that emits uncompilable Python is caught by compile() and the
    loop converges once the error vector is fed back."""

    def weak(unit, closure, errors):
        src = _build_correct(unit)
        if errors:
            return src
        return src + "\n    break"  # invalid indentation → SyntaxError

    result = build_project(_calculator_spec(), weak, context_budget=2000)
    assert result.ok is True
    assert result.repaired >= 1


def test_budget_caps_context_for_each_unit():
    seen_big: list[int] = []

    def spy(unit, closure, errors):
        seen_big.append(len(closure))
        return _build_correct(unit)

    build_project(_calculator_spec(), spy, context_budget=120)
    assert all(n <= 120 for n in seen_big)  # every unit's context stayed tiny


def spec_unit(spec: BuildSpec, path: str) -> UnitSpec:
    for u in spec.units:
        if u.path == path:
            return u
    raise KeyError(path)
