"""Repository text search (ripgrep if available, pure-Python fallback)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
}
_TEXT_SUFFIXES = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".cs",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
    ".css",
    ".scss",
    ".html",
    ".xml",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".cmd",
    ".ini",
    ".cfg",
    ".env",
    ".gitignore",
    ".rhai",
}


@dataclass
class SearchHit:
    path: str
    line: int
    text: str


def _which_rg() -> str | None:
    return shutil.which("rg") or shutil.which("ripgrep")


def search_repo(
    root: Path,
    pattern: str,
    *,
    path: str = ".",
    glob: str | None = None,
    max_matches: int = 50,
    case_insensitive: bool = False,
    context_before: int = 0,
    context_after: int = 0,
) -> tuple[list[SearchHit], str]:
    """Search under *root*/*path*. Returns (hits, engine_label)."""
    root = root.resolve()
    rel = (path or ".").strip() or "."
    start = (root / rel).resolve() if rel not in (".", "") else root
    try:
        start.relative_to(root)
    except ValueError:
        return [], "error: path outside root"

    if not start.exists():
        return [], "error: path not found"

    max_matches = max(1, min(500, int(max_matches or 50)))
    rg = _which_rg()
    if rg:
        hits = _search_rg(
            rg,
            root,
            start,
            pattern,
            glob=glob,
            max_matches=max_matches,
            case_insensitive=case_insensitive,
            context_before=context_before,
            context_after=context_after,
        )
        return hits, "rg"
    hits = _search_python(
        root,
        start,
        pattern,
        glob=glob,
        max_matches=max_matches,
        case_insensitive=case_insensitive,
    )
    return hits, "python"


def _search_rg(
    rg: str,
    root: Path,
    start: Path,
    pattern: str,
    *,
    glob: str | None,
    max_matches: int,
    case_insensitive: bool,
    context_before: int,
    context_after: int,
) -> list[SearchHit]:
    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        "--max-count",
        str(max_matches),
    ]
    if case_insensitive:
        cmd.append("-i")
    if context_before > 0:
        cmd.extend(["-B", str(min(5, context_before))])
    if context_after > 0:
        cmd.extend(["-A", str(min(5, context_after))])
    if glob:
        cmd.extend(["--glob", glob])
    # Skip heavy dirs
    for d in _SKIP_DIR_NAMES:
        cmd.extend(["--glob", f"!{d}/**"])
    cmd.extend(["--", pattern, str(start)])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            cwd=str(root),
            env={**os.environ, "RIPGREP_CONFIG_PATH": ""},
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    hits: list[SearchHit] = []
    for line in (proc.stdout or "").splitlines():
        # path:line:text  or context lines path-line-text
        m = re.match(r"^(.*?):(\d+)[:\-](.*)$", line)
        if not m:
            continue
        p = Path(m.group(1))
        try:
            rel = p.resolve().relative_to(root).as_posix()
        except Exception:
            rel = m.group(1)
        hits.append(
            SearchHit(path=rel, line=int(m.group(2)), text=m.group(3).rstrip("\n")[:400])
        )
        if len(hits) >= max_matches:
            break
    return hits


def _glob_match(name: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    # Simple suffix / * patterns
    import fnmatch

    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name, pattern.lstrip("*/"))


def _search_python(
    root: Path,
    start: Path,
    pattern: str,
    *,
    glob: str | None,
    max_matches: int,
    case_insensitive: bool,
) -> list[SearchHit]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        cre = re.compile(pattern, flags)
    except re.error:
        cre = re.compile(re.escape(pattern), flags)

    hits: list[SearchHit] = []
    files: list[Path] = []
    if start.is_file():
        files = [start]
    else:
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES and not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                p = Path(dirpath) / fn
                if glob and not _glob_match(fn, glob) and not _glob_match(p.as_posix(), glob):
                    continue
                if p.suffix.lower() not in _TEXT_SUFFIXES and not glob:
                    continue
                files.append(p)
                if len(files) > 5000:
                    break
            if len(files) > 5000:
                break

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:2048]:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if cre.search(line):
                try:
                    rel = fp.resolve().relative_to(root).as_posix()
                except Exception:
                    rel = str(fp)
                hits.append(SearchHit(path=rel, line=i, text=line[:400]))
                if len(hits) >= max_matches:
                    return hits
    return hits


def format_hits(hits: list[SearchHit], *, engine: str, pattern: str) -> str:
    if not hits:
        return f"No matches for {pattern!r} (engine={engine})."
    lines = [f"Found {len(hits)} match(es) for {pattern!r} (engine={engine}):"]
    for h in hits:
        lines.append(f"{h.path}:{h.line}: {h.text}")
    return "\n".join(lines)
