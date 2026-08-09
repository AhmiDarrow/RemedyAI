"""TDD-as-OS — machine writes failing tests first, then implement hops.

Frontier H: red tests exist before any implement hop. DONE = those nodes
green (+ optional mutant bar).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def materialize_tdd_tests(
    runtime: Any,
    units: list[dict[str, Any]],
    *,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Write unit test stubs to disk (expected RED until implement)."""
    try:
        root = Path(runtime.effective_project_path()) if root is None else Path(root)
        if root.is_file():
            root = root.parent
    except Exception as e:
        return {"ok": False, "error": str(e), "written": []}

    written: list[str] = []
    tests_dir = root / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    init = tests_dir / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")

    for u in units or []:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "").replace("\\", "/")
        sym = str(u.get("symbol") or Path(path).stem)
        tests_src = str(u.get("tests") or "")
        if not tests_src:
            tests_src = synthesize_failing_test(path, sym, behavior=str(u.get("behavior") or ""))
        test_rel = f"tests/test_{_safe(sym)}.py"
        dest = root / test_rel
        # resolve write jail if available
        try:
            dest = Path(runtime.resolve_tool_path(test_rel, for_write=True))
        except Exception:
            try:
                dest = Path(runtime.resolve_tool_path(test_rel))
            except Exception:
                dest = root / test_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(tests_src, encoding="utf-8")
        written.append(test_rel)
        u["test_path"] = test_rel

    return {
        "ok": bool(written),
        "written": written,
        "phase": "tdd_red",
        "message": (
            f"TDD: wrote {len(written)} failing test file(s). "
            "Implement units until these go green. Do not weaken asserts."
        ),
    }


def synthesize_failing_test(module_path: str, symbol: str, *, behavior: str = "") -> str:
    """Deterministic failing test that imports module and checks symbol."""
    mod = module_path.replace("\\", "/").removesuffix(".py")
    parts = [p for p in mod.split("/") if p and p != "."]
    if parts and parts[0] == "src":
        parts = parts[1:]
    mod_name = ".".join(parts) if parts else symbol
    beh = (behavior or "").replace('"""', "'")[:200]
    return (
        '"""Auto-generated TDD oracle — must go green for DONE."""\n'
        "import importlib\n\n"
        f"# behavior: {beh}\n\n"
        f"def test_{symbol}_importable():\n"
        f"    mod = importlib.import_module({mod_name!r})\n"
        f"    assert hasattr(mod, {symbol!r}), "
        f"'missing {symbol} on {mod_name}'\n"
    )


def _safe(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return s or "unit"


def tdd_bootstrap(
    runtime: Any,
    goal: str,
    *,
    use_llm_implement: bool = False,
) -> dict[str, Any]:
    """Full H path: compile spec → write tests → optional implement hops."""
    from remedy.core.build_spec_compiler import (
        compile_goal_to_spec,
        save_locked_spec,
    )

    try:
        root = Path(runtime.effective_project_path())
        if root.is_file():
            root = root.parent
    except Exception as e:
        return {"ok": False, "error": f"no project: {e}"}

    compiled = compile_goal_to_spec(goal, root=root)
    if not compiled.get("ok"):
        return compiled

    save_locked_spec(root, compiled)
    mat = materialize_tdd_tests(runtime, compiled.get("units") or [], root=root)

    implement_results: list[dict[str, Any]] = []
    if use_llm_implement:
        from remedy.core.build_live_hop import live_unit_hop

        for u in compiled.get("units") or []:
            path = str(u.get("path") or "")
            sym = str(u.get("symbol") or "")
            tests = str(u.get("tests") or "")
            res = live_unit_hop(
                runtime,
                path=path,
                symbol=sym,
                behavior=str(u.get("behavior") or goal)[:400],
                use_llm=True,
                max_repairs=3,
                tests=tests,
            )
            implement_results.append(res)

    return {
        "ok": True,
        "compiled": compiled,
        "tdd": mat,
        "implement": implement_results,
        "lock": compiled.get("lock"),
        "message": mat.get("message", "")
        + (
            f" Implemented {sum(1 for r in implement_results if r.get('ok'))}/"
            f"{len(implement_results)} units."
            if implement_results
            else " Next: build_unit_hop / build_live_project against locked_spec."
        ),
    }


def format_tdd_message(result: dict[str, Any]) -> dict[str, str]:
    lines = [
        "[Build engine · TDD-AS-OS]",
        result.get("message") or "",
    ]
    tdd = result.get("tdd") or {}
    for w in (tdd.get("written") or [])[:12]:
        lines.append(f"  · red test: {w}")
    lock = result.get("lock") or (result.get("compiled") or {}).get("lock")
    if lock:
        lines.append(f"spec_lock={lock}")
    lines.append("Order: tests(red) → implement hops → gate tower → mutant score → DONE.")
    return {"role": "user", "content": "\n".join(lines)}
