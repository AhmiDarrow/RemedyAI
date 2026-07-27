"""Repository text search (bundled/system ripgrep, pure-Python text-sniff fallback).

Language-agnostic: no exclusive file-extension allowlist. Prefer rg when available
(gitignore-aware); otherwise sniff text vs binary and search.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from remedy.core.text_files import (
    DEFAULT_MAX_SEARCH_FILE_BYTES,
    should_search_file,
)

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
    ".godot",  # Godot cache/import noise
    ".next",
    ".nuxt",
    ".turbo",
    ".cache",
    "Pods",
    "Carthage",
}

# Soft cap for pure-Python walk when scanning large trees without rg.
_MAX_PYTHON_FILES = 8000

# Empty-search recovery hint (models should re-scope, not invent symbols).
EMPTY_SEARCH_HINT = (
    "No matches. Recover: confirm the search path (use an absolute path to the "
    "tree you mean), list_dir that path, try a simpler pattern, or broaden/narrow "
    "scope. Do not invent file paths or symbols."
)


@dataclass
class SearchHit:
    path: str
    line: int
    text: str


def _resolve_start(
    root: Path,
    path: str,
    *,
    allowed_roots: list[Path] | None = None,
    access_scope: str = "project",
) -> tuple[Path, Path, str | None]:
    """Return (display_root, start, error).

    Absolute *path* is allowed when it stays under *allowed_roots* (or full
    scope). Relative paths resolve under *root*. Callers that already jailed
    via ``resolve_tool_path`` may pass the resolved path without extra roots.
    """
    root = root.expanduser()
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()

    raw = (path or ".").strip() or "."
    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            start = p.resolve()
        except OSError:
            start = p.absolute()
        if not start.exists():
            return root, start, "error: path not found"
        # When allowed_roots provided, enforce jail (fail closed).
        if allowed_roots is not None:
            scope = (access_scope or "project").strip().lower()
            if scope not in ("full", "machine", "all", "unrestricted"):
                ok = False
                for r in allowed_roots:
                    try:
                        rr = r.resolve() if r.exists() else Path(r).absolute()
                    except OSError:
                        rr = Path(r).absolute()
                    try:
                        start.relative_to(rr)
                        ok = True
                        break
                    except ValueError:
                        continue
                if not ok:
                    return root, start, "error: path outside root"
        display_root = start if start.is_dir() else start.parent
        return display_root, start, None

    start = (root / raw).resolve() if raw not in (".", "") else root
    try:
        start.relative_to(root)
    except ValueError:
        return root, start, "error: path outside root"
    if not start.exists():
        return root, start, "error: path not found"
    return root, start, None


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
    prefer_system_rg: bool = False,
    force_python: bool = False,
    home_dir: str | Path | None = None,
    allowed_roots: list[Path] | None = None,
    access_scope: str = "project",
) -> tuple[list[SearchHit], str]:
    """Search under *root*/*path* (or absolute *path*). Returns (hits, engine_label)."""
    display_root, start, err = _resolve_start(
        Path(root),
        path,
        allowed_roots=allowed_roots,
        access_scope=access_scope,
    )
    if err:
        return [], err

    max_matches = max(1, min(500, int(max_matches or 50)))

    # Warn when scanning a huge root without a narrow path (anti-thrash).
    huge_note = ""
    try:
        if start.is_dir() and start.resolve() == Path.home().resolve():
            huge_note = "huge-root"
    except Exception:
        pass

    if not force_python:
        try:
            from remedy.core.rg_binary import engine_label, find_rg, schedule_ensure_rg

            rg_path, source = find_rg(home_dir, prefer_system=prefer_system_rg)
            if rg_path is None:
                # Non-blocking: install in background; use Python this call.
                schedule_ensure_rg(home_dir)
            if rg_path is not None:
                hits, rg_ok = _search_rg(
                    str(rg_path),
                    display_root,
                    start,
                    pattern,
                    glob=glob,
                    max_matches=max_matches,
                    case_insensitive=case_insensitive,
                    context_before=context_before,
                    context_after=context_after,
                )
                if rg_ok:
                    label = engine_label(source)
                    if huge_note:
                        label = f"{label}+{huge_note}"
                    return hits, label
        except Exception:
            pass

    hits = _search_python(
        display_root,
        start,
        pattern,
        glob=glob,
        max_matches=max_matches,
        case_insensitive=case_insensitive,
        context_before=context_before,
        context_after=context_after,
    )
    label = "python"
    if huge_note:
        label = f"{label}+{huge_note}"
    return hits, label


def _parse_rg_line(line: str) -> tuple[str, int, str] | None:
    """Parse ``path:line:text`` robustly (Windows drive letters use ``:``)."""
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
            cwd=str(root if root.is_dir() else root.parent),
            env={**os.environ, "RIPGREP_CONFIG_PATH": ""},
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], False
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
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                rel = str(p).replace("\\", "/")
        except Exception:
            rel = raw_path.replace("\\", "/")
        hits.append(SearchHit(path=rel, line=lineno, text=text.rstrip("\n")[:400]))
        if len(hits) >= max_matches:
            break
    return hits, True


def _glob_match(name: str, pattern: str | None) -> bool:
    if not pattern:
        return True
    import fnmatch

    return fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(name, pattern.lstrip("*/"))


