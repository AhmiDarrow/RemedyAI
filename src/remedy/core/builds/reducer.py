"""Build reducer — machine-native orchestration for small-context local models.

A low-parameter model cannot hold a project or reason over a long build. This
module treats the model as a *stateless local-search function*:

    f(minimal_context_delta, error_vector) -> edit

All project state lives in the machine (filesystem, a symbol registry, and a
falsification oracle). The machine:
  - owns the symbol table (like a linker): cross-file consistency is enforced
    here, never by the model "remembering" other files;
  - computes a dependency-closure-minimal context per unit (a 4k window stays 4k);
  - verifies each edit with a real oracle and feeds the resulting error vector
    back so the loop converges by falsification.

A long build is therefore a machine-assembled composition of short, verified
hops — the way a CPU runs a program: many tiny instructions, none of which
"understands" the program.

Reliability properties (this iteration):
  - the loop always terminates: per-unit repair caps + a global iteration cap;
  - the oracle is pluggable; a `PytestOracle` drives the loop from real pytest
    failures (behavioral falsification), not just syntax/structural checks;
  - the project can be materialized to disk and verified end-to-end with pytest;
  - a real local-model hook (loopback llama-server) and offline mock models are
    both available, plus a CLI demo (`python -m remedy.core.builds`).

This module is self-contained (no live-loop wiring) so it is safe to test.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 1. Spec-as-schema (machine-written, machine-enforced; the model never designs
#    the API). A UnitSpec is one file the build must produce, optionally with a
#    pytest contract the oracle can falsify.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Signature:
    symbol: str
    params: str = ""          # text signature, e.g. "a: int, b: int"
    returns: str = ""
    defines_path: str = ""    # where the implementation lives
    consumes: tuple[str, ...] = ()  # symbols this body references (deps)


@dataclass
class UnitSpec:
    id: str
    path: str
    imports: list[str] = field(default_factory=list)  # module names
    declare: list[Signature] = field(default_factory=list)  # must define these
    requires: list[str] = field(default_factory=list)  # symbols to import/use
    behavior: str = ""  # short contract; model implements to it
    tests: str = ""  # optional pytest source that must pass for this unit


@dataclass
class BuildSpec:
    units: list[UnitSpec]

    def order(self) -> list[UnitSpec]:
        """Deterministic order: definitions before consumers."""
        defined: set[str] = set()
        out: list[UnitSpec] = []
        rest = list(self.units)
        while rest:
            moved = False
            for u in list(rest):
                if set(u.requires) <= defined:
                    out.append(u)
                    for s in u.declare:
                        defined.add(s.symbol)
                    rest.remove(u)
                    moved = True
            if not moved:  # cycle / impossible deps: emit remaining in order
                for u in rest:
                    for s in u.declare:
                        defined.add(s.symbol)
                out.extend(rest)
                break
        return out


# --------------------------------------------------------------------------
# 2. Symbol registry — the machine's linker symbol table.
# --------------------------------------------------------------------------


class SymbolRegistry:
    def __init__(self) -> None:
        self._sigs: dict[str, Signature] = {}

    def declare(self, sig: Signature) -> None:
        self._sigs[sig.symbol] = sig

    def lookup(self, symbol: str) -> Signature | None:
        return self._sigs.get(symbol)

    def references(self, source: str) -> list[str]:
        """Symbols the unit's body actually references (static scan)."""
        names: list[str] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return names
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                names.append(node.value.id)
        return [n for n in dict.fromkeys(names) if n in self._sigs]

    def closure_text(self, unit: UnitSpec, *, budget: int) -> str:
        """Minimal context: the unit's own contract + the signatures it needs."""
        lines: list[str] = []
        lines.append(f"# unit: {unit.path}")
        if unit.imports:
            lines.append(f"# import: {', '.join(unit.imports)}")
        lines.append("# define (must exist):")
        for s in unit.declare:
            lines.append(f"#   {s.symbol}({s.params}) -> {s.returns or '?'}")
        seen: set[str] = set()
        dep_lines: list[str] = []
        for dep in unit.requires:
            sig = self._sigs.get(dep)
            if sig and sig.symbol not in seen:
                seen.add(sig.symbol)
                dep_lines.append(
                    f"#   {sig.symbol}({sig.params}) -> {sig.returns or '?'}"
                    f"  [defined in {sig.defines_path}]"
                )
        if dep_lines:
            lines.append("# available (from dependencies):")
            lines.extend(dep_lines)
        if unit.behavior:
            lines.append(f"# behavior: {unit.behavior}")
        text = "\n".join(lines)
        return text[:budget]


