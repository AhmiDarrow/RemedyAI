"""Scoped verify — run only tests related to the write-set (machine efficiency).

A full suite after every edit is human-slow. The machine maps mutated paths
to the smallest falsification set it can prove still meaningful.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any


def _project_root(runtime: Any) -> Path | None:
    with suppress(Exception):
        root = runtime.effective_project_path()
        p = Path(root)
        if p.is_file():
            p = p.parent
        if p.is_dir():
            return p
    return None


def _rel_to_project(path: str, root: Path) -> str | None:
    try:
        p = Path(path)
        cand = (root / p).resolve() if not p.is_absolute() else p.resolve()
        rel = cand.relative_to(root.resolve())
        return rel.as_posix()
    except Exception:
        return None


def map_source_to_test_candidates(rel: str, root: Path) -> list[Path]:
    """Heuristic source → test path mapping (Python-first, portable-ish)."""
    rel = rel.replace("\\", "/")
    out: list[Path] = []
    name = Path(rel).name
    stem = Path(rel).stem
    parent = str(Path(rel).parent).replace("\\", "/")

    # Already a test file
    if re.search(r"(^|/)tests?/", rel) or name.startswith("test_") or name.endswith("_test.py"):
        p = root / rel
        if p.is_file():
            out.append(p)
        return out

    candidates = [
        root / "tests" / f"test_{stem}.py",
        root / "test" / f"test_{stem}.py",
        root / parent / f"test_{stem}.py",
        root / parent / f"{stem}_test.py",
        root / "tests" / parent / f"test_{stem}.py",
        root / "src" / "tests" / f"test_{stem}.py",
    ]
    # package tests
    if parent and parent != ".":
        candidates.append(root / "tests" / Path(parent).name / f"test_{stem}.py")

    for c in candidates:
        with suppress(Exception):
            if c.is_file():
                out.append(c)
    return out


def scoped_verify_command(
    runtime: Any,
    write_set: list[str],
    *,
    base_command: str = "",
    use_mutation_cone: bool = True,
) -> str:
    """Return a scoped verify command, or empty to fall back to full suite.

    Python: ``pytest -q path1 path2`` for mapped tests.
    When *use_mutation_cone*, expand write_set with reverse-import dependents
    so importers of mutated modules also get their tests selected.
    Other stacks: empty (caller uses base suite).
    """
    root = _project_root(runtime)
    if root is None:
        return ""
    base = (base_command or "").strip()
    # Expand write_set via import cone (mutation score)
    paths = list(write_set or [])
    if use_mutation_cone and paths:
        with suppress(Exception):
            from remedy.core.build_import_graph import mutation_score_paths

            ms = mutation_score_paths(root, paths)
            for cp in ms.get("cone_paths") or []:
                if cp not in paths:
                    paths.append(cp)
            # stash for status
            with suppress(Exception):
                runtime._last_mutation_score = ms  # type: ignore[attr-defined]
    # Only scope pytest-family
    if base and not re.search(r"(?i)\bpytest\b", base) and base not in ("",):
        # npm test / cargo — hard to scope reliably without project knowledge
        if re.search(r"(?i)\bnpm\s+test\b", base):
            return base  # full
        if re.search(r"(?i)\bcargo\s+test\b", base):
            return base
        if re.search(r"(?i)\bgo\s+test\b", base):
            # go test for packages under write set
            pkgs: list[str] = []
            for w in paths:
                rel = _rel_to_project(w, root)
                if not rel or not rel.endswith(".go"):
                    continue
                pkg = "./" + str(Path(rel).parent).replace("\\", "/")
                if pkg not in pkgs:
                    pkgs.append(pkg if pkg != "./." else "./...")
            if pkgs:
                return "go test " + " ".join(pkgs[:8])
        return ""

    test_files: list[str] = []
    for w in paths:
        rel = _rel_to_project(w, root)
        if not rel:
            continue
        for tp in map_source_to_test_candidates(rel, root):
            try:
                trel = tp.relative_to(root).as_posix()
            except Exception:
                trel = str(tp)
            if trel not in test_files:
                test_files.append(trel)

    if not test_files:
        # No mapped tests — prefer last-failed if pytest cache exists
        if (root / ".pytest_cache").exists() and (
            (root / "tests").is_dir() or (root / "test").is_dir()
        ):
            return "pytest -q --lf"
        return ""

    # Cap paths for CLI length
    paths = " ".join(f'"{t}"' if " " in t else t for t in test_files[:12])
    return f"pytest -q {paths}"


def format_scoped_verify_note(full_cmd: str, scoped_cmd: str) -> str:
    if not scoped_cmd or scoped_cmd == full_cmd:
        return f"full suite: `{full_cmd}`"
    return f"scoped: `{scoped_cmd}` (fallback full: `{full_cmd}`)"
