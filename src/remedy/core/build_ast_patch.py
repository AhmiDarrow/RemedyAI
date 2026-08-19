"""AST-minimal patches — prefer surgical symbol edits over whole-file rewrites.

Frontier G: reduce blast radius. When the model only needs to fix one def,
replace that node rather than rewriting the entire module.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class PatchResult:
    ok: bool
    source: str
    method: str  # ast_replace | whole_file | failed
    symbol: str = ""
    error: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "method": self.method,
            "symbol": self.symbol,
            "error": self.error,
            "chars": len(self.source or ""),
        }


def _node_source_segment(source: str, node: ast.AST) -> str | None:
    try:
        return ast.get_source_segment(source, node)
    except Exception:
        return None


def extract_def_source(source: str, symbol: str) -> str | None:
    """Return the full source text of a top-level function/class named *symbol*."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                seg = _node_source_segment(source, node)
                if seg:
                    return seg
                # fallback: line range
                if hasattr(node, "lineno") and hasattr(node, "end_lineno") and node.end_lineno:
                    lines = source.splitlines(keepends=True)
                    return "".join(lines[node.lineno - 1 : node.end_lineno])
    return None


def replace_top_level_def(
    source: str,
    symbol: str,
    new_def_source: str,
) -> PatchResult:
    """Replace a top-level function/class body with *new_def_source*."""
    new_def_source = (new_def_source or "").strip()
    if not new_def_source:
        return PatchResult(ok=False, source=source, method="failed", symbol=symbol, error="empty patch")
    # Strip markdown fences if present
    if new_def_source.startswith("```"):
        new_def_source = re.sub(r"^```(?:\w+)?\n?", "", new_def_source)
        new_def_source = re.sub(r"\n?```$", "", new_def_source).strip()
    try:
        tree = ast.parse(source or "")
    except SyntaxError as e:
        return PatchResult(
            ok=False, source=source, method="failed", symbol=symbol, error=f"base SyntaxError: {e}"
        )
    # Validate new def parses
    try:
        new_mod = ast.parse(new_def_source)
    except SyntaxError as e:
        return PatchResult(
            ok=False, source=source, method="failed", symbol=symbol, error=f"patch SyntaxError: {e}"
        )
    if not new_mod.body:
        return PatchResult(ok=False, source=source, method="failed", symbol=symbol, error="empty AST")

    target = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                target = node
                break
    if target is None:
        # Append as new top-level def
        out = (source or "").rstrip() + "\n\n" + new_def_source + "\n"
        try:
            compile(out, "<patch>", "exec")
        except SyntaxError as e:
            return PatchResult(
                ok=False, source=source, method="failed", symbol=symbol, error=str(e)
            )
        return PatchResult(ok=True, source=out, method="ast_replace", symbol=symbol)

    if not hasattr(target, "lineno") or not getattr(target, "end_lineno", None):
        return PatchResult(
            ok=False, source=source, method="failed", symbol=symbol, error="no line span"
        )
    lines = (source or "").splitlines(keepends=True)
    start = target.lineno - 1
    # A decorated def has lineno on the `def` line, not on the decorators above
    # it. Replacing from there kept the old decorators and then wrote the
    # patch's own on top, so a patch that carried its decorators produced them
    # twice — and the merged file still compiled, which is precisely the
    # failure this module exists to avoid. `@cache` twice is harmless;
    # `@app.route(...)` twice registers the route twice and `@retry(3)` twice
    # is nine attempts. Only widen the span when the patch brings its own.
    new_head = new_mod.body[0]
    if getattr(new_head, "decorator_list", None) and getattr(
        target, "decorator_list", None
    ):
        start = min(d.lineno for d in target.decorator_list) - 1
    end = target.end_lineno  # exclusive in slice end
    # Preserve trailing newline on replacement
    repl = new_def_source
    if not repl.endswith("\n"):
        repl += "\n"
    new_lines = lines[:start] + [repl] + lines[end:]
    out = "".join(new_lines)
    try:
        compile(out, "<patch>", "exec")
    except SyntaxError as e:
        return PatchResult(
            ok=False, source=source, method="failed", symbol=symbol, error=f"merged SyntaxError: {e}"
        )
    return PatchResult(ok=True, source=out, method="ast_replace", symbol=symbol)


def apply_minimal_patch(
    base_source: str,
    *,
    symbol: str = "",
    patch_source: str = "",
    whole_file: str = "",
) -> PatchResult:
    """Prefer AST symbol patch; fall back to whole-file when needed."""
    if whole_file and whole_file.strip() and not (symbol and patch_source):
        body = whole_file.strip()
        try:
            compile(body, "<whole>", "exec")
        except SyntaxError as e:
            return PatchResult(
                ok=False, source=base_source, method="failed", error=f"whole_file SyntaxError: {e}"
            )
        return PatchResult(ok=True, source=body + ("\n" if not body.endswith("\n") else ""), method="whole_file")
    if symbol and patch_source:
        # If patch looks like a full module, treat as whole_file
        if re.search(r"(?m)^(import |from |class |def )", patch_source) and patch_source.count("\n") > 15:
            # still try symbol replace first if it defines the symbol
            if f"def {symbol}" in patch_source or f"class {symbol}" in patch_source:
                # extract just the def if full file provided as patch
                seg = extract_def_source(patch_source, symbol)
                if seg:
                    return replace_top_level_def(base_source, symbol, seg)
        return replace_top_level_def(base_source, symbol, patch_source)
    if whole_file and whole_file.strip():
        return apply_minimal_patch(base_source, whole_file=whole_file)
    return PatchResult(ok=False, source=base_source, method="failed", error="no patch material")
