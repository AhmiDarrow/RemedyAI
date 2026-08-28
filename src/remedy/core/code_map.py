"""Lightweight on-PC symbol map — definitions without a cloud index.

Walks a tree (same skip-dirs / time budget as file_glob) and records
class/def/fn names. Not embeddings. Any provider can query it instead of
serial list_dir + grep.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from remedy.core.repo_search import _SKIP_DIR_NAMES, _should_skip_huge_dir, is_huge_root

MAP_WALK_BUDGET_S = 8.0
_HARD_MAX = 400
_DEF_RE = re.compile(
    r"(?m)^(?:export\s+)?(?:async\s+)?(?:pub(?:lic)?\s+)?"
    r"(?P<kind>class|def|fn|func|function|interface|struct|enum|type)\s+"
    r"(?P<name>[A-Za-z_][\w]*)"
)


@dataclass
class MapHit:
    name: str
    kind: str
    path: str
    line: int


def build_code_map(
    root: Path | str,
    *,
    query: str = "",
    max_hits: int = 80,
    time_budget_s: float = MAP_WALK_BUDGET_S,
) -> list[MapHit]:
    """Return definition hits under *root*, optionally filtered by *query*."""
    base = Path(root)
    if not base.is_dir():
        return []
    try:
        cap = max(1, min(_HARD_MAX, int(max_hits or 80)))
    except (TypeError, ValueError):
        cap = 80
    q = (query or "").strip().lower()
    try:
        base_res = base.resolve()
    except Exception:
        return []
    huge = is_huge_root(base_res)
    try:
        budget = float(time_budget_s)
    except (TypeError, ValueError):
        budget = MAP_WALK_BUDGET_S
    if budget <= 0:
        budget = MAP_WALK_BUDGET_S
    deadline = time.monotonic() + budget
    hits: list[MapHit] = []
    for dirpath, dirnames, filenames in os.walk(base_res):
        if time.monotonic() > deadline:
            break
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIR_NAMES
            and not (d.startswith(".") and d not in {".remedy-build"})
            and not (huge and _should_skip_huge_dir(d))
        ]
        for fn in filenames:
            if not re.search(r"\.(py|ts|tsx|js|jsx|rs|go|java|kt)$", fn, re.I):
                continue
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(base_res).as_posix()
            except ValueError:
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines()[:400], 1):
                m = _DEF_RE.match(line.lstrip())
                if not m:
                    continue
                name = m.group("name")
                if q and q not in name.lower() and q not in rel.lower():
                    continue
                hits.append(
                    MapHit(name=name, kind=m.group("kind"), path=rel, line=i)
                )
                if len(hits) >= cap:
                    return hits
            if time.monotonic() > deadline:
                return hits
    return hits


def format_code_map(hits: list[MapHit], *, query: str = "") -> str:
    if not hits:
        q = f" for `{query}`" if query else ""
        return (
            f"code_map: 0 symbols{q}. "
            "Recover: repo_search(symbol=…) or file_glob a tighter tree."
        )
    lines = [f"code_map: {len(hits)} symbol(s)" + (f" matching `{query}`" if query else "")]
    lines.extend(f"  {h.kind} {h.name}  {h.path}:{h.line}" for h in hits)
    return "\n".join(lines)
