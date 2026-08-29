"""Import-graph dry-run + mutation cone for scoped re-verify.

After .py writes, the machine:
1) Parses import edges (static AST)
2) Dry-runs import of mutated modules (subprocess isolation)
3) Computes the **import cone** of write_set — reverse deps that may break
4) Feeds that cone into scoped pytest selection (mutation score)

This is faster than full suite and more precise than single-file mapping alone.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections import defaultdict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ImportGraph:
    """Directed graph: module → set of modules it imports (within project)."""

    root: Path
    # rel posix path without .py → set of imported module paths (same form)
    edges: dict[str, set[str]] = field(default_factory=dict)
    # reverse: module → importers
    reverse: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    path_by_mod: dict[str, str] = field(default_factory=dict)  # mod → rel path

    def to_public(self) -> dict[str, Any]:
        return {
            "modules": len(self.edges),
            "edges": {k: sorted(v) for k, v in list(self.edges.items())[:80]},
        }


def _path_to_mod(rel: str) -> str | None:
    rel = rel.replace("\\", "/").strip("/")
    if not rel.endswith(".py"):
        return None
    rel = rel[:-len("/__init__.py")] if rel.endswith("/__init__.py") else rel[:-len(".py")]
    parts = [p for p in rel.split("/") if p and p != "."]
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return None
    if any(not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", p) for p in parts):
        return None
    return ".".join(parts)


def _mod_to_candidates(mod: str, root: Path) -> list[Path]:
    parts = mod.split(".")
    cands = [
        root.joinpath(*parts).with_suffix(".py"),
        root.joinpath(*parts, "__init__.py"),
        root.joinpath("src", *parts).with_suffix(".py"),
        root.joinpath("src", *parts, "__init__.py"),
    ]
    return cands


def parse_imports_from_source(source: str) -> list[str]:
    """Top-level import module names from Python source."""
    out: list[str] = []
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name:
                    out.append(a.name.split(".")[0] if False else a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.append(node.module)
    # unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for m in out:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq


def build_import_graph(root: Path | str, *, max_files: int = 400) -> ImportGraph:
    """Scan project .py files and build an import graph (project-local only)."""
    root = Path(root)
    g = ImportGraph(root=root)
    files: list[Path] = []
    with suppress(Exception):
        for p in root.rglob("*.py"):
            # skip heavy/noise
            parts = set(p.parts)
            if parts & {".venv", "venv", "node_modules", ".git", "__pycache__", "dist", "build"}:
                continue
            files.append(p)
            if len(files) >= max_files:
                break

    # index path → mod
    for p in files:
        try:
            rel = p.relative_to(root).as_posix()
        except Exception:
            continue
        mod = _path_to_mod(rel)
        if mod:
            g.path_by_mod[mod] = rel
            g.edges.setdefault(mod, set())

    for p in files:
        try:
            rel = p.relative_to(root).as_posix()
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        mod = _path_to_mod(rel)
        if not mod:
            continue
        for imp in parse_imports_from_source(text):
            # resolve to project module if possible
            target = None
            if imp in g.path_by_mod:
                target = imp
            else:
                # try parent packages
                for k in g.path_by_mod:
                    if k == imp or k.startswith(imp + "."):
                        target = k
                        break
            if target and target != mod:
                g.edges.setdefault(mod, set()).add(target)
                g.reverse[target].add(mod)
    return g


def import_cone(
    graph: ImportGraph,
    seed_mods: list[str],
    *,
    max_nodes: int = 40,
) -> list[str]:
    """Modules that import seeds (reverse BFS) plus seeds themselves."""
    seeds = [s for s in seed_mods if s]
    if not seeds:
        return []
    seen: set[str] = set()
    q: deque[str] = deque()
    for s in seeds:
        if s not in seen:
            seen.add(s)
            q.append(s)
    while q and len(seen) < max_nodes:
        cur = q.popleft()
        for parent in graph.reverse.get(cur, ()):
            if parent not in seen:
                seen.add(parent)
                q.append(parent)
                if len(seen) >= max_nodes:
                    break
    return list(seen)


def paths_to_mods(paths: list[str], root: Path) -> list[str]:
    mods: list[str] = []
    for raw in paths or []:
        try:
            p = Path(raw)
            if not p.is_absolute():
                p = (root / p).resolve()
            rel = p.relative_to(root.resolve()).as_posix()
        except Exception:
            rel = str(raw).replace("\\", "/")
        m = _path_to_mod(rel)
        if m and m not in mods:
            mods.append(m)
    return mods


def dry_run_import(
    module: str,
    *,
    root: Path | str,
    timeout_s: float = 15.0,
) -> dict[str, Any]:
    """Import *module* in a subprocess with PYTHONPATH=root (and root/src).

    Uses a real CPython (never the frozen/sidecar ``remedy`` exe). A missing
    interpreter is reported as ``interpreter`` error class — not a module fault.
    """
    root = Path(root)
    from remedy.core.build_python import is_sidecar_spawn_error, python_cmd_for_subprocess

    py = python_cmd_for_subprocess(root)
    if not py:
        return {
            "ok": False,
            "module": module,
            "error": (
                "no real Python interpreter for import dry-run "
                "(sys.executable is the Remedy sidecar/CLI; set REMEDY_PYTHON)"
            ),
            "error_class": "interpreter",
        }
    env_pythonpath = str(root)
    src = root / "src"
    if src.is_dir():
        env_pythonpath = str(src) + (";" if sys.platform == "win32" else ":") + env_pythonpath
    code = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(root / 'src')!r})\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        f"importlib.import_module({module!r})\n"
        "print('OK')\n"
    )
    try:
        from remedy.execution.process import hidden_subprocess_kwargs

        proc = subprocess.run(
            [*py, "-c", code],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**dict(os.environ), "PYTHONPATH": env_pythonpath},
            **hidden_subprocess_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "module": module, "error": str(e)[:400], "error_class": "spawn"}
    if proc.returncode == 0 and "OK" in (proc.stdout or ""):
        return {"ok": True, "module": module, "error": "", "error_class": ""}
    err = ((proc.stderr or "") + (proc.stdout or ""))[-500:]
    err_class = "import"
    if is_sidecar_spawn_error(err):
        err_class = "interpreter"
        err = (
            "import dry-run spawned the Remedy CLI instead of CPython "
            f"(cmd={py!r}). {err[:300]}"
        )
    return {
        "ok": False,
        "module": module,
        "error": err or f"exit {proc.returncode}",
        "error_class": err_class,
    }


def dry_run_imports_for_paths(
    paths: list[str],
    root: Path | str,
    *,
    max_modules: int = 10,
) -> list[dict[str, Any]]:
    root = Path(root)
    mods = paths_to_mods(paths, root)[:max_modules]
    return [dry_run_import(m, root=root) for m in mods]


def format_import_dry_run_message(results: list[dict[str, Any]]) -> dict[str, str] | None:
    bad = [r for r in results if not r.get("ok")]
    if not bad:
        return None
    # Interpreter/sidecar failures are machine config — do not send the model
    # on a wild goose chase editing healthy modules.
    interp = [
        r
        for r in bad
        if str(r.get("error_class") or "") == "interpreter"
        or "no real Python" in str(r.get("error") or "")
        or "Remedy CLI" in str(r.get("error") or "")
        or "REMEDY_PYTHON" in str(r.get("error") or "")
    ]
    if interp and len(interp) == len(bad):
        sample = (interp[0].get("error") or "")[:240]
        return {
            "role": "user",
            "content": (
                "[Build engine · IMPORT DRY-RUN · SKIPPED]\n"
                "Import dry-run could not run: no real CPython (sidecar/CLI was "
                "selected as sys.executable). This is **not** a module import bug — "
                "do not file_edit product code for it. Set REMEDY_PYTHON to a real "
                f"interpreter or install Python on PATH, then continue.\n  · {sample}"
            ),
        }
    lines = [
        "[Build engine · IMPORT DRY-RUN · RED]",
        "Machine failed importing mutated modules (faster than full suite):",
    ]
    for r in bad[:10]:
        if str(r.get("error_class") or "") == "interpreter":
            continue
        lines.append(f"  · {r.get('module')}: {(r.get('error') or '')[:200]}")
    # If we filtered everything, fall back to skip message
    if len(lines) <= 2:
        return {
            "role": "user",
            "content": (
                "[Build engine · IMPORT DRY-RUN · SKIPPED]\n"
                "Only interpreter/spawn failures — not module bugs. Continue the build."
            ),
        }
    lines.append("file_edit those modules/deps, then continue. Prefer fixing imports first.")
    return {"role": "user", "content": "\n".join(lines)}


def mutation_score_paths(
    root: Path | str,
    write_set: list[str],
    *,
    max_nodes: int = 40,
) -> dict[str, Any]:
    """Return import cone paths + score for scoped verify expansion."""
    root = Path(root)
    g = build_import_graph(root)
    seeds = paths_to_mods(write_set, root)
    cone = import_cone(g, seeds, max_nodes=max_nodes)
    cone_paths = []
    for m in cone:
        rel = g.path_by_mod.get(m)
        if rel:
            cone_paths.append(rel)
    # score: fraction of project modules in cone (lower is more focused)
    total = max(1, len(g.edges))
    score = min(1.0, len(cone) / total)
    return {
        "seed_mods": seeds,
        "cone_mods": cone,
        "cone_paths": cone_paths,
        "mutation_score": round(score, 4),
        "graph_modules": total,
    }
