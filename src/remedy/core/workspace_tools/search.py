"""Workspace tools — search."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.react_policy import (
    FILE_READ_CHAR_CAP as _FILE_READ_CHAR_CAP,
)
from remedy.core.react_policy import (
    HARD_SAFETY_CHARS as _HARD_SAFETY_CHARS,
)
from remedy.core.security import check_dangerous_command
from remedy.core.workspace_tools.guards import (
    FULL_WRITE_PREFER_EDIT_BYTES as _FULL_WRITE_PREFER_EDIT_BYTES,
    HISTORY_STUB_MARKERS as _HISTORY_STUB_MARKERS,
    TINY_REWRITE_ABS as _TINY_REWRITE_ABS,
    TINY_REWRITE_RATIO as _TINY_REWRITE_RATIO,
    junk_write_guard,
    normalize_edits_arg as _normalize_edits_arg,
    note_path,
    parent_hint,
    reserved_guard,
    track_read,
)


def register_search_tools(runtime: Any) -> None:
    """Register search workspace tools."""
    def _parent_hint(path: str) -> str:
        return parent_hint(path)

    def _reserved_guard(path: str) -> str | None:
        return reserved_guard(path)

    def _junk_write_guard(path: str) -> str | None:
        return junk_write_guard(path)

    def _note_path(target: Path) -> None:
        note_path(runtime, target)

    def _track_read(target: Path) -> None:
        track_read(runtime, target)

    async def repo_search(
        pattern: str = "",
        path: str = ".",
        glob: str = "",
        max_matches: int = 50,
        case_insensitive: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        symbol: str = "",
    ) -> str:
        """Search text under path (any language). Prefer absolute path when multi-tree."""
        from remedy.core.repo_search import (
            SearchHit,
            format_hits,
            search_repo,
            symbol_search_patterns,
        )

        root = runtime.effective_project_path()
        raw_path = (path or ".").strip() or "."
        search_path = raw_path
        # Fail closed: never fall back to raw absolute paths outside jail.
        if raw_path not in (".", "./", ""):
            try:
                resolved = runtime.resolve_tool_path(raw_path)
            except Exception as e:
                return format_tool_error(
                    f"path not allowed or unresolvable: {raw_path} ({e})",
                    code="PATH_DENIED",
                    tool_name="repo_search",
                    suggestion=(
                        "Use a path under the project/access scope, or list_dir "
                        "on '.' to discover valid paths."
                    ),
                )
            search_path = str(resolved)
            if resolved.exists():
                _note_path(resolved)

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        sym = (symbol or "").strip()
        if sym and not (pattern or "").strip():
            # Definition-oriented multi-pattern search
            all_hits: list[SearchHit] = []
            engine_used = "python"
            for pat in symbol_search_patterns(sym):
                hits, engine = search_repo(
                    root,
                    pat,
                    path=search_path,
                    glob=(glob or None) or None,
                    max_matches=max(5, int(max_matches or 50) // 2),
                    case_insensitive=True,
                    context_before=int(context_before or 0),
                    context_after=int(context_after or 0),
                    home_dir=home,
                )
                engine_used = engine
                for h in hits:
                    if not any(
                        x.path == h.path and x.line == h.line for x in all_hits
                    ):
                        all_hits.append(h)
                if len(all_hits) >= int(max_matches or 50):
                    break
            return format_hits(
                all_hits[: int(max_matches or 50)],
                engine=engine_used,
                pattern=f"symbol:{sym}",
            )

        if not (pattern or "").strip():
            return format_tool_error(
                "pattern or symbol is required",
                code="MISSING_PATTERN",
                tool_name="repo_search",
                suggestion=(
                    'repo_search(pattern="class_name Foo", path="src") '
                    'or repo_search(symbol="WorldGenerator", path=...)'
                ),
            )

        hits, engine = search_repo(
            root,
            pattern.strip(),
            path=search_path,
            glob=(glob or None) or None,
            max_matches=int(max_matches or 50),
            case_insensitive=bool(case_insensitive),
            context_before=int(context_before or 0),
            context_after=int(context_after or 0),
            home_dir=home,
        )
        return format_hits(hits, engine=engine, pattern=pattern.strip())

    async def list_dir(
        path: str = ".",
        limit: int = 200,
        offset: int = 0,
    ) -> str:
        bad = _reserved_guard(path)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="list_dir",
                suggestion="Skip reserved device paths; list the parent directory.",
            )
        root = runtime.effective_project_path()
        target = runtime.resolve_tool_path(path)
        _note_path(target)
        if not target.exists():
            parent = _parent_hint(path)
            return format_tool_error(
                f"path not found: {path}",
                code="NOT_FOUND",
                tool_name="list_dir",
                suggestion=(
                    f"Call list_dir on '{parent}' or default cwd ('.') "
                    "to find the correct directory name."
                ),
            )
        if not target.is_dir():
            return format_tool_error(
                f"not a directory: {path}",
                code="NOT_A_DIRECTORY",
                tool_name="list_dir",
                suggestion=f'Use file_read("{path}") for file contents instead.',
            )
        # Default page size 200; hard safety cap 2000 per call
        try:
            lim = max(1, min(2000, int(limit or 200)))
        except (TypeError, ValueError):
            lim = 200
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            off = 0
        lines: list[str] = []
        total = 0
        try:
            entries = sorted(
                target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())
            )
            visible = [p for p in entries if not p.name.startswith(".")]
            total = len(visible)
            page = visible[off : off + lim]
            for p in page:
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    rel = str(p)
                lines.append(f"{'dir ' if p.is_dir() else 'file'} {rel}")
        except OSError as e:
            return format_tool_error(
                f"cannot list {path}: {e}",
                code="IO_ERROR",
                tool_name="list_dir",
                suggestion="Retry with project root '.' or a known subdirectory.",
            )
        if not lines:
            return "(empty)"
        footer = ""
        shown = off + len(lines)
        if shown < total:
            footer = (
                f"\n… showing {off + 1}-{shown} of {total}; "
                f'list_dir(path="{path}", limit={lim}, offset={shown}) for more'
            )
        elif off > 0:
            footer = f"\n… showing {off + 1}-{shown} of {total}"
        return "\n".join(lines) + footer


    runtime.tool_registry.register_builtin_handler(
        "repo_search",
        "Search text by regex/literal (bundled/system ripgrep, else content-sniff). "
        "Any language — no extension allowlist. Prefer absolute path when multi-tree. "
        "Use symbol= for definition-oriented search. context_before/after for snippets.",
        repo_search,
        {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex supported)",
                },
                "symbol": {
                    "type": "string",
                    "description": "Find definitions of this symbol (class/func/etc.)",
                },
                "path": {
                    "type": "string",
                    "description": "Subdirectory, file, or absolute tree (default: focus/cwd)",
                    "default": ".",
                },
                "glob": {
                    "type": "string",
                    "description": "Optional file glob e.g. *.py",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "Max hits (default 50, max 500)",
                    "default": 50,
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search",
                    "default": False,
                },
                "context_before": {
                    "type": "integer",
                    "description": "Context lines before each hit (0-5)",
                    "default": 0,
                },
                "context_after": {
                    "type": "integer",
                    "description": "Context lines after each hit (0-5)",
                    "default": 0,
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "list_dir",
        "List files and directories under allowed roots (see access scope). "
        "Default limit=200; use offset for the next page. Absolute paths OK.",
        list_dir,
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute directory (default: focus/cwd)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 200, max 2000)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Skip this many entries (pagination)",
                },
            },
        },
    )

