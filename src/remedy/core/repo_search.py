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
        hits, rg_ok = _search_rg(
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
        # Fall back if rg missing results due to crash/bad flags (not merely no matches)
        if rg_ok:
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


def _parse_rg_line(line: str) -> tuple[str, int, str] | None:
    """Parse ``path:line:text`` robustly (Windows drive letters use ``:``)."""
    # Match the *last* :digits: or :digits- segment so ``C:\a\b.py:12:code`` works.
    m = re.search(r":(\d+)([:\-])(.*)$", line)
    if not m:
        return None
    path = line[: m.start()]
    if not path:
        return None
    try:
        lineno = int(m.group(1))
    except ValueError:
        return None
    return path, lineno, m.group(3)


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
) -> tuple[list[SearchHit], bool]:
    """Returns (hits, ok). ok=False means fall back to pure Python."""
    cmd = [
        rg,
        "--line-number",
        "--no-heading",
        "--color",
        "never",
        # Per-file cap high; we enforce total max_matches while parsing.
        "--max-count",
        str(max(1, min(100, max_matches))),
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
        return [], False
    # 0 = matches, 1 = no matches, 2 = error (bad regex / etc.)
    if proc.returncode not in (0, 1):
        return [], False
    hits: list[SearchHit] = []
    for line in (proc.stdout or "").splitlines():
        parsed = _parse_rg_line(line)
        if not parsed:
            continue
        raw_path, lineno, text = parsed
        p = Path(raw_path)
        try:
            p = (root / p).resolve() if not p.is_absolute() else p.resolve()
            rel = p.relative_to(root).as_posix()
        except Exception:
            rel = raw_path.replace("\\", "/")
        hits.append(SearchHit(path=rel, line=lineno, text=text.rstrip("\n")[:400]))
        if len(hits) >= max_matches:
            break
    return hits, True


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
