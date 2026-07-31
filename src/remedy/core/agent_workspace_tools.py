"""Workspace file/shell tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

import re
from contextlib import suppress
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

# Agent scaffold dumps that should never land in product trees.
_JUNK_WRITE_NAME_RE = re.compile(
    r"(?i)"
    r"(?:^|[/\\])_ref_[^/\\]+$"
    r"|(?:^|[/\\])_ex_[a-z0-9]+(?:\.[^/\\]+)?$"
    r"|(?:^|[/\\])_write_[^/\\]+\.py$"
    r"|(?:^|[/\\])_patch_[^/\\]+\.py$"
    r"|(?:^|[/\\])_vault_tail\.txt$"
)

# Existing files this large should use file_edit unless force_full_write.
_FULL_WRITE_PREFER_EDIT_BYTES = 4_000
# Tiny absolute/relative size change via full rewrite → refuse (use file_edit).
_TINY_REWRITE_ABS = 120
_TINY_REWRITE_RATIO = 0.02

# Provider-history stubs must never be written back to disk (agent echo bug).
_HISTORY_STUB_MARKERS = (
    "[file_write content omitted",
    "omitted from provider history",
    "_history_summarized",
    "<<NOT_SOURCE_CODE",
    "DO_NOT_file_write_this_string",
    "history_stub kind=",
)


def _normalize_edits_arg(edits: Any) -> str:
    """Accept JSON string or already-parsed list/dict for edits= parameters."""
    import json as _json

    if edits is None:
        return ""
    if isinstance(edits, (list, dict)):
        return _json.dumps(edits, ensure_ascii=False)
    return str(edits)


def register_workspace_tools(runtime: Any) -> None:
    """Register file/shell tools jailed to the project workspace."""

    def _parent_hint(path: str) -> str:
        p = (path or ".").strip() or "."
        if p in (".", "./", ""):
            return "."
        parent = Path(p).parent.as_posix()
        return parent if parent not in ("", ".") else "."

    def _reserved_guard(path: str) -> str | None:
        from remedy.core.win_paths import check_tool_path_safe

        return check_tool_path_safe(path)

    def _junk_write_guard(path: str) -> str | None:
        p = (path or "").strip().replace("\\", "/")
        if not p:
            return None
        if _JUNK_WRITE_NAME_RE.search(p):
            return (
                f"refusing junk scaffold path {path!r}: do not write _ref_*, "
                "_ex_*, _write_*.py, or _patch_*.py into the project. "
                "Read reference sources from their real location; edit the "
                "real target with file_edit / file_write."
            )
        return None

    def _note_path(target: Path) -> None:
        with suppress(Exception):
            from remedy.core.work_roots import note_work_path

            note_work_path(runtime, target)

    def _track_read(target: Path) -> None:
        with suppress(Exception):
            key = str(target.resolve()).lower()
            reads = getattr(runtime, "_files_read_this_turn", None)
            if not isinstance(reads, set):
                reads = set()
                runtime._files_read_this_turn = reads
            reads.add(key)

    async def file_read(
        path: str = ".",
        offset: int = 0,
        limit: int | None = None,
        **_kwargs: object,
    ) -> str:
        runtime.effective_project_path()
        bad = _reserved_guard(path)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="file_read",
                suggestion="Skip reserved device paths (e.g. nul); use list_dir on the parent.",
            )
        target = runtime.resolve_tool_path(path)
        _note_path(target)
        if not target.exists():
            parent = _parent_hint(path)
            return format_tool_error(
                f"file not found: {path}",
                code="NOT_FOUND",
                tool_name="file_read",
                suggestion=(
                    f"Call list_dir on '{parent}' or project root ('.') "
                    "to discover the correct path, then retry file_read."
                ),
            )
        if target.is_dir():
            return format_tool_error(
                f"path is a directory: {path}",
                code="IS_DIRECTORY",
                tool_name="file_read",
                suggestion=(
                    f'Use list_dir("{path}") then file_read on a specific file inside it.'
                ),
            )
        try:
            from remedy.core.text_files import is_probably_text

            if not is_probably_text(target):
                return format_tool_error(
                    f"binary or non-text file: {path}",
                    code="BINARY",
                    tool_name="file_read",
                    suggestion="Use another tool for binaries; file_read is for text sources.",
                )
        except Exception:
            pass
        try:
            data = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return format_tool_error(
                f"cannot read {path}: {e}",
                code="IO_ERROR",
                tool_name="file_read",
                suggestion="Check permissions or try list_dir on the parent path.",
            )
        # Optional line window (models often pass offset/limit like list_dir).
        # Full file remains default; only emergency OOM guard after slicing.
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            off = 0
        lim: int | None
        try:
            lim = None if limit is None else max(1, int(limit))
        except (TypeError, ValueError):
            lim = None
        if off or lim is not None:
            lines = data.splitlines(keepends=True)
            end = len(lines) if lim is None else min(len(lines), off + lim)
            start = min(off, len(lines))
            sliced = "".join(lines[start:end])
            suffix = ""
            if end < len(lines):
                suffix = (
                    f"\n\n... [lines {start+1}-{end} of {len(lines)}; "
                    f"file_read path={path!r} offset={end} limit={lim or 200} for more]"
                )
            elif start > 0 or lim is not None:
                suffix = f"\n\n... [lines {start+1}-{end} of {len(lines)}]"
            data = sliced + suffix
        cap = _FILE_READ_CHAR_CAP if _FILE_READ_CHAR_CAP > 0 else _HARD_SAFETY_CHARS
        if len(data) > cap:
            return (
                data[:cap]
                + f"\n\n... [safety cap {cap} chars of {len(data)} — "
                f"read a smaller path or a specific section if needed]"
            )
        runtime._track_artifact(str(target))
        _track_read(target)
        return data

    async def file_write(
        path: str,
        content: str = "",
        force_full_write: bool = False,
    ) -> str:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id

        bad = _reserved_guard(path)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="file_write",
                suggestion="Choose a normal filename; never write Windows device names (nul, con, …).",
            )
        junk = _junk_write_guard(path)
        if junk:
            return format_tool_error(
                junk,
                code="JUNK_PATH",
                tool_name="file_write",
                suggestion=(
                    "file_read the real source (e.g. sibling project path) and "
                    "file_edit the real target file."
                ),
            )
        new_body = content if content is not None else ""
        # Refuse writing provider-history summaries as file bodies (corrupts tree).
        head = (new_body or "")[:240]
        body_s = new_body if isinstance(new_body, str) else str(new_body)
        if (
            any(m in head for m in _HISTORY_STUB_MARKERS)
            or any(m in body_s for m in _HISTORY_STUB_MARKERS)
            or (
                body_s.strip().startswith("[")
                and "omitted from provider history" in body_s
            )
            or (
                isinstance(content, dict)
                and (
                    content.get("_invalid_json")
                    or content.get("_truncated")
                    or content.get("_history_summarized")
                )
            )
        ):
            return format_tool_error(
                "refusing to write provider-history summary stub as file content "
                f"({path}). That text is not source code — it was redacted for the LLM.",
                code="HISTORY_STUB",
                tool_name="file_write",
                suggestion=(
                    "file_read the real path first; then file_edit surgical hunks, "
                    "or file_write with the full real source (force_full_write=true "
                    "if replacing a large existing file)."
                ),
            )
        # Path jail before approval so denied scopes never create Ask tickets.
        try:
            target = runtime.resolve_tool_path(path, for_write=True)
        except Exception as e:
            return format_tool_error(
                f"path not allowed for write: {path} ({e})",
                code="PATH_DENIED",
                tool_name="file_write",
                suggestion=(
                    "Writes stay inside the project folder under project scope. "
                    "Use a path under the focus folder, or raise access_scope "
                    "to home/full in Settings for multi-tree edits."
                ),
            )
        sid = turn_session_id(runtime)
        ask_reason = APPROVALS.needs_ask(f"write {path}", tool_name="file_write")
        if ask_reason and not APPROVALS.is_approved(
            "file_write", f"write {path}", session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="file_write",
                command=f"write {path}",
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"path={path}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry file_write."
            )
        _note_path(target)
        # Capture prior content for time-travel undo (best-effort).
        existed = False
        previous: str | None = None
        try:
            if target.is_file():
                existed = True
                previous = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            previous = None
        # Prefer surgical edits for existing large files (unless forced).
        if (
            existed
            and previous is not None
            and not force_full_write
            and len(previous) >= _FULL_WRITE_PREFER_EDIT_BYTES
        ):
            old_len = len(previous)
            new_len = len(new_body)
            delta = abs(new_len - old_len)
            tiny = delta <= max(
                _TINY_REWRITE_ABS, int(old_len * _TINY_REWRITE_RATIO)
            )
            if tiny and new_body != previous:
                return format_tool_error(
                    (
                        f"file_write refused: {path} already exists ({old_len} chars) and "
                        f"new content is only ±{delta} chars different. "
                        "Use file_edit / file_edit_batch for small changes."
                    ),
                    code="PREFER_FILE_EDIT",
                    tool_name="file_write",
                    suggestion=(
                        'file_edit(path=..., old_string="…", new_string="…") or '
                        "file_write(..., force_full_write=true) only for intentional full rewrites."
                    ),
                )
            if not tiny and new_body == previous:
                return f"No change: {path} already has identical content ({old_len} chars)."

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(new_body, encoding="utf-8")
        except OSError as e:
            parent = _parent_hint(path)
            return format_tool_error(
                f"cannot write {path}: {e}",
                code="IO_ERROR",
                tool_name="file_write",
                suggestion=(
                    f"Verify the parent path with list_dir('{parent}') "
                    "and ensure the path is inside allowed roots "
                    f"(access scope: {runtime.access_scope()})."
                ),
            )
        runtime._track_artifact(str(target))
        try:
            from remedy.core.time_travel import SessionUndoLog

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            SessionUndoLog(home).record_file_write(
                session_id=str(sid or getattr(runtime, "_session_id", "") or ""),
                path=target,
                previous_content=previous,
                existed=existed,
                new_size=len(new_body or ""),
                message_id=getattr(runtime, "_active_message_id", None),
            )
        except Exception:
            pass
        note = ""
        if existed and previous is not None and not force_full_write:
            if len(previous) >= _FULL_WRITE_PREFER_EDIT_BYTES:
                note = (
                    " (tip: prefer file_edit for future small deltas on this file)"
                )
        return f"Wrote {len(new_body)} bytes to {path}{note}"

    async def file_edit(
        path: str = "",
        old_string: str = "",
        new_string: str = "",
        replace_all: bool = False,
        edits: Any = "",
    ) -> str:
        """Precise search/replace edit (prefer over rewriting whole files).

        Pass either old_string/new_string, or edits= as a JSON list of
        {old_string, new_string, replace_all?} for multi-hunk edits in one call.
        """
        import json as _json

        from remedy.core.approvals import APPROVALS
        from remedy.core.file_edit import apply_multi_hunk, apply_search_replace

        if not (path or "").strip():
            return format_tool_error(
                "path is required",
                code="MISSING_PATH",
                tool_name="file_edit",
                suggestion=(
                    'file_edit(path="src/foo.py", old_string="...", new_string="...") '
                    'or edits=\'[{"old_string":"a","new_string":"b"}]\''
                ),
            )
        bad = _reserved_guard(path)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="file_edit",
                suggestion="Skip reserved device paths.",
            )
        from remedy.core.turn_context import turn_session_id

        # Path jail before approval so denied scopes never create Ask tickets.
        try:
            target = runtime.resolve_tool_path(path, for_write=True)
        except Exception as e:
            return format_tool_error(
                f"path not allowed for edit: {path} ({e})",
                code="PATH_DENIED",
                tool_name="file_edit",
                suggestion=(
                    "Edits stay inside the project folder under project scope. "
                    "Raise access_scope to home/full only for intentional multi-tree edits."
                ),
            )
        sid = turn_session_id(runtime)
        ask_reason = APPROVALS.needs_ask(f"edit {path}", tool_name="file_edit")
        if ask_reason and not APPROVALS.is_approved(
            "file_edit", f"edit {path}", session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="file_edit",
                command=f"edit {path}",
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"path={path}\n"
                "Do not invent success. Tell the user this needs approval "
                f"(or /approve {item.id}), then retry file_edit."
            )
        _note_path(target)
        if not target.is_file():
            return format_tool_error(
                f"file not found: {path}",
                code="NOT_FOUND",
                tool_name="file_edit",
                suggestion="Use list_dir/repo_search to find the path, then file_read before edit.",
            )
        edits_raw = _normalize_edits_arg(edits).strip()
        has_single = bool((old_string or "").strip())
        if not edits_raw and not has_single:
            return format_tool_error(
                "provide old_string/new_string or edits= JSON array",
                code="MISSING_EDIT",
                tool_name="file_edit",
                suggestion=(
                    'file_edit(path=..., old_string="…", new_string="…") or '
                    'edits=\'[{"old_string":"…","new_string":"…"}]\''
                ),
            )
        # Soft read-before-edit guidance
        read_warn = ""
        with suppress(Exception):
            key = str(target.resolve()).lower()
            reads = getattr(runtime, "_files_read_this_turn", None) or set()
            if key not in reads:
                read_warn = (
                    "\nNote: this file was not file_read this turn — "
                    "prefer reading before large edits to avoid stale matches."
                )
        try:
            previous = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return format_tool_error(
                f"cannot read {path}: {e}",
                code="IO_ERROR",
                tool_name="file_edit",
            )

        if edits_raw:
            try:
                parsed = _json.loads(edits_raw)
            except _json.JSONDecodeError as e:
                return format_tool_error(
                    f"edits must be JSON array: {e}",
                    code="INVALID_EDITS",
                    tool_name="file_edit",
                    suggestion='edits=\'[{"old_string":"…","new_string":"…"}]\'',
                )
            if not isinstance(parsed, list):
                return format_tool_error(
                    "edits must be a JSON array of hunks",
                    code="INVALID_EDITS",
                    tool_name="file_edit",
                )
            result = apply_multi_hunk(previous, parsed)
        else:
            result = apply_search_replace(
                previous,
                old_string or "",
                new_string if new_string is not None else "",
                replace_all=bool(replace_all),
            )
        if not result.ok or result.new_content is None:
            return format_tool_error(
                result.message,
                code="EDIT_FAILED",
                tool_name="file_edit",
                suggestion=(
                    "file_read the file, copy the exact old_string (including whitespace), "
                    "or set replace_all=true if multiple matches are intentional."
                ),
            )
        try:
            target.write_text(result.new_content, encoding="utf-8")
        except OSError as e:
            return format_tool_error(
                f"cannot write {path}: {e}",
                code="IO_ERROR",
                tool_name="file_edit",
            )
        runtime._track_artifact(str(target))
        try:
            from remedy.core.time_travel import SessionUndoLog

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            SessionUndoLog(home).record_file_write(
                session_id=str(sid or getattr(runtime, "_session_id", "") or ""),
                path=target,
                previous_content=previous,
                existed=True,
                new_size=len(result.new_content or ""),
                message_id=getattr(runtime, "_active_message_id", None),
            )
        except Exception:
            pass
        _track_read(target)
        return f"{result.message} path={path}{read_warn}"

    async def file_edit_batch(edits: Any = "") -> str:
        """Apply search/replace hunks across one or more files (JSON array).

        Each item: {path, old_string, new_string, replace_all?}
        Files are processed in order; same path serialized by tool batch locks.
        Accepts *edits* as a JSON string **or** an already-parsed list (models
        sometimes pass arrays directly — previously crashed on ``.strip()``).
        """
        import json as _json

        from remedy.core.approvals import APPROVALS
        from remedy.core.file_edit import apply_search_replace

        # Accept list/dict directly (common LLM/tool-arg path) or JSON string.
        if isinstance(edits, list):
            items: Any = edits
        elif isinstance(edits, dict):
            # Single object → one-item batch
            items = [edits]
        else:
            raw = _normalize_edits_arg(edits).strip()
            if not raw:
                return format_tool_error(
                    "edits JSON array is required",
                    code="MISSING_EDITS",
                    tool_name="file_edit_batch",
                    suggestion=(
                        'file_edit_batch(edits=\'[{"path":"a.py","old_string":"x",'
                        '"new_string":"y"}]\')'
                    ),
                )
            try:
                items = _json.loads(raw)
            except _json.JSONDecodeError as e:
                return format_tool_error(
                    f"invalid JSON: {e}",
                    code="INVALID_EDITS",
                    tool_name="file_edit_batch",
                )
        if not isinstance(items, list) or not items:
            return format_tool_error(
                "edits must be a non-empty JSON array",
                code="INVALID_EDITS",
                tool_name="file_edit_batch",
            )
        from remedy.core.turn_context import turn_session_id

        reports: list[str] = []
        sid = turn_session_id(runtime)
        for i, edit_item in enumerate(items[:40]):
            if not isinstance(edit_item, dict):
                reports.append(f"[{i}] skip: not an object")
                continue
            p = str(edit_item.get("path") or "").strip()
            if not p:
                reports.append(f"[{i}] skip: missing path")
                continue
            bad = _reserved_guard(p)
            if bad:
                reports.append(f"[{i}] {p}: reserved name")
                continue
            junk = _junk_write_guard(p)
            if junk:
                reports.append(f"[{i}] {p}: junk path blocked")
                continue
            ask_reason = APPROVALS.needs_ask(f"edit {p}", tool_name="file_edit")
            if ask_reason and not APPROVALS.is_approved(
                "file_edit", f"edit {p}", session_id=sid
            ):
                approval = APPROVALS.create(
                    tool_name="file_edit",
                    command=f"edit {p}",
                    reason=ask_reason,
                    session_id=sid,
                )
                reports.append(
                    f"[{i}] {p}: APPROVAL_REQUIRED id={approval.id} reason={ask_reason}"
                )
                continue
            try:
                target = runtime.resolve_tool_path(p, for_write=True)
            except Exception as e:
                reports.append(
                    f"[{i}] {p}: PATH_DENIED (writes stay in project under project scope): {e}"
                )
                continue
            _note_path(target)
            if not target.is_file():
                reports.append(f"[{i}] {p}: not found")
                continue
            try:
                previous = target.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                reports.append(f"[{i}] {p}: read error {e}")
                continue
            r = apply_search_replace(
                previous,
                str(edit_item.get("old_string") or ""),
                str(
                    edit_item.get("new_string")
                    if "new_string" in edit_item
                    else ""
                ),
                replace_all=bool(edit_item.get("replace_all")),
            )
            if not r.ok or r.new_content is None:
                reports.append(f"[{i}] {p}: FAIL {r.message}")
                continue
            try:
                target.write_text(r.new_content, encoding="utf-8")
            except OSError as e:
                reports.append(f"[{i}] {p}: write error {e}")
                continue
            runtime._track_artifact(str(target))
            # Undo trail (parity with file_edit / file_write)
            try:
                from remedy.core.time_travel import SessionUndoLog

                home = getattr(getattr(runtime, "config", None), "home_dir", None)
                SessionUndoLog(home).record_file_write(
                    session_id=str(sid or getattr(runtime, "_session_id", "") or ""),
                    path=target,
                    previous_content=previous,
                    existed=True,
                    new_size=len(r.new_content or ""),
                    message_id=getattr(runtime, "_active_message_id", None),
                )
            except Exception:
                pass
            reports.append(f"[{i}] {p}: OK {r.message}")
        return "file_edit_batch:\n" + "\n".join(reports)

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

    async def bash_exec(
        command: str = "",
        timeout_seconds: float = 60.0,
        workdir: str = "",
        description: str = "",
    ) -> str:
        """Run a shell command through SubprocessSandbox (hidden console on Windows).

        *timeout_seconds* default 60, clamped to 5–600. *workdir* optional
        absolute or relative path (defaults to focus/default cwd). Local
        venv/node_modules/.bin and repo-root tools are prepended to PATH.
        *description* is accepted (and ignored) when models pass a human note.
        """
        _ = description
        from remedy.core.approvals import APPROVALS
        from remedy.core.project_fingerprint import path_env_with_local_bins
        from remedy.execution.process import win_shell_prefix
        from remedy.execution.sandbox import SubprocessSandbox

        if not command or not str(command).strip():
            return format_tool_error(
                "empty command",
                code="EMPTY_COMMAND",
                tool_name="bash_exec",
                suggestion="Pass a non-empty shell command string.",
            )
        danger = check_dangerous_command(["bash", "-c", command])
        if danger:
            suggestion = (
                "Use a safer equivalent (read files with file_read/list_dir; "
                "avoid destructive or network-restricted commands)."
            )
            low = danger.lower()
            if any(
                k in low
                for k in (
                    "app.exe",
                    "named app",
                    "tauri",
                    "remedy desktop",
                    "port 7400",
                    "remedy.exe",
                    "remedy serve",
                    "host agent",
                    "host api",
                )
            ):
                suggestion = (
                    "Self-preservation: do not kill bare app.exe / remedy / port 7400. "
                    "Restart a project UI only with a Path/CommandLine filter for that "
                    r'folder (e.g. Where-Object { $_.Path -match "SecretFolder" }).'
                )
            return format_tool_error(
                f"blocked by security policy: {danger}",
                code="SECURITY_BLOCK",
                tool_name="bash_exec",
                suggestion=suggestion,
            )
        # Partner trust: bash_exec always asks in ask-mode (high-impact tool)
        from remedy.core.turn_context import turn_session_id

        ask_reason = APPROVALS.needs_ask(command, tool_name="bash_exec")
        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved(
            "bash_exec", command, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="bash_exec",
                command=command,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"command={command[:400]}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry bash_exec with "
                "the same command."
            )
        root = runtime.effective_project_path()
        cwd = root
        wd_raw = (workdir or "").strip()
        if wd_raw:
            bad_wd = _reserved_guard(wd_raw)
            if bad_wd:
                return format_tool_error(
                    bad_wd,
                    code="RESERVED_NAME",
                    tool_name="bash_exec",
                    suggestion="Use a normal directory for workdir.",
                )
            try:
                # Shell cwd is a mutation surface — jail to write roots.
                cwd = runtime.resolve_tool_path(wd_raw, for_write=True)
                if cwd.is_file():
                    cwd = cwd.parent
            except Exception as e:
                return format_tool_error(
                    f"invalid workdir: {e}",
                    code="BAD_WORKDIR",
                    tool_name="bash_exec",
                    suggestion=(
                        "Pass a workdir under the project folder (project scope). "
                        "Raise access_scope to home/full for shell outside the project."
                    ),
                )
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 60.0)
        except (TypeError, ValueError):
            timeout = 60.0
        timeout = max(5.0, min(600.0, timeout))

        # Write roots only — never fall back to read roots (Desktop/Docs/Downloads).
        try:
            roots = list(runtime.write_roots() or [])
        except Exception as exc:
            return format_tool_error(
                f"cannot resolve write roots for shell jail: {exc}",
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Ensure a project folder is set. Shell refused fail-closed "
                    "(will not fall back to profile read roots)."
                ),
            )
        if not roots:
            roots = [root]

        # Project write jail for shell mutations (not just cwd). Fail closed.
        from remedy.core.shell_write_jail import check_shell_write_jail

        try:
            bound = not bool(runtime.project_path_is_unset())
        except Exception:
            bound = True  # fail closed: treat as bound if unknown
        try:
            scope = str(runtime.access_scope() or "project")
        except Exception:
            scope = "project"
        try:
            jail_hit = check_shell_write_jail(
                command,
                write_roots=list(roots),
                cwd=cwd,
                project_bound=bound,
                access_scope=scope,
            )
        except Exception as exc:
            return format_tool_error(
                f"shell write jail check failed (refused): {exc}",
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Stay inside the focus project with file_write/file_edit. "
                    "Jail check errors fail closed — shell not executed."
                ),
            )
        if jail_hit:
            return format_tool_error(
                jail_hit,
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Stay inside the focus project. Prefer file_write/file_edit. "
                    "Do not retarget sibling folders (SecretFolder vs SecretSticky). "
                    "To edit another tree, switch session project explicitly with the user."
                ),
            )

        argv = [*win_shell_prefix(), command]
        sandbox = SubprocessSandbox(allowed_paths=roots or [root, cwd])
        env = path_env_with_local_bins(cwd)
        result = await sandbox.execute(
            argv, workdir=cwd, timeout_seconds=timeout, env=env
        )
        parts = [f"exit_code={result.exit_code}", f"cwd={cwd}", f"timeout_s={timeout}"]
        # Full stdout/stderr — no quality truncation for the model.
        if result.stdout:
            out = result.stdout
            if len(out) > _HARD_SAFETY_CHARS:
                out = out[:_HARD_SAFETY_CHARS] + f"\n…[stdout safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(out)
        if result.stderr:
            err = result.stderr
            if len(err) > _HARD_SAFETY_CHARS:
                err = err[:_HARD_SAFETY_CHARS] + f"\n…[stderr safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(f"stderr:\n{err}")
        if result.exit_code != 0:
            parts.append(
                "Suggestion: Read stderr, fix flags/paths/cwd, raise timeout_seconds "
                "for long builds, or try a different command; use list_dir/file_read "
                "if you only need file contents."
            )
            # Best-effort path:line extraction for faster fix loops
            with suppress(Exception):
                import re as _re

                blob = (result.stderr or "") + "\n" + (result.stdout or "")
                locs: list[str] = []
                for m in _re.finditer(
                    r"([A-Za-z]:\\[^\s:\"']+\.\w{1,8}|[^\s:\"']+\.\w{1,8}):(\d+)",
                    blob,
                ):
                    loc = f"{m.group(1)}:{m.group(2)}"
                    if loc not in locs:
                        locs.append(loc)
                    if len(locs) >= 5:
                        break
                if locs:
                    parts.append(
                        "Likely locations (file_read these):\n"
                        + "\n".join(f"- {x}" for x in locs)
                    )
        return "\n".join(parts)

    async def help_list() -> str:
        """List F1 / owner's manual articles (always available)."""
        from remedy.core.help_docs import list_help_articles

        arts = list_help_articles()
        if not arts:
            return (
                "No help articles found on disk. Dev: set REMEDY_DEV_ROOT to the "
                "repo root so docs/manual is discoverable."
            )
        lines = [
            "F1 Help / owner's manual (read with help_read(id=…)):",
            "",
        ]
        for a in arts:
            lines.append(f"- **{a['id']}** — {a['title']}")
        lines.append("")
        lines.append(
            "These are the same chapters as in-app F1. Always readable; "
            "not limited by project access scope."
        )
        return "\n".join(lines)

    async def help_read(id: str = "", article_id: str = "") -> str:
        """Read one F1 help article by id (e.g. computer-use-soak, 19-metabolism)."""
        from remedy.core.help_docs import read_help_article

        aid = (id or article_id or "").strip()
        if not aid:
            return (
                "help_read requires id= (article slug). Call help_list first. "
                "Example: help_read(id=\"computer-use-soak\")"
            )
        result = read_help_article(aid)
        if not result.get("ok"):
            return str(result.get("error") or "help_read failed")
        title = result.get("title") or result.get("id")
        path = result.get("path") or ""
        body = result.get("content") or ""
        return f"# {title}\n\n_Source: {path}_\n\n{body}"

    runtime.tool_registry.register_builtin_handler(
        "help_list",
        "List F1 Help / owner's manual article ids (same as in-app F1). "
        "Always available — not limited by project access scope. "
        "Then help_read(id=…) for full text.",
        help_list,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "help_read",
        "Read one F1 Help / owner's manual article by id "
        "(e.g. computer-use-soak, 00-overview, 19-metabolism, 18-agency). "
        "Always available read-only — never claim help is outside access scope.",
        help_read,
        {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Article id/slug (from help_list), e.g. computer-use-soak",
                },
                "article_id": {
                    "type": "string",
                    "description": "Alias for id",
                },
            },
            "required": ["id"],
        },
    )

    runtime.tool_registry.register_builtin_handler(
        "file_read",
        "Read a text file under allowed roots (see access scope). "
        "Prefer paths relative to the project root. "
        "Optional offset/limit are 0-based line windows for large files. "
        "For F1 / owner's manual chapters prefer help_read(id=…) instead.",
        file_read,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the project"},
                "offset": {
                    "type": "integer",
                    "description": "0-based start line (optional)",
                    "default": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (optional; omit for full file subject to safety cap)",
                },
            },
            "required": ["path"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "file_write",
        "Create or overwrite a text file (UTF-8). Prefer file_edit for small "
        "changes to existing files. Existing large files with tiny deltas are "
        "refused unless force_full_write=true. Do NOT write _ref_*/_ex_* junk "
        "or bash Set-Content. Allowed: project, Desktop, Documents, Downloads "
        "(plus home when access scope is home/full).",
        file_write,
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or project-relative path",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents to write",
                },
                "force_full_write": {
                    "type": "boolean",
                    "description": (
                        "Set true only for intentional full rewrites of existing "
                        "large files. Default false prefers file_edit for tiny deltas."
                    ),
                    "default": False,
                },
            },
            "required": ["path", "content"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "file_edit",
        "Precise search/replace edit of an existing text file. Prefer this over "
        "file_write when changing part of a large file. old_string must match "
        "exactly once unless replace_all=true. For multiple changes in one file, "
        "pass edits= as a JSON array of {old_string,new_string} hunks.",
        file_edit,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {
                    "type": "string",
                    "description": "Exact text to find (include context if needed)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every match (default false = unique match required)",
                    "default": False,
                },
                "edits": {
                    "type": "string",
                    "description": (
                        "Optional JSON array of hunks "
                        '[{"old_string":"...","new_string":"...","replace_all":false}] '
                        "applied in order (multi-hunk edit)"
                    ),
                },
            },
            "required": ["path"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "file_edit_batch",
        "Apply multiple search/replace edits across one or more files in one call. "
        "edits= JSON array of {path, old_string, new_string, replace_all?}.",
        file_edit_batch,
        {
            "type": "object",
            "properties": {
                "edits": {
                    "type": "string",
                    "description": (
                        'JSON array e.g. [{"path":"a.py","old_string":"x","new_string":"y"}]'
                    ),
                },
            },
            "required": ["edits"],
        },
    )
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
    runtime.tool_registry.register_builtin_handler(
        "bash_exec",
        "Run a shell command. Default cwd = focus folder (or home). "
        "Optional workdir= absolute/relative path; timeout_seconds= 5–600 (default 60). "
        "Local .venv/node_modules/.bin and repo-root tools are on PATH. "
        "Do NOT use for simple text file create/edit — use file_write instead.",
        bash_exec,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout seconds (default 60, max 600)",
                    "default": 60,
                },
                "workdir": {
                    "type": "string",
                    "description": "Optional working directory (absolute or relative)",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Optional human note about the command (ignored by the runner; "
                        "accepted so models do not fail on extra kwargs)"
                    ),
                },
            },
            "required": ["command"],
        },
    )
    runtime._register_comfyui_tools()
    runtime._register_vision_tools()
    runtime._register_local_discover_tools()
    runtime._register_skill_tools()
    try:
        from remedy.core.agent_mission_tools import register_mission_tools

        register_mission_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_spread_tools import register_spread_tools

        register_spread_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_web_tools import register_web_tools

        register_web_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_computer_tools import register_computer_tools

        register_computer_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_assistant_tools import register_assistant_tools

        register_assistant_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_settings_tools import register_settings_tools

        register_settings_tools(runtime)
    except Exception:
        pass
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps = []
    runtime._learning_loop = None

