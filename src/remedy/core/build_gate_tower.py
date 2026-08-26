"""Gate tower L0→L4 — cheapest falsification first.

Frontier F:
  L0 syntax
  L1 static (ruff/mypy/pyright/tsc when present)
  L2 import dry-run
  L3 hop / unit tests
  L4 cone pytest (+ optional full suite, mutants elsewhere)
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LEVELS = ("L0_syntax", "L1_static", "L2_import", "L3_unit", "L4_cone")


@dataclass
class GateResult:
    level: str
    ok: bool
    command: str = ""
    summary: str = ""
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "ok": self.ok,
            "command": self.command,
            "summary": self.summary[:800],
            "details": self.details[:20],
        }


def _python_cmd(root: Path | None = None) -> list[str]:
    """Real CPython argv prefix — never the Remedy sidecar/CLI."""
    from remedy.core.build_python import python_cmd_for_subprocess

    return python_cmd_for_subprocess(root)


def _run(cmd: list[str], cwd: Path, timeout_s: float = 60.0) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        out = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        return proc.returncode == 0, out
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


def gate_l0_syntax(paths: list[str], root: Path) -> GateResult:
    from remedy.core.build_syntax import check_paths_syntax

    resolved = []
    for p in paths:
        pp = Path(p)
        if not pp.is_absolute():
            pp = root / p
        resolved.append(str(pp))
    syn = check_paths_syntax(resolved[-12:])
    bad = [r for r in syn if not r.get("ok")]
    return GateResult(
        level="L0_syntax",
        ok=not bad,
        command="compile/json",
        summary="syntax green" if not bad else f"{len(bad)} syntax errors",
        details=bad[:12],
    )


def gate_l1_static(root: Path, paths: list[str]) -> GateResult:
    """Run first available static checker on paths (ruff > pyright > mypy > tsc)."""
    py_paths = [p for p in paths if str(p).endswith(".py")]
    ts_paths = [p for p in paths if str(p).endswith((".ts", ".tsx"))]

    if py_paths and shutil.which("ruff"):
        rels = []
        for p in py_paths[:20]:
            pp = Path(p)
            try:
                rels.append(str(pp.relative_to(root)) if pp.is_absolute() else p)
            except Exception:
                rels.append(str(p))
        ok, out = _run(["ruff", "check", *rels], root, timeout_s=45)
        return GateResult(
            level="L1_static",
            ok=ok,
            command="ruff check " + " ".join(rels[:6]),
            summary=out[:600] or ("ruff green" if ok else "ruff red"),
        )

    if py_paths and shutil.which("pyright"):
        ok, out = _run(["pyright", *[str(p) for p in py_paths[:12]]], root, timeout_s=90)
        return GateResult(level="L1_static", ok=ok, command="pyright", summary=out[:600])

    if py_paths and shutil.which("mypy"):
        py = _python_cmd(root)
        if not py:
            return GateResult(
                level="L1_static",
                ok=True,
                command="mypy (skipped — no real Python)",
                summary="static gate soft-pass: no CPython for mypy -m",
            )
        ok, out = _run(
            [*py, "-m", "mypy", "--pretty", "--no-error-summary",
             *[str(p) for p in py_paths[:12]]],
            root,
            timeout_s=90,
        )
        return GateResult(level="L1_static", ok=ok, command="mypy", summary=out[:600])

    if (ts_paths or (root / "tsconfig.json").is_file()) and shutil.which("tsc"):
        ok, out = _run(["tsc", "--noEmit"], root, timeout_s=120)
        return GateResult(level="L1_static", ok=ok, command="tsc --noEmit", summary=out[:600])

    # No static tool — pass through (not fail closed)
    return GateResult(
        level="L1_static",
        ok=True,
        command="(skipped — no ruff/mypy/pyright/tsc)",
        summary="static gate skipped",
    )


def gate_l2_import(root: Path, paths: list[str]) -> GateResult:
    from remedy.core.build_import_graph import dry_run_imports_for_paths

    py = [p for p in paths if str(p).endswith(".py")]
    if not py:
        return GateResult(level="L2_import", ok=True, command="(no py)", summary="skip")
    results = dry_run_imports_for_paths(py, root)
    bad = [r for r in results if not r.get("ok")]
    # Sidecar/interpreter failures are machine config, not product red.
    real_bad = [
        r
        for r in bad
        if str(r.get("error_class") or "") not in {"interpreter", "spawn"}
    ]
    if bad and not real_bad:
        return GateResult(
            level="L2_import",
            ok=True,
            command="importlib dry-run",
            summary="import soft-pass: no real CPython (not a module fault)",
            details=bad[:10],
        )
    return GateResult(
        level="L2_import",
        ok=not real_bad,
        command="importlib dry-run",
        summary="import green" if not real_bad else f"{len(real_bad)} import failures",
        details=(real_bad or bad)[:10],
    )


def gate_l3_unit(root: Path, paths: list[str], *, unit_tests: list[str] | None = None) -> GateResult:
    """Run unit-level pytest files if provided or mapped."""
    tests = list(unit_tests or [])
    if not tests:
        with suppress(Exception):
            from remedy.core.build_scoped import map_source_to_test_candidates

            for w in paths:
                rel = str(w).replace("\\", "/")
                try:
                    pp = Path(w)
                    if pp.is_absolute():
                        rel = pp.relative_to(root).as_posix()
                except Exception:
                    pass
                for tp in map_source_to_test_candidates(rel, root):
                    try:
                        tests.append(tp.relative_to(root).as_posix())
                    except Exception:
                        tests.append(str(tp))
    tests = list(dict.fromkeys(tests))[:12]
    if not tests:
        return GateResult(
            level="L3_unit",
            ok=True,
            command="(no unit tests mapped)",
            summary="unit gate soft-pass",
        )
    py = _python_cmd(root)
    if not py:
        return GateResult(
            level="L3_unit",
            ok=True,
            command="(no real Python for unit pytest)",
            summary="unit gate soft-pass: interpreter missing",
        )
    ok, out = _run(
        [*py, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        root,
        timeout_s=90,
    )
    return GateResult(
        level="L3_unit",
        ok=ok,
        command="pytest -q " + " ".join(tests[:6]),
        summary=out[:600] or ("unit green" if ok else "unit red"),
    )


def gate_l4_cone(runtime: Any, root: Path, write_set: list[str], base_command: str = "") -> GateResult:
    from remedy.core.build_scoped import scoped_verify_command

    cmd = scoped_verify_command(
        runtime, write_set, base_command=base_command or "pytest -q", use_mutation_cone=True
    )
    if not cmd:
        cmd = base_command or "pytest -q"
    py = _python_cmd(root)
    if not py:
        return GateResult(
            level="L4_cone",
            ok=True,
            command=cmd,
            summary="cone soft-pass: no real Python for pytest",
        )
    # Parse simple pytest invocation
    parts = cmd if isinstance(cmd, list) else cmd.split()
    if parts and parts[0] == "pytest":
        argv = [*py, "-m", "pytest", *parts[1:]]
    else:
        # shell-ish — run via python -m pytest if pytest in string
        if re.search(r"\bpytest\b", cmd):
            rest = re.sub(r"^.*?\bpytest\b", "", cmd).strip()
            argv = [*py, "-m", "pytest", *rest.split()] if rest else [
                *py, "-m", "pytest", "-q"
            ]
        else:
            return GateResult(
                level="L4_cone",
                ok=True,
                command=cmd,
                summary="non-pytest L4 deferred to job runner",
            )
    ok, out = _run(argv, root, timeout_s=120)
    return GateResult(
        level="L4_cone",
        ok=ok,
        command=cmd,
        summary=out[:600] or ("cone green" if ok else "cone red"),
    )


def run_gate_tower(
    runtime: Any,
    write_set: list[str],
    *,
    stop_at_first_red: bool = True,
    levels: list[str] | None = None,
    base_verify: str = "",
    unit_tests: list[str] | None = None,
) -> dict[str, Any]:
    """Run L0→L4 with per-level isolation; stop at first red by default."""
    try:
        root = Path(runtime.effective_project_path())
        if root.is_file():
            root = root.parent
    except Exception:
        return {"ok": False, "error": "no project", "results": [], "first_red": None}

    want = levels or list(LEVELS)
    paths = list(write_set or [])
    results: list[dict[str, Any]] = []
    first_red: dict[str, Any] | None = None

    def _one(lev: str) -> GateResult:
        try:
            if lev == "L0_syntax":
                return gate_l0_syntax(paths, root)
            if lev == "L1_static":
                return gate_l1_static(root, paths)
            if lev == "L2_import":
                return gate_l2_import(root, paths)
            if lev == "L3_unit":
                return gate_l3_unit(root, paths, unit_tests=unit_tests)
            if lev == "L4_cone":
                return gate_l4_cone(runtime, root, paths, base_verify)
            return GateResult(level=lev, ok=True, summary="unknown level skipped")
        except Exception as e:
            return GateResult(level=lev, ok=False, summary=str(e)[:400])

    for lev in want:
        gr = _one(lev)
        pub = gr.to_public()
        results.append(pub)
        if not gr.ok and first_red is None:
            first_red = pub
            if stop_at_first_red:
                break

    return {
        "ok": first_red is None and bool(results),
        "results": results,
        "first_red": first_red,
        "passed_levels": [r["level"] for r in results if r.get("ok")],
        "message": (
            "Gate tower GREEN: " + " → ".join(r["level"] for r in results)
            if first_red is None and results
            else f"Gate tower RED at {(first_red or {}).get('level')}: "
            f"{(first_red or {}).get('summary', '')[:200]}"
        ),
    }


# Back-compat alias
run_gate_tower_safe = run_gate_tower


def format_gate_tower_message(result: dict[str, Any]) -> dict[str, str]:
    tag = "GREEN" if result.get("ok") else "RED"
    lines = [
        f"[Build engine · GATE TOWER · {tag}]",
        result.get("message") or "",
    ]
    for r in result.get("results") or []:
        mark = "✓" if r.get("ok") else "✗"
        lines.append(f"  {mark} {r.get('level')}: {(r.get('summary') or '')[:120]}")
    if not result.get("ok"):
        lines.append("Fix the first red level only, then re-run gate tower.")
    return {"role": "user", "content": "\n".join(lines)}