def _should_skip_dir(name: str) -> bool:
    if name in _SKIP_DIR_NAMES:
        return True
    # Skip most hidden dirs; keep common config roots searchable by name walk
    # only when not in skip list. Dot-dirs that are pure caches are listed above.
    return name.startswith(".") and name not in (
        ".github",
        ".gitlab",
        ".circleci",
        ".config",
    )


def _load_gitignore_names(root: Path) -> set[str]:
    """Best-effort: directory/file basenames mentioned in .gitignore (simple lines)."""
    names: set[str] = set()
    gi = root / ".gitignore"
    if not gi.is_file():
        return names
    try:
        text = gi.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        # Only simple single-segment patterns (foo/, *.pyc skipped except suffix)
        s = s.strip("/")
        if "/" in s or s.startswith("*"):
            # *.ext → skip via suffix later; bare dir names only
            if s.startswith("*.") and s.count("*") == 1:
                continue
            continue
        if s:
            names.add(s)
    return names


def _search_python(
    root: Path,
    start: Path,
    pattern: str,
    *,
    glob: str | None,
    max_matches: int,
    case_insensitive: bool,
    context_before: int = 0,
    context_after: int = 0,
) -> list[SearchHit]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        cre = re.compile(pattern, flags)
    except re.error:
        cre = re.compile(re.escape(pattern), flags)

    ignore_names = _load_gitignore_names(root if root.is_dir() else root.parent)
    # Cap walk when searching home-sized trees
    file_cap = _MAX_PYTHON_FILES
    try:
        if start.is_dir() and start.resolve() == Path.home().resolve():
            file_cap = min(file_cap, 2000)
    except Exception:
        pass

    hits: list[SearchHit] = []
    files: list[Path] = []
    if start.is_file():
        files = [start] if should_search_file(start) or glob else []
    else:
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = [
                d
                for d in dirnames
                if not _should_skip_dir(d) and d not in ignore_names
            ]
            for fn in filenames:
                if fn in ignore_names:
                    continue
                p = Path(dirpath) / fn
                if glob and not _glob_match(fn, glob) and not _glob_match(p.as_posix(), glob):
                    continue
                if not should_search_file(p, max_file_bytes=DEFAULT_MAX_SEARCH_FILE_BYTES):
                    continue
                files.append(p)
                if len(files) > file_cap:
                    break
            if len(files) > file_cap:
                break

    ctx_b = max(0, min(5, int(context_before or 0)))
    ctx_a = max(0, min(5, int(context_after or 0)))

    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\x00" in text[:2048]:
            continue
        all_lines = text.splitlines()
        for i, line in enumerate(all_lines, 1):
            if cre.search(line):
                try:
                    rel = fp.resolve().relative_to(root).as_posix()
                except Exception:
                    rel = str(fp).replace("\\", "/")
                if ctx_b or ctx_a:
                    lo = max(0, i - 1 - ctx_b)
                    hi = min(len(all_lines), i + ctx_a)
                    chunk = all_lines[lo:hi]
                    body = "\n".join(chunk)[:800]
                else:
                    body = line[:400]
                hits.append(SearchHit(path=rel, line=i, text=body))
                if len(hits) >= max_matches:
                    return hits
    return hits


def _record_search_metrics(engine: str, hit_count: int) -> None:
    try:
        from remedy.core.metrics import default_registry

        eng = (engine or "unknown").split("+")[0].split(":")[0]
        default_registry.counter("remedy_repo_search_total", engine=eng).inc()
        if hit_count <= 0 and not str(engine).startswith("error"):
            default_registry.counter("remedy_repo_search_empty_total", engine=eng).inc()
        if "huge-root" in str(engine):
            default_registry.counter("remedy_repo_search_huge_root_total").inc()
    except Exception:
        pass


def format_hits(hits: list[SearchHit], *, engine: str, pattern: str) -> str:
    _record_search_metrics(engine, len(hits))
    if not hits:
        extra = ""
        if "huge-root" in str(engine):
            extra = (
                "\nNote: search started at a very large root (e.g. home). "
                "Pass path= to a specific project directory."
            )
        return (
            f"No matches for {pattern!r} (engine={engine}).\n"
            f"{EMPTY_SEARCH_HINT}{extra}"
        )
    lines = [f"Found {len(hits)} match(es) for {pattern!r} (engine={engine}):"]
    if "huge-root" in str(engine):
        lines.append(
            "(warning: large root — prefer absolute path= to the repo next time)"
        )
    for h in hits:
        if "\n" in h.text:
            lines.append(f"{h.path}:{h.line}:")
            for sub in h.text.splitlines():
                lines.append(f"  {sub}")
        else:
            lines.append(f"{h.path}:{h.line}: {h.text}")
    return "\n".join(lines)


def symbol_search_patterns(symbol: str) -> list[str]:
    """Best-effort definition patterns for a symbol (language-light)."""
    sym = (symbol or "").strip()
    if not sym:
        return []
    # Escape for regex
    e = re.escape(sym)
    return [
        rf"\bclass_name\s+{e}\b",
        rf"\bclass\s+{e}\b",
        rf"\bdef\s+{e}\s*\(",
        rf"\bfunc\s+{e}\s*\(",
        rf"\bfunction\s+{e}\s*\(",
        rf"\bfn\s+{e}\s*[\(\{{]",
        rf"\b(const|let|var|type)\s+{e}\b",
        rf"^{e}\s*=",
    ]
