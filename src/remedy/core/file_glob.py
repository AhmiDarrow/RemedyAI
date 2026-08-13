"""Jail-aware file glob — Claude-class discovery without list_dir thrash.

``*.py`` matches any basename in the tree. ``src/**/*.rs`` is a recursive path
glob. Skip-dirs match repo_search so node_modules / .git never flood results.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from remedy.core.repo_search import _SKIP_DIR_NAMES

_DEFAULT_MAX = 80
_HARD_MAX = 400


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
    rel_n = rel.replace("\\", "/").lstrip("./")
    name = Path(rel_n).name
    if "/" not in pat and "**" not in pat:
        return fnmatch.fnmatch(name, pat)
    if "**" in pat:
        rx = _glob_to_re(pat)
        return bool(rx.fullmatch(rel_n) or rx.fullmatch(name))
    return fnmatch.fnmatch(rel_n, pat) or fnmatch.fnmatch(name, pat)


def glob_files(
    root: Path | str,
    pattern: str,
    *,
    max_results: int = _DEFAULT_MAX,
) -> list[str]:
    """Walk *root* and return relative posix paths matching *pattern*."""
    base = Path(root)
    if not base.is_dir():
        return []
    try:
        cap = max(1, min(_HARD_MAX, int(max_results or _DEFAULT_MAX)))
    except (TypeError, ValueError):
        cap = _DEFAULT_MAX
    pat = (pattern or "").replace("\\", "/").strip() or "**/*"
    out: list[str] = []
    base_res = base.resolve()
    for dirpath, dirnames, filenames in os.walk(base_res):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIR_NAMES and not (d.startswith(".") and d not in {".remedy-build"})
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
                    return out
    return out


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
