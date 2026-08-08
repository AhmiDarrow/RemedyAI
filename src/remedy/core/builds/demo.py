"""Runnable demo project for the build reducer — a tiny `order` package with
behavioral tests, plus a deterministic correct mock model (offline) and a
driver shared by the CLI (`python -m remedy.core.builds`)."""

from __future__ import annotations

from pathlib import Path

from remedy.core.builds.reducer import (
    BuildResult,
    BuildSpec,
    PytestOracle,
    Signature,
    UnitSpec,
    build_project,
    demo_weak_model,
    local_llama_model,
    run_project_tests,
)


def demo_project_spec() -> BuildSpec:
    return BuildSpec(
        units=[
            UnitSpec(
                id="pkg",
                path="order/__init__.py",
                imports=[],
                declare=[],
                behavior="package marker (can be empty)",
            ),
            UnitSpec(
                id="currency",
                path="order/currency.py",
                imports=[],
                declare=[
                    Signature("to_cents", "amount: float", "int", "order/currency.py"),
                    Signature("from_cents", "cents: int", "float", "order/currency.py"),
                ],
                requires=[],
                behavior="to_cents rounds amount*100 to an int; from_cents divides by 100",
                tests=(
                    "from order.currency import to_cents, from_cents\n"
                    "def test_to_cents():\n"
                    "    assert to_cents(1.5) == 150\n"
                    "    assert to_cents(0.10) == 10\n"
                    "def test_from_cents():\n"
                    "    assert from_cents(150) == 1.5\n"
                    "    assert from_cents(10) == 0.10\n"
                ),
            ),
            UnitSpec(
                id="report",
                path="order/report.py",
                imports=["order.currency"],
                declare=[
                    Signature(
                        "total_cents", "items: list[tuple[str, float]]", "int", "order/report.py"
                    ),
                    Signature("avg_amount", "cents: list[int]", "float", "order/report.py"),
                ],
                requires=["to_cents", "from_cents"],
                behavior=(
                    "total_cents sums item prices and returns to_cents of the total; "
                    "avg_amount returns from_cents of the mean of cents (0.0 when empty)"
                ),
                tests=(
                    "from order.report import total_cents, avg_amount\n"
                    "def test_total():\n"
                    "    assert total_cents([('a', 1.0), ('b', 2.5)]) == 350\n"
                    "def test_avg():\n"
                    "    assert avg_amount([100, 200, 300]) == 2.0\n"
                    "    assert avg_amount([]) == 0.0\n"
                ),
            ),
        ]
    )


def demo_correct_model(unit: UnitSpec, closure: str, errors: object) -> str:
    """Deterministic stateless worker that emits correct bodies for the demo.

    Stands in for a capable local model so the offline demo converges to green
    tests; it reads nothing but the closure's contract (minimal context).
    """
    if unit.id == "pkg":
        return ""
    if unit.id == "currency":
        return (
            "def to_cents(amount: float) -> int:\n"
            "    return int(round(amount * 100))\n"
            "\n"
            "def from_cents(cents: int) -> float:\n"
            "    return cents / 100.0\n"
        )
    # report
    return (
        "from order.currency import to_cents, from_cents\n"
        "\n"
        "def total_cents(items):\n"
        "    return to_cents(sum(price for _, price in items))\n"
        "\n"
        "def avg_amount(cents):\n"
        "    if not cents:\n"
        "        return 0.0\n"
        "    return from_cents(int(round(sum(cents) / len(cents))))\n"
    )


def run_demo(
    *,
    mode: str = "mock",
    out_dir: str | Path | None = None,
    context_budget: int = 2_000,
    max_repairs: int = 5,
    defect_rate: float = 0.35,
    base_url: str | None = None,
) -> BuildResult:
    """Run the demo build. mode: mock | weak | local."""
    spec = demo_project_spec()
    oracle = PytestOracle(Path(out_dir) if out_dir else Path.home() / ".remedy-builds-demo")
    if mode == "local":
        model = local_llama_model(base_url=base_url, context_budget=context_budget)
    elif mode == "weak":
        model = demo_weak_model(defect_rate=defect_rate, repair=demo_correct_model)
    else:
        model = demo_correct_model
    result = build_project(
        spec,
        model,
        oracle=oracle,
        context_budget=context_budget,
        max_repairs=max_repairs,
    )
    return result


def _print_demo(result: BuildResult, out_dir: Path) -> None:
    print(result.summary())
    print()
    print("Project tree:")
    for rel, src in sorted(result.files.items()):
        head = src.strip().splitlines()
        print(f"  {rel}  ({len(src)} bytes, {len(head)} lines)")
    print()
    if result.ok:
        ok, summary, fails = run_project_tests(result.files, out_dir)
        print("pytest result:", "PASS" if ok else "FAIL")
        if not ok:
            print("\n".join(fails[:10]))
        else:
            print(summary.strip())
    else:
        print("Build did not fully verify (see failures above).")


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tempfile

    p = argparse.ArgumentParser(prog="python -m remedy.core.builds", description="Build reducer demo")
    p.add_argument("--mode", choices=["mock", "weak", "local"], default="mock")
    p.add_argument("--out", default=None, help="output dir (default: temp)")
    p.add_argument("--budget", type=int, default=2_000, help="per-unit context budget in tokens")
    p.add_argument("--max-repairs", type=int, default=5, help="max extra attempts per unit")
    p.add_argument("--defect-rate", type=float, default=0.35, help="weak mock defect rate")
    p.add_argument("--base-url", default=None, help="llama-server base URL (local mode)")
    args = p.parse_args(argv)

    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="remedy-build-demo-"))
    result = run_demo(
        mode=args.mode,
        out_dir=out,
        context_budget=args.budget,
        max_repairs=args.max_repairs,
        defect_rate=args.defect_rate,
        base_url=args.base_url,
    )
    _print_demo(result, out)
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