# --------------------------------------------------------------------------
# 3. Oracles — the falsification signal that drives the loop.
# --------------------------------------------------------------------------


@dataclass
class OracleError:
    unit_id: str
    message: str


def run_oracle(unit: UnitSpec, source: str) -> list[OracleError]:
    """Structural oracle: compile + verify imports and declared symbols exist."""
    errors: list[OracleError] = []
    try:
        compile(source, unit.path, "exec")
    except SyntaxError as e:
        errors.append(OracleError(unit.id, f"SyntaxError: {e.msg} (line {e.lineno})"))
        return errors
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return errors
    for imp in unit.imports:
        mod = imp.split(".")[0]
        found = any(
            isinstance(n, (ast.Import, ast.ImportFrom))
            and any(
                a.name == mod or (a.name or "").startswith(mod + ".")
                for a in n.names
            )
            for n in ast.walk(tree)
        )
        if not found:
            errors.append(OracleError(unit.id, f"missing import: {mod}"))
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
    for sig in unit.declare:
        if sig.symbol not in defined:
            errors.append(OracleError(unit.id, f"missing definition: {sig.symbol}"))
    return errors


def _structural_oracle(unit: UnitSpec, state: dict[str, str]) -> list[OracleError]:
    return run_oracle(unit, state.get(unit.path, ""))


