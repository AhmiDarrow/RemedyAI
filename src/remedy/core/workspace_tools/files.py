"""Workspace tools — files."""

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
from remedy.core.workspace_tools.guards import (
    FULL_WRITE_PREFER_EDIT_BYTES as _FULL_WRITE_PREFER_EDIT_BYTES,
)
from remedy.core.workspace_tools.guards import (
    junk_write_guard,
    note_path,
    parent_hint,
    reserved_guard,
    track_read,
)
from remedy.core.workspace_tools.guards import (
    normalize_edits_arg as _normalize_edits_arg,
)


def register_files_tools(runtime: Any) -> None:
    """Register files workspace tools."""
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

    def _claim_or_block(sid: str | None, target: Path, *, tool_name: str) -> str | None:
        """Body coordination: claim ``target`` for this session before writing.

        Returns a tool-error string when another *live* muscle (session) already
        holds the file — block-and-coordinate, never overwrite. Returns None on
        success (the claim is recorded/refreshed). Best-effort; never raises.
        """
        if not sid:
            return None
        with suppress(Exception):
            from remedy.core import coordination as _coord
            from remedy.core.llm_binding import get_llm_binding

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            proj = ""
            with suppress(Exception):
                proj = str(runtime.effective_project_path() or "")
            muscle = ""
            with suppress(Exception):
                _b = get_llm_binding(runtime)
                muscle = "/".join(
                    x
                    for x in ((_b.provider or "").strip(), (_b.model or "").strip())
                    if x
                )
            conflict = _coord.claim_path(
                sid, target, muscle=muscle, project_path=proj, home=home
            )
            if conflict is not None:
                who = conflict.muscle or "another session"
                return format_tool_error(
                    f"'{target.name}' is being worked on right now by {who} "
                    f"(session {conflict.session_id[:8]}) — not overwriting it.",
                    code="PATH_HELD_BY_OTHER_SESSION",
                    tool_name=tool_name,
                    suggestion=(
                        "Another of Remedy's muscles holds this file. Pick a "
                        "different file, or coordinate before editing — never "
                        "overwrite a sibling's work. The hold frees automatically "
                        "when that session moves on."
                    ),
                )
        return None

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
        # Ignore tiny accidental limits (models often pass limit=50/200 and then
        # claim the file is "capped at 2000 chars" — partner must see full source).
        try:
            off = max(0, int(offset or 0))
        except (TypeError, ValueError):
            off = 0
        lim: int | None
        try:
            lim = None if limit is None else max(1, int(limit))
        except (TypeError, ValueError):
            lim = None
        if lim is not None and lim < 500 and off == 0:
            # Treat as "full file" unless the file is enormous (then use a floor)
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
        path: str = "",
        content: str = "",
        force_full_write: bool = False,
        **kwargs: Any,
    ) -> str:
        from remedy.core.approvals import APPROVALS
        from remedy.core.turn_context import turn_session_id

        # Models often send file/filepath instead of path — never TypeError.
        if not (path or "").strip():
            path = str(
                kwargs.get("file")
                or kwargs.get("filepath")
                or kwargs.get("filename")
                or kwargs.get("target")
                or kwargs.get("path")
                or ""
            ).strip()
        if content == "" and kwargs.get("content") is not None:
            content = kwargs.get("content")  # type: ignore[assignment]
        if isinstance(content, (dict, list)):
            import json as _json

            content = _json.dumps(content, ensure_ascii=False)
        if not (path or "").strip():
            return format_tool_error(
                "file_write requires path= (or file=/filepath=)",
                code="MISSING_PATH",
                tool_name="file_write",
                suggestion=(
                    'file_write(path="src/core/app.py", content="...") with a path '
                    "under the project root."
                ),
            )

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
        # Soft-skip when the real file is already on disk (breaks HISTORY_STUB loops).
        from remedy.core.workspace_tools.guards import (
            looks_like_history_stub_text,
            resolve_stub_write_skip,
        )

        body_s = new_body if isinstance(new_body, str) else str(new_body)
        # Partner: never wipe source with empty body or looped-import spam.
        from remedy.core.workspace_tools.guards import (
            refuse_bad_file_write,
            resolve_empty_write_skip,
        )

        bad_body = refuse_bad_file_write(
            path, body_s, force_full_write=bool(force_full_write)
        )
        if bad_body:
            # Soft-skip empty wipe when real file already exists (screenshot 2026-08-08)
            if "empty file_write" in bad_body:
                skip_empty = resolve_empty_write_skip(
                    runtime, path, tool_name="file_write"
                )
                if skip_empty:
                    return skip_empty
            code = (
                "EMPTY_SOURCE_WRITE"
                if "empty file_write" in bad_body
                else "SPAM_SOURCE_WRITE"
            )
            return format_tool_error(
                bad_body,
                code=code,
                tool_name="file_write",
                suggestion=(
                    "file_read the path first, then file_edit a small hunk — "
                    "or file_write once with complete real source (not blank, "
                    "not a repeated import list). Never send content=\"\"."
                ),
            )
        is_stub_body = looks_like_history_stub_text(body_s) or (
            isinstance(content, dict)
            and (
                content.get("_invalid_json")
                or content.get("_truncated")
                or content.get("_history_summarized")
                or content.get("_body_omitted")
            )
        )
        if is_stub_body:
            skip = resolve_stub_write_skip(runtime, path, tool_name="file_write")
            if skip:
                return skip
            return format_tool_error(
                "refusing to write provider-history summary stub as file content "
                f"({path}). That text is not source code — it was redacted for the LLM.",
                code="HISTORY_STUB",
                tool_name="file_write",
                suggestion=(
                    "Write **new real source** (not history_stub text). "
                    "file_read the path if it exists, then file_edit; or file_write "
                    "a short skeleton and grow it with file_edit."
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
                    "Writes stay inside the project folder under Ask/Auto. "
                    "Use a path under the focus folder, or set Approvals → Full "
                    "(warn) if you granted machine-wide control."
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
        # Partner rule (2026-08-08): NEVER refuse a real rewrite with PREFER_FILE_EDIT.
        # That guard blocked 31k app.py rewrites and taught the agent to monologue.
        # Only no-op when content is byte-identical. Prefer file_edit is a tip, not a wall.
        if existed and previous is not None and new_body == previous:
            return (
                f"No change: {path} already has identical content "
                f"({len(previous)} chars)."
            )

        blocked = _claim_or_block(sid, target, tool_name="file_write")
        if blocked:
            return blocked
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
                    "Edits stay inside the project folder under Ask/Auto. "
                    "Use a path under the focus folder, or set Approvals → Full "
                    "(warn) if you granted machine-wide control."
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
            extra = ""
            with suppress(Exception):
                from remedy.core.file_edit import note_failed_edit

                extra = note_failed_edit(runtime, path, old_string or "")
            return format_tool_error(
                result.message + extra,
                code="EDIT_FAILED",
                tool_name="file_edit",
                suggestion=(
                    "file_read the file, copy the exact old_string (including whitespace), "
                    "or set replace_all=true if multiple matches are intentional."
                    " If this hunk already failed, do not resend it."
                ),
            )
        blocked = _claim_or_block(sid, target, tool_name="file_edit")
        if blocked:
            return blocked
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
            blocked = _claim_or_block(sid, target, tool_name="file_edit_batch")
            if blocked:
                reports.append(f"[{i}] {p}: held by another session — skipped")
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

