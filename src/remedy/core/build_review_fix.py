"""Implement → review → fix: a second pass over the write set.

After hops/verify, the machine inspects the diff (heuristics, no required LLM)
and turns findings into repair tickets. Frontier agents often stop at green
tests and miss TODO leftovers, bare excepts, or missing mapped tests.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any

_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
_BARE_EXCEPT_RE = re.compile(r"(?m)^\s*except\s*:\s*(?:pass\s*)?(?:#.*)?$")


def _project_root(runtime: Any) -> Path | None:
    with suppress(Exception):
        p = Path(runtime.effective_project_path())
        return p.parent if p.is_file() else p
    return None


def _read(runtime: Any, rel: str, root: Path) -> str:
    with suppress(Exception):
        dest = Path(runtime.resolve_tool_path(rel))
        if dest.is_file():
            return dest.read_text(encoding="utf-8", errors="replace")
    p = root / rel
    if p.is_file():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def collect_diff_findings(
    runtime: Any,
    write_set: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Heuristic findings on the current write set."""
    root = _project_root(runtime)
    findings: list[dict[str, Any]] = []
    if root is None:
        return findings
    paths = [str(p).replace("\\", "/") for p in (write_set or []) if p]
    for rel in paths[:24]:
        body = _read(runtime, rel, root)
        if not body:
            findings.append(
                {"path": rel, "severity": "warn", "kind": "missing", "detail": "written path missing on disk"}
            )
            continue
        if _TODO_RE.search(body):
            findings.append(
                {"path": rel, "severity": "warn", "kind": "todo", "detail": "TODO/FIXME left in write set"}
            )
        if rel.endswith(".py") and _BARE_EXCEPT_RE.search(body):
            findings.append(
                {"path": rel, "severity": "error", "kind": "bare_except", "detail": "bare except: swallows errors"}
            )
        with suppress(Exception):
            from remedy.core.build_lang_oracle import check_lang_syntax

            dest = root / rel
            with suppress(Exception):
                dest = Path(runtime.resolve_tool_path(rel))
            syn = check_lang_syntax(dest)
            if not syn.get("ok"):
                findings.append(
                    {
                        "path": rel,
                        "severity": "error",
                        "kind": "syntax",
                        "detail": str(syn.get("error") or "syntax red")[:240],
                    }
                )
        if rel.endswith((".py", ".ts", ".tsx", ".go", ".rs")) and "/test" not in rel and not Path(rel).name.startswith("test_"):
            with suppress(Exception):
                from remedy.core.build_scoped import map_source_to_test_candidates

                tests = map_source_to_test_candidates(rel, root)
                if not tests:
                    findings.append(
                        {
                            "path": rel,
                            "severity": "warn",
                            "kind": "no_test",
                            "detail": "no mapped test file for this source",
                        }
                    )
    return findings


def review_fix_pass(
    runtime: Any,
    write_set: list[str] | None = None,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    """Review write_set; hop error-severity findings when possible."""
    findings = collect_diff_findings(runtime, write_set)
    errors = [f for f in findings if f.get("severity") == "error"]
    hops: list[dict[str, Any]] = []
    if errors:
        from remedy.core.build_isolated import isolated_unit_hop

        seen: set[str] = set()
        for f in errors[:4]:
            path = str(f.get("path") or "")
            if not path or path in seen:
                continue
            seen.add(path)
            hops.append(
                isolated_unit_hop(
                    runtime,
                    path=path,
                    use_llm=bool(use_llm),
                    max_repairs=2,
                )
            )
    ok = not errors or all(h.get("ok") for h in hops)
    return {
        "ok": ok,
        "findings": findings,
        "errors": len(errors),
        "warns": sum(1 for f in findings if f.get("severity") == "warn"),
        "hops": hops,
        "message": (
            f"review_fix findings={len(findings)} errors={len(errors)} "
            f"hops={sum(1 for h in hops if h.get('ok'))}/{len(hops)}"
        ),
    }


def maybe_review_fix(
    runtime: Any,
    state: Any,
    *,
    use_llm: bool | None = None,
) -> dict[str, Any] | None:
    """Once per turn after writes exist — second pass, not a loop."""
    if state is None or not getattr(state, "active", False):
        return None
    if getattr(state, "review_fix_ran", False):
        return None
    ws = list(getattr(state, "write_set", None) or [])
    if not ws:
        return None
    state.review_fix_ran = True
    if use_llm is None:
        from remedy.core.build_drive import should_use_live_llm

        use_llm = should_use_live_llm(runtime)
    return review_fix_pass(runtime, ws, use_llm=bool(use_llm))


def format_review_fix_message(result: dict[str, Any] | None) -> dict[str, str] | None:
    if not result:
        return None
    lines = ["[Build engine · REVIEW-FIX]", str(result.get("message") or "")]
    for f in (result.get("findings") or [])[:10]:
        lines.append(f"  · {f.get('severity')} {f.get('kind')} {f.get('path')}: {f.get('detail')}")
    if result.get("errors"):
        lines.append("Error findings hopped in isolation. Re-verify; do not ignore them.")
    return {"role": "user", "content": "\n".join(lines)}