def materialize(files: dict[str, str], root: str | Path) -> Path:
    """Write the produced files under root (creating dirs); returns root."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, source in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
    return root


def run_project_tests(
    files: dict[str, str],
    root: str | Path,
    *,
    extra_tests: dict[str, str] | None = None,
    timeout_s: float = 60.0,
) -> tuple[bool, str, list[str]]:
    """Materialize the project and run pytest; returns (ok, summary, failures)."""
    root = Path(materialize(files, root))
    for rel, test_src in (extra_tests or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(test_src, encoding="utf-8")
    py = sys.executable
    try:
        proc = subprocess.run(
            [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"pytest failed to run: {e}", []
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return True, out.strip(), []
    fails: list[str] = []
    for line in (out or "").splitlines():
        if line and not line.startswith(("=", "---", "…", " ", "\t")) and "FAILED" in line:
            fails.append(line.strip())
    return False, out.strip(), fails


class PytestOracle:
    """Behavioral oracle: materialize state + the unit's pytest, run it, and turn
    real failures into an error vector the loop repairs against."""

    def __init__(self, root: str | Path, timeout_s: float = 45.0) -> None:
        self.root = Path(root)
        self.timeout_s = timeout_s

    def __call__(self, unit: UnitSpec, state: dict[str, str]) -> list[OracleError]:
        if not unit.tests:
            return _structural_oracle(unit, state)
        materialize(state, self.root)
        test_rel = f"test_{unit.id}.py"
        (self.root / test_rel).write_text(unit.tests, encoding="utf-8")
        py = sys.executable
        try:
            proc = subprocess.run(
                [py, "-m", "pytest", test_rel, "-q", "-p", "no:cacheprovider"],
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return [OracleError(unit.id, f"pytest error: {e}")]
        if proc.returncode == 0:
            return []
        out = (proc.stdout or "") + (proc.stderr or "")
        snippet = "\n".join(
            ln for ln in (out or "").splitlines() if "assert" in ln or "Error" in ln or "FAILED" in ln
        )[:1200] or out[:1200]
        return [OracleError(unit.id, f"test failed:\n{snippet}")]


# --------------------------------------------------------------------------
# 4. The reducer — model is a stateless worker, the machine owns the loop.
# --------------------------------------------------------------------------


ModelFn = Callable[[UnitSpec, str, list[OracleError]], str]
OracleFn = Callable[[UnitSpec, dict[str, str]], list[OracleError]]


@dataclass
class UnitFailure:
    unit_id: str
    path: str
    attempts: int
    last_error: str


@dataclass
class BuildResult:
    ok: bool
    files: dict[str, str]
    iterations: int
    repaired: int = 0
    errors: list[OracleError] = field(default_factory=list)
    failures: list[UnitFailure] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"ok={self.ok} files={len(self.files)} iterations={self.iterations} "
            f"repaired={self.repaired} failures={len(self.failures)}",
        ]
        for f in self.failures:
            lines.append(f"  [failed] {f.path} after {f.attempts} attempts: {f.last_error}")
        return "\n".join(lines)

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "file_count": len(self.files),
            "iterations": self.iterations,
            "repaired": self.repaired,
            "error_count": len(self.errors),
            "failed_units": [f.path for f in self.failures],
        }


def build_project(
    spec: BuildSpec,
    model: ModelFn,
    *,
    oracle: OracleFn | None = None,
    context_budget: int = 3_000,
    max_iterations: int = 200,
    max_repairs: int = 5,
) -> BuildResult:
    """Reduce the spec to a project by stateless, oracle-verified hops.

    Always terminates: a unit is dropped (and reported) after ``max_repairs``
    extra attempts, and the whole loop stops at ``max_iterations``.
    """
    oracle = oracle or _structural_oracle
    registry = SymbolRegistry()
    for u in spec.order():
        for s in u.declare:
            registry.declare(s)

    files: dict[str, str] = {}
    repaired = 0
    errors: list[OracleError] = []
    pending = list(spec.order())
    iters = 0
    attempts: dict[str, int] = {}
    failures: list[UnitFailure] = []

    while pending and iters < max_iterations:
        unit = pending.pop(0)
        attempts[unit.id] = attempts.get(unit.id, 0) + 1
        closure = registry.closure_text(unit, budget=context_budget)
        errs = [e for e in errors if e.unit_id == unit.id]
        source = model(unit, closure, errs)
        files[unit.path] = source
        unit_errors = oracle(unit, files)
        errors = [e for e in errors if e.unit_id != unit.id] + unit_errors
        if not unit_errors:
            repaired += attempts[unit.id] - 1
            continue  # verified; machine moves on
        if attempts[unit.id] > max_repairs:
            last = unit_errors[0].message if unit_errors else "unknown error"
            failures.append(UnitFailure(unit.id, unit.path, attempts[unit.id], last))
            continue  # give up on this unit; don't starve the rest
        pending.append(unit)  # falsified → re-enqueue for repair
        iters += 1

    ok = not failures and not pending and not errors
    return BuildResult(
        ok=ok,
        files=files,
        iterations=iters,
        repaired=repaired,
        errors=[e for e in errors if e.unit_id in attempts],
        failures=failures,
        attempts=attempts,
    )


# --------------------------------------------------------------------------
# 5. Output normalization + model hooks (real local + offline mocks).
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:python|py)?\s*(.*?)\s*```", re.DOTALL)


def extract_markdown_fence(text: str) -> str:
    """Strip a common ```python ... ``` fence so raw model output is usable."""
    m = _FENCE_RE.search(text or "")
    return (m.group(1) if m else (text or "")).strip()


def _chat_complete_loopback(
    prompt: str,
    *,
    base_url: str,
    max_tokens: int,
    temperature: float,
    timeout_s: float,
) -> str:
    """OpenAI-compatible completion against a loopback-only llama-server."""
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request

    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        return ""
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    body = {
        "model": "local-build",
        "temperature": temperature,
        "max_tokens": max(1, int(max_tokens)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write one source file for a Python project. "
                    "Output ONLY the raw file source. No markdown fences, no "
                    "explanation, no commentary."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "RemedyAI-builds/1.0"},
        method="POST",
    )
    try:
        with urlopen_no_redirect(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        return ""
    try:
        msg = (payload.get("choices") or [{}])[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            return " ".join(str(c.get("text") or c) if isinstance(c, dict) else str(c) for c in content)
        return str(content or "")
    except Exception:
        return ""


def local_llama_model(
    *,
    base_url: str | None = None,
    context_budget: int = 2_000,
    max_tokens: int = 2_000,
    temperature: float = 0.1,
    timeout_s: float = 90.0,
) -> ModelFn:
    """Build a model hook bound to a loopback llama-server (real inference)."""
    if base_url is None:
        try:
            from remedy.memory.harness.local_brief import _local_base_url

            base_url = _local_base_url()
        except Exception:
            base_url = "http://127.0.0.1:8740/v1"

    def hook(unit: UnitSpec, closure: str, errors: list[OracleError]) -> str:
        prompt = closure + "\n\n"
        if errors:
            prompt += (
                "The oracle rejected your previous version with these errors:\n"
                + "\n".join(f"- {e.message}" for e in errors)
                + "\n\nFix them and output the corrected file only.\n"
            )
        else:
            prompt += (
                "Write the complete file now. It must define every listed symbol "
                "and implement the behavior. Output the file source only.\n"
            )
        text = _chat_complete_loopback(
            prompt,
            base_url=base_url,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_s=timeout_s,
        )
        src = extract_markdown_fence(text)
        # A usable file must at least define something; an empty reply keeps the
        # structural oracle firing so the loop re-asks with the error in context.
        return src if src.strip() else "# empty\npass\n"

    return hook


# Offline mock "small models" for testing the mechanism without a server.

_PROMPT_FNS = re.compile(r"#   (\w+)\(")


def _demo_correct(unit: UnitSpec, closure: str) -> str:
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


def demo_model(unit: UnitSpec, closure: str, errors: list[OracleError]) -> str:
    """Deterministic correct-ish mock: reads its contract from the closure."""
    if errors:
        return _demo_correct(unit, closure)
    return _demo_correct(unit, closure)


def demo_weak_model(
    defect_rate: float = 0.35, seed: int = 1, repair: ModelFn | None = None
) -> ModelFn:
    """Stochastic weak mock: emits a syntax error or drops a symbol on the first
    pass; once the oracle error is in context it repairs. ``repair`` is the model
    that produces the corrected edit (defaults to a structural stub — pass a
    behavior-aware model for end-to-end PytestOracle convergence)."""

    def model(unit: UnitSpec, closure: str, errors: list[OracleError]) -> str:
        import random

        rng = random.Random(seed + sum(ord(c) for c in unit.id) + len(errors) * 100)
        if errors:
            if repair is not None:
                return repair(unit, closure, errors)
            return _demo_correct(unit, closure)
        src = _demo_correct(unit, closure)
        roll = rng.random()
        if roll < defect_rate * 0.5:
            return src + "\n    break"  # SyntaxError
        if roll < defect_rate and unit.declare:
            drop = unit.declare[-1].symbol
            return _drop_def(src, drop)
        return src

    return model


def _drop_def(src: str, symbol: str) -> str:
    out: list[str] = []
    skip = False
    for ln in src.splitlines():
        if ln.startswith(f"def {symbol}("):
            skip = True
            continue
        if skip and (
            ln.startswith("def ")
            or ln.startswith("import ")
            or ln.startswith("class ")
            or ln.startswith("def _uses")
        ):
            skip = False
        if not skip:
            out.append(ln)
    return "\n".join(out)
