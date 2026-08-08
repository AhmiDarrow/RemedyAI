"""Post-write syntax gate — cheap compile check before full oracle.

Machine path: after Python (and simple JSON) mutations, fail closed early
without waiting for a full test suite.
"""

from __future__ import annotations

import json
import py_compile
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any


def check_path_syntax(path: str | Path) -> dict[str, Any]:
    """Return {ok, path, error} for a single file on disk."""
    p = Path(path)
    out: dict[str, Any] = {"ok": True, "path": str(p), "error": ""}
    if not p.is_file():
        return {"ok": False, "path": str(p), "error": "not a file"}
    suffix = p.suffix.lower()
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "path": str(p), "error": str(e)}

    if suffix == ".py":
        try:
            # Write to temp so we don't need write on original for compile cache
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as fh:
                fh.write(text)
                tmp = fh.name
            try:
                py_compile.compile(tmp, doraise=True)
            finally:
                with suppress(Exception):
                    Path(tmp).unlink(missing_ok=True)
        except py_compile.PyCompileError as e:
            out["ok"] = False
            out["error"] = str(e)[:500]
        except SyntaxError as e:
            out["ok"] = False
            out["error"] = f"SyntaxError: {e.msg} (line {e.lineno})"
        except Exception as e:
            out["ok"] = False
            out["error"] = str(e)[:300]
        return out

    if suffix == ".json":
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            out["ok"] = False
            out["error"] = f"JSONDecodeError: {e.msg} (line {e.lineno})"
        return out

    # Unknown types: pass (full oracle handles them)
    return out


def check_paths_syntax(paths: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = str(raw or "").strip()
        if not p or p in seen:
            continue
        # Skip shell commands mistaken for paths
        if " " in p and not Path(p).exists():
            continue
        if not p.endswith((".py", ".json")):
            continue
        seen.add(p)
        results.append(check_path_syntax(p))
        if len(results) >= 12:
            break
    return results


def format_syntax_gate_message(results: list[dict[str, Any]]) -> dict[str, str] | None:
    """User inject if any syntax failures; None if all ok / empty."""
    bad = [r for r in results if not r.get("ok")]
    if not bad:
        return None
    lines = [
        "[Build engine · SYNTAX GATE · RED]",
        "Machine compile/parse failed before full tests. Fix syntax first:",
    ]
    for r in bad[:8]:
        lines.append(f"  · {r.get('path')}: {r.get('error')}")
    lines.append("file_edit the broken files, then continue. Do not run full suite yet.")
    return {"role": "user", "content": "\n".join(lines)}
