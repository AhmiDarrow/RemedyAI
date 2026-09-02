"""Spec compiler — goal → locked BuildSpec DAG (machine-owned API surface).

Frontier B: the model never invents public APIs mid-flight. The machine
decompiles a goal into UnitSpecs with declare/requires/tests stubs.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_text_atomic
from remedy.core.builds.reducer import BuildSpec, Signature, UnitSpec
from remedy.core.relpath import norm_rel

_VERB_HINTS = re.compile(
    r"\b(implement|build|add|create|write|ship|scaffold|make|fix|fix)\b",
    re.I,
)
_MODULE_HINT = re.compile(
    r"\b(?:module|file|package)\s+[`'\"]?([A-Za-z_][\w./]*)[`'\"]?",
    re.I,
)
_FUNC_HINT = re.compile(
    r"\b(?:function|def|class|method|symbol|api)\s+[`'\"]?([A-Za-z_]\w*)[`'\"]?",
    re.I,
)
_NAMED = re.compile(r"[`'\"]([A-Za-z_][\w.]*)[`'\"]")


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", (s or "").strip())
    s = re.sub(r"_+", "_", s).strip("_").lower()
    return s or "unit"


def _safe_ident(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    if not s or s[0].isdigit():
        s = "f_" + s
    return s


def compile_goal_to_spec(
    goal: str,
    *,
    root: Path | str | None = None,
    default_package: str = "",
) -> dict[str, Any]:
    """Compile free-text goal into a locked BuildSpec JSON-friendly dict.

    Heuristic (deterministic, no LLM required):
    - Extract named modules/functions from quotes and keywords
    - Else invent a single unit from goal slug under src/ or package/
    - Each unit gets declare, behavior excerpt, and a failing TDD stub test
    """
    goal = (goal or "").strip()
    if not goal:
        return {"ok": False, "error": "empty goal", "spec": None, "units": []}

    units: list[UnitSpec] = []
    modules = _MODULE_HINT.findall(goal)
    funcs = _FUNC_HINT.findall(goal)
    quoted = _NAMED.findall(goal)

    # Prefer explicit paths ending in .py from goal
    py_paths = re.findall(r"([\w./-]+\.py)", goal)

    if py_paths:
        for i, p in enumerate(py_paths[:8]):
            rel = norm_rel(p)
            stem = Path(rel).stem
            named = i < len(funcs)
            sym = _safe_ident(funcs[i] if named else stem)
            # "fix the bug in utils.py" names a file, not a symbol: the stub
            # must only prove the module imports, never assert ``utils.utils``.
            units.append(_make_unit(rel, sym, goal, assert_symbol=named))
    elif modules:
        for i, m in enumerate(modules[:8]):
            m = m.replace("\\", "/").rstrip("/")
            rel = m.replace(".", "/") + ".py" if not m.endswith(".py") else m
            if default_package and not rel.startswith(("src/", default_package)):
                rel = f"{default_package.rstrip('/')}/{Path(rel).name}"
            sym = _safe_ident(funcs[i] if i < len(funcs) else Path(rel).stem)
            units.append(_make_unit(rel, sym, goal))
    elif funcs:
        pkg = default_package or "src"
        for f in funcs[:6]:
            sym = _safe_ident(f)
            rel = f"{pkg}/{sym}.py"
            units.append(_make_unit(rel, sym, goal))
    elif quoted:
        # use first 3 quoted tokens as symbols
        pkg = default_package or "src"
        for q in quoted[:4]:
            if q.lower() in {"true", "false", "none", "null"}:
                continue
            if "." in q and not q.endswith(".py"):
                rel = q.replace(".", "/") + ".py"
                sym = _safe_ident(q.split(".")[-1])
            else:
                sym = _safe_ident(q)
                rel = f"{pkg}/{sym}.py"
            units.append(_make_unit(rel, sym, goal))
            if len(units) >= 4:
                break
    else:
        slug = _slug(goal)[:40]
        sym = _safe_ident(slug.split("_")[0] if slug else "feature")
        if len(sym) < 3:
            sym = "feature"
        pkg = default_package or "src"
        rel = f"{pkg}/{slug[:32]}.py"
        units.append(_make_unit(rel, sym, goal))

    # Wire requires: later units depend on earlier declared symbols (simple chain)
    for i in range(1, len(units)):
        prev = units[i - 1]
        if prev.declare:
            units[i].requires = list(
                dict.fromkeys(units[i].requires + [prev.declare[0].symbol])
            )

    # Enrich from disk index if root given
    if root:
        with contextlib.suppress(Exception):
            from remedy.core.build_symbol_index import build_symbol_index

            idx = build_symbol_index(root)
            for u in units:
                for r in list(u.requires):
                    if not idx.lookup(r):
                        # drop impossible requires from chain if not on disk and not declared earlier
                        declared = {s.symbol for uu in units for s in uu.declare}
                        if r not in declared:
                            u.requires = [x for x in u.requires if x != r]

    spec = BuildSpec(units=units)
    ordered = spec.order()
    public_units = [_unit_public(u) for u in ordered]
    return {
        "ok": True,
        "goal": goal[:400],
        "units": public_units,
        "spec": {"units": public_units},
        "lock": _spec_lock(public_units),
        "tdd_first": True,
        "message": (
            f"Locked BuildSpec: {len(public_units)} unit(s). "
            "Implement bodies only; do not rename declare symbols without recompile."
        ),
    }


def _make_unit(path: str, symbol: str, goal: str, *, assert_symbol: bool = True) -> UnitSpec:
    path = norm_rel(path)
    symbol = _safe_ident(symbol)
    check = (
        f"    assert hasattr(mod, {symbol!r}), {symbol!r} + ' missing'\n"
        if assert_symbol
        else "    assert mod is not None\n"
    )
    test_src = (
        f"import importlib\n"
        f"import pytest\n\n"
        f"def test_{symbol}_exists():\n"
        f"    # TDD red: module must import"
        f"{' and expose ' + symbol if assert_symbol else ''}\n"
        f"    mod_name = {path!r}.replace('/', '.').removesuffix('.py')\n"
        f"    if mod_name.startswith('src.'):\n"
        f"        mod_name = mod_name[4:]\n"
        f"    mod = importlib.import_module(mod_name)\n"
        f"{check}"
    )
    return UnitSpec(
        id=symbol,
        path=path,
        declare=[Signature(symbol=symbol, defines_path=path)],
        behavior=goal[:400],
        tests=test_src,
    )


def _unit_public(u: UnitSpec) -> dict[str, Any]:
    return {
        "id": u.id,
        "path": u.path,
        "symbol": u.declare[0].symbol if u.declare else u.id,
        "declare": [
            {"symbol": s.symbol, "params": s.params, "returns": s.returns, "defines_path": s.defines_path}
            for s in u.declare
        ],
        "requires": list(u.requires),
        "imports": list(u.imports),
        "behavior": u.behavior,
        "tests": u.tests,
    }


def _spec_lock(units: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        [{"path": u["path"], "symbol": u.get("symbol"), "requires": u.get("requires")} for u in units],
        sort_keys=True,
    )
    import hashlib

    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def units_from_public(units: list[dict[str, Any]]) -> list[UnitSpec]:
    out: list[UnitSpec] = []
    for u in units or []:
        if not isinstance(u, dict):
            continue
        path = str(u.get("path") or "").replace("\\", "/")
        if not path:
            continue
        sym = str(u.get("symbol") or (u.get("declare") or [{}])[0].get("symbol") or Path(path).stem)
        declare_raw = u.get("declare") or [{"symbol": sym, "defines_path": path}]
        declare = [
            Signature(
                symbol=str(d.get("symbol") or sym),
                params=str(d.get("params") or ""),
                returns=str(d.get("returns") or ""),
                defines_path=str(d.get("defines_path") or path),
            )
            for d in declare_raw
            if isinstance(d, dict)
        ]
        req = u.get("requires") or []
        if isinstance(req, str):
            req = [req]
        imps = u.get("imports") or []
        if isinstance(imps, str):
            imps = [imps]
        out.append(
            UnitSpec(
                id=str(u.get("id") or sym),
                path=path,
                declare=declare or [Signature(symbol=sym, defines_path=path)],
                requires=list(req),
                imports=list(imps),
                behavior=str(u.get("behavior") or "")[:800],
                tests=str(u.get("tests") or ""),
            )
        )
    return out


def save_locked_spec(root: Path | str, compiled: dict[str, Any]) -> Path:
    root = Path(root)
    d = root / ".remedy-build"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "locked_spec.json"
    write_text_atomic(path, json.dumps(compiled, indent=2)[:100_000])
    return path


def load_locked_spec(root: Path | str) -> dict[str, Any] | None:
    path = Path(root) / ".remedy-build" / "locked_spec.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
