"""Workspace file/shell tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

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

    async def file_write(path: str, content: str = "") -> str:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id

        ask_reason = APPROVALS.needs_ask(f"write {path}", tool_name="file_write")
        sid = turn_session_id(runtime)
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
        bad = _reserved_guard(path)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="file_write",
                suggestion="Choose a normal filename; never write Windows device names (nul, con, …).",
            )
        target = runtime.resolve_tool_path(path)
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
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
                new_size=len(content or ""),
                message_id=getattr(runtime, "_active_message_id", None),
            )
        except Exception:
            pass
        return f"Wrote {len(content)} bytes to {path}"

    async def file_edit(
        path: str = "",
        old_string: str = "",
        new_string: str = "",
        replace_all: bool = False,
        edits: str = "",
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

        ask_reason = APPROVALS.needs_ask(f"edit {path}", tool_name="file_edit")
        sid = turn_session_id(runtime)
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
        target = runtime.resolve_tool_path(path)
        _note_path(target)
        if not target.is_file():
            return format_tool_error(
                f"file not found: {path}",
                code="NOT_FOUND",
                tool_name="file_edit",
                suggestion="Use list_dir/repo_search to find the path, then file_read before edit.",
            )
        edits_raw = (edits or "").strip()
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

    async def file_edit_batch(edits: str = "") -> str:
        """Apply search/replace hunks across one or more files (JSON array).

        Each item: {path, old_string, new_string, replace_all?}
        Files are processed in order; same path serialized by tool batch locks.
        """
        import json as _json

        from remedy.core.approvals import APPROVALS
        from remedy.core.file_edit import apply_search_replace

        raw = (edits or "").strip()
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
        for i, item in enumerate(items[:40]):
            if not isinstance(item, dict):
                reports.append(f"[{i}] skip: not an object")
                continue
            p = str(item.get("path") or "").strip()
            if not p:
                reports.append(f"[{i}] skip: missing path")
                continue
            bad = _reserved_guard(p)
            if bad:
                reports.append(f"[{i}] {p}: reserved name")
                continue
            ask_reason = APPROVALS.needs_ask(f"edit {p}", tool_name="file_edit")
            if ask_reason and not APPROVALS.is_approved(
                "file_edit", f"edit {p}", session_id=sid
            ):
                item = APPROVALS.create(
                    tool_name="file_edit",
                    command=f"edit {p}",
                    reason=ask_reason,
                    session_id=sid,
                )
                reports.append(
                    f"[{i}] {p}: APPROVAL_REQUIRED id={item.id} reason={ask_reason}"
                )
                continue
            try:
                target = runtime.resolve_tool_path(p)
            except Exception as e:
                reports.append(f"[{i}] {p}: resolve error {e}")
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
                str(item.get("old_string") or ""),
                str(item.get("new_string") if "new_string" in item else ""),
                replace_all=bool(item.get("replace_all")),
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
    ) -> str:
        """Run a shell command through SubprocessSandbox (hidden console on Windows).

        *timeout_seconds* default 60, clamped to 5–600. *workdir* optional
        absolute or relative path (defaults to focus/default cwd). Local
        venv/node_modules/.bin and repo-root tools are prepended to PATH.
        """
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
            return format_tool_error(
                f"blocked by security policy: {danger}",
                code="SECURITY_BLOCK",
                tool_name="bash_exec",
                suggestion=(
                    "Use a safer equivalent (read files with file_read/list_dir; "
                    "avoid destructive or network-restricted commands)."
                ),
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
                cwd = runtime.resolve_tool_path(wd_raw)
                if cwd.is_file():
                    cwd = cwd.parent
            except Exception as e:
                return format_tool_error(
                    f"invalid workdir: {e}",
                    code="BAD_WORKDIR",
                    tool_name="bash_exec",
                    suggestion="Pass an absolute path or a path under allowed roots.",
                )
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 60.0)
        except (TypeError, ValueError):
            timeout = 60.0
        timeout = max(5.0, min(600.0, timeout))

        roots = runtime.allowed_roots()
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

    runtime.tool_registry.register_builtin_handler(
        "file_read",
        "Read a text file under allowed roots (see access scope). "
        "Prefer paths relative to the project root. "
        "Optional offset/limit are 0-based line windows for large files.",
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
        "changes to existing files. Do NOT use bash/powershell Set-Content. "
        "Allowed: project, Desktop, Documents, Downloads (plus home when access "
        "scope is home/full). Absolute Desktop paths are fine.",
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
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps = []
    runtime._learning_loop = None

