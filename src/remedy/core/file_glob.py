"""Jail-aware file glob — Claude-class discovery without list_dir thrash.

``*.py`` matches any basename in the tree. ``src/**/*.rs`` is a recursive path
glob. Skip-dirs match repo_search so node_modules / .git never flood results.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from remedy.core.relpath import norm_rel
from remedy.core.repo_search import (
    _SKIP_DIR_NAMES,
    _should_skip_huge_dir,
    is_huge_root,
)

_DEFAULT_MAX = 80
_HARD_MAX = 400
# Same class of stall as repo_search's Python walk: a home-sized glob
# must not occupy a worker until os.walk finishes.
GLOB_WALK_BUDGET_S = 8.0


def _glob_to_re(pattern: str) -> re.Pattern[str]:
    """Convert a glob (with optional ``**``) to a fullmatch regex.

    ``**/`` matches zero or more directories so ``src/**/*.ts`` hits
    both ``src/x.ts`` and ``src/pkg/x.ts``.
    """
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile(rf"^{''.join(out)}$")


def match_glob(rel: str, pattern: str) -> bool:
    """True when *rel* (posix, relative) or its basename matches *pattern*."""
    pat = (pattern or "").replace("\\", "/").strip()
    if not pat:
        return True
    rel_n = norm_rel(rel)
    name = Path(rel_n).name
    if "/" not in pat and "**" not in pat:
        return fnmatch.fnmatch(name, pat)
    if "**" in pat:
        rx = _glob_to_re(pat)
        return bool(rx.fullmatch(rel_n) or rx.fullmatch(name))
    return fnmatch.fnmatch(rel_n, pat) or fnmatch.fnmatch(name, pat)


@dataclass
class GlobResult:
    hits: list[str]
    truncated: bool = False


def glob_search(
    root: Path | str,
    pattern: str,
    *,
    max_results: int = _DEFAULT_MAX,
    time_budget_s: float = GLOB_WALK_BUDGET_S,
) -> GlobResult:
    """Walk *root* for *pattern*. Caps hits and wall-clock so huge trees cannot stall."""
    base = Path(root)
    if not base.is_dir():
        return GlobResult([])
    try:
        cap = max(1, min(_HARD_MAX, int(max_results or _DEFAULT_MAX)))
    except (TypeError, ValueError):
        cap = _DEFAULT_MAX
    pat = (pattern or "").replace("\\", "/").strip() or "**/*"
    out: list[str] = []
    try:
        base_res = base.resolve()
    except Exception:
        return GlobResult([])
    huge = is_huge_root(base_res)
    try:
        budget = float(time_budget_s)
    except (TypeError, ValueError):
        budget = GLOB_WALK_BUDGET_S
    if budget <= 0:
        budget = GLOB_WALK_BUDGET_S
    deadline = time.monotonic() + budget
    truncated = False
    for dirpath, dirnames, filenames in os.walk(base_res):
        if time.monotonic() > deadline:
            truncated = True
            break
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIR_NAMES
            and not (d.startswith(".") and d not in {".remedy-build"})
            and not (huge and _should_skip_huge_dir(d))
        ]
        for fn in filenames:
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(base_res).as_posix()
            except ValueError:
                continue
            if match_glob(rel, pat):
                out.append(rel)
                if len(out) >= cap:
                    return GlobResult(out, truncated=True)
    return GlobResult(out, truncated=truncated)


def glob_files(
    root: Path | str,
    pattern: str,
    *,
    max_results: int = _DEFAULT_MAX,
) -> list[str]:
    """Walk *root* and return relative posix paths matching *pattern*."""
    return glob_search(root, pattern, max_results=max_results).hits


def format_glob_hits(
    hits: list[str],
    *,
    pattern: str,
    truncated: bool = False,
) -> str:
    if not hits:
        return (
            f"file_glob: 0 hits for `{pattern}`.\n"
            "Recover: list_dir the intended tree, simplify the pattern "
            "(e.g. `*.py`), or pass an absolute path= under access scope."
        )
    lines = [f"file_glob: {len(hits)} hit(s) for `{pattern}`"]
    lines.extend(f"  {h}" for h in hits)
    if truncated:
        lines.append("… truncated; raise max_results or narrow the pattern.")
    return "\n".join(lines)
