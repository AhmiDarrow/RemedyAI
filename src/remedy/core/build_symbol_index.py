"""Disk-wide symbol index — machine linker over the live project tree.

Frontier G: closure is not per-hop hand-waving. The machine indexes defs/refs
across the tree so hop context is always minimal and cross-file consistency
can be checked like a real linker.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.relpath import norm_rel

_SKIP = frozenset(
    {".venv", "venv", "node_modules", ".git", "__pycache__", "dist", "build", ".remedy-build"}
)


@dataclass
class SymbolDef:
    name: str
    kind: str  # function | class | assign
    path: str  # rel posix
    line: int = 0
    params: str = ""


@dataclass
class SymbolIndex:
    root: Path
    defs: dict[str, list[SymbolDef]] = field(default_factory=lambda: defaultdict(list))
    # path → set of names defined
    defs_by_path: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # name → paths that reference it (best-effort Name loads)
    refs: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    file_count: int = 0

    def lookup(self, name: str) -> list[SymbolDef]:
        return list(self.defs.get(name) or [])

    def primary(self, name: str) -> SymbolDef | None:
        hits = self.lookup(name)
        return hits[0] if hits else None

    def to_public(self) -> dict[str, Any]:
        return {
            "files": self.file_count,
            "symbols": len(self.defs),
            "sample": sorted(self.defs.keys())[:40],
        }


def _rel(root: Path, p: Path) -> str | None:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return None


def _extract_params(node: ast.AST) -> str:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    args = node.args
    parts: list[str] = []
    for a in list(args.posonlyargs) + list(args.args):
        parts.append(a.arg)
    if args.vararg:
        parts.append("*" + args.vararg.arg)
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append("**" + args.kwarg.arg)
    return ", ".join(parts)


def index_python_file(source: str, rel: str) -> tuple[list[SymbolDef], set[str]]:
    """Return (defs, free-ish name references)."""
    defs: list[SymbolDef] = []
    refs: set[str] = set()
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return defs, refs
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append(
                SymbolDef(
                    name=node.name,
                    kind="function",
                    path=rel,
                    line=getattr(node, "lineno", 0) or 0,
                    params=_extract_params(node),
                )
            )
        elif isinstance(node, ast.ClassDef):
            defs.append(
                SymbolDef(
                    name=node.name,
                    kind="class",
                    path=rel,
                    line=getattr(node, "lineno", 0) or 0,
                )
            )
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and re.match(r"^[A-Z_][A-Z0-9_]*$", t.id):
                    defs.append(
                        SymbolDef(
                            name=t.id,
                            kind="assign",
                            path=rel,
                            line=getattr(node, "lineno", 0) or 0,
                        )
                    )
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if not node.id.startswith("_"):
                refs.add(node.id)
    return defs, refs


def build_symbol_index(root: Path | str, *, max_files: int = 500) -> SymbolIndex:
    root = Path(root)
    idx = SymbolIndex(root=root)
    files: list[Path] = []
    with suppress(Exception):
        for p in root.rglob("*.py"):
            if set(p.parts) & _SKIP:
                continue
            files.append(p)
            if len(files) >= max_files:
                break
    for p in files:
        rel = _rel(root, p)
        if not rel:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        defs, refs = index_python_file(text, rel)
        idx.file_count += 1
        for d in defs:
            idx.defs[d.name].append(d)
            idx.defs_by_path[rel].add(d.name)
        defined_here = {d.name for d in defs}
        for name in refs:
            if name not in defined_here:
                idx.refs[name].add(rel)
    return idx


def closure_from_index(
    index: SymbolIndex,
    *,
    path: str,
    symbols: list[str] | None = None,
    requires: list[str] | None = None,
    budget: int = 4000,
) -> str:
    """Minimal linker-style context for a unit path."""
    rel = norm_rel(path)
    lines: list[str] = [f"# unit: {rel}", f"# indexed_files={index.file_count}"]
    local = sorted(index.defs_by_path.get(rel) or [])
    if local:
        lines.append("# defines (on disk):")
        for n in local[:20]:
            d = next((x for x in index.defs.get(n, []) if x.path == rel), None)
            if d:
                if d.kind == "function":
                    lines.append(f"#   def {d.name}({d.params})  L{d.line}")
                else:
                    lines.append(f"#   {d.kind} {d.name}  L{d.line}")
    need = list(symbols or []) + list(requires or [])
    seen: set[str] = set()
    dep_lines: list[str] = []
    for n in need:
        if n in seen:
            continue
        seen.add(n)
        for d in index.lookup(n)[:3]:
            if d.path == rel:
                continue
            dep_lines.append(
                f"#   {d.name}({d.params}) [{d.kind}] @ {d.path}:{d.line}"
            )
    if dep_lines:
        lines.append("# available (linker):")
        lines.extend(dep_lines[:30])
    # Callers that may break if we change symbols
    for n in (symbols or local)[:8]:
        importers = sorted(index.refs.get(n) or [])
        if importers:
            lines.append(f"# referenced_by {n}: {', '.join(importers[:6])}")
    text = "\n".join(lines)
    return text[:budget]


def linker_check_requires(
    index: SymbolIndex,
    requires: list[str],
    *,
    defining_path: str = "",
) -> list[str]:
    """Return missing required symbols (not defined anywhere in the index)."""
    missing: list[str] = []
    for r in requires or []:
        hits = index.lookup(r)
        if not hits:
            missing.append(r)
            continue
        if defining_path and all(h.path == defining_path for h in hits):
            # only defined in the same file we're about to rewrite — ok if self
            pass
    return missing
