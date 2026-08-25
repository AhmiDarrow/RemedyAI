"""Guard the react_loop name-binding contract.

The step modules (``loop_prelude`` / ``loop_http`` / ``loop_round`` /
``loop_finals`` / ``loop_steps``) do not import their dependencies directly:
they resolve them from ``remedy.core.react_loop.loop`` at call time, so tests
that patch ``loop.<name>`` keep working. Two lists in ``loop_bindings`` are the
contract for that -- ``LOOP_BIND_NAMES`` (callables/constants re-exported by
``loop``) and ``STATE_NAMES`` (mutable turn state mirrored on bag ``s``).

Nothing else checks that contract. ``loop.py`` carries a blanket ``F401``
ignore, so a deleted re-export is invisible to ruff; every binding is ``Any``,
so it is invisible to mypy. These tests are the check. They are pure AST -- no
import of ``remedy``, so they cannot touch a live Remedy home.
"""

from __future__ import annotations

import ast
import pathlib
import re

PKG = pathlib.Path(__file__).resolve().parents[1] / "src" / "remedy" / "core" / "react_loop"
STEP_MODULES = ("loop_prelude", "loop_http", "loop_round", "loop_finals", "loop_steps")


def _canonical() -> tuple[list[str], list[str]]:
    tree = ast.parse((PKG / "loop_bindings.py").read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Tuple)
        ):
            found[node.target.id] = [e.value for e in node.value.elts]
    return found["LOOP_BIND_NAMES"], found["STATE_NAMES"]


LOOP_BIND_NAMES, STATE_NAMES = _canonical()


def _module_level_names(path: pathlib.Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


def test_loop_module_still_exports_every_bound_name() -> None:
    """A dropped re-export in ``loop.py`` is an AttributeError mid-stream-turn.

    ``loop.py`` is ``F401``-ignored precisely because these imports look unused
    (they are read via ``getattr``), so ruff cannot catch the deletion.
    """
    bound = _module_level_names(PKG / "loop.py")
    missing = [n for n in LOOP_BIND_NAMES if n not in bound]
    assert not missing, f"loop.py no longer binds: {missing}"


def _keyed_pairs(src: str, bag: str) -> list[tuple[str, str]]:
    """Return ``target = bag[key]`` pairs, in source order."""
    pat = re.compile(rf'^\s*([A-Za-z_]\w*) = {re.escape(bag)}\["([^"]+)"\]', re.M)
    return [(m.group(1), m.group(2)) for m in pat.finditer(src)]


def test_binding_is_keyed_never_positional() -> None:
    """Positional unpacking of these lists silently mis-binds on reorder.

    An 83-way / 116-way tuple unpack replicated across five modules has no
    static check: swap two same-typed names in the canonical list and every
    call site keeps parsing, keeps type-checking, and binds the wrong values.
    """
    offenders = []
    for name in STEP_MODULES:
        src = (PKG / f"{name}.py").read_text(encoding="utf-8")
        if re.search(r"\)\s*=\s*bind_loop_tuple\(\)", src):
            offenders.append(f"{name}.py: positional unpack of bind_loop_tuple()")
        if re.search(r"\)\s*=\s*tuple\(\s*_st\d*\[n\] for n in STATE_NAMES\s*\)", src):
            offenders.append(f"{name}.py: positional unpack of STATE_NAMES")
    assert not offenders, "; ".join(offenders)


def test_every_step_module_binds_the_whole_contract() -> None:
    """Each keyed block must cover its canonical list exactly, key == target."""
    problems = []
    for name in STEP_MODULES:
        src = (PKG / f"{name}.py").read_text(encoding="utf-8")

        lb = _keyed_pairs(src, "_lb")
        if lb:
            mismatched = [(t, k) for t, k in lb if t != k]
            if mismatched:
                problems.append(f"{name}.py: target/key mismatch {mismatched[:3]}")
            keys = {k for _, k in lb}
            if keys != set(LOOP_BIND_NAMES):
                missing = sorted(set(LOOP_BIND_NAMES) - keys)
                extra = sorted(keys - set(LOOP_BIND_NAMES))
                problems.append(f"{name}.py: bind block missing={missing} extra={extra}")

        for bag in ("_st", "_st2"):
            st = _keyed_pairs(src, bag)
            if not st:
                continue
            mismatched = [(t, k) for t, k in st if t != k]
            if mismatched:
                problems.append(f"{name}.py[{bag}]: target/key mismatch {mismatched[:3]}")
            keys = {k for _, k in st}
            if keys != set(STATE_NAMES):
                missing = sorted(set(STATE_NAMES) - keys)
                extra = sorted(keys - set(STATE_NAMES))
                problems.append(f"{name}.py[{bag}]: state block missing={missing} extra={extra}")

    assert not problems, "; ".join(problems)


def test_pull_bag_nonlocal_covers_all_state() -> None:
    """Guard ``_pull_bag``, which rebinds state after a delegate ran.

    A name left out of its ``nonlocal`` list silently keeps a stale value for
    the rest of the turn.
    """
    tree = ast.parse((PKG / "loop_steps.py").read_text(encoding="utf-8"))
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_pull_bag":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Nonlocal):
                    declared.update(inner.names)
    assert declared, "_pull_bag not found -- update this guard"
    missing = sorted(set(STATE_NAMES) - declared)
    assert not missing, f"_pull_bag nonlocal omits {missing}"


def test_no_state_name_is_write_only() -> None:
    """Require every ``STATE_NAMES`` entry to be assigned somewhere.

    ``unpack_state`` defaults a missing attribute to ``None`` rather than
    raising, so a mistyped or dead entry would read ``None`` forever instead
    of failing.
    """
    sources = [(PKG / f"{n}.py").read_text(encoding="utf-8") for n in STEP_MODULES]
    sources.append((PKG / "loop.py").read_text(encoding="utf-8"))
    assigned: set[str] = set()
    for src in sources:
        for node in ast.walk(ast.parse(src)):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign | ast.AugAssign | ast.For):
                targets = [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        assigned.add(sub.id)
    missing = sorted(set(STATE_NAMES) - assigned)
    assert not missing, f"STATE_NAMES entries nothing ever assigns: {missing}"
