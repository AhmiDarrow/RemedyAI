"""Path/junk guards for workspace tools."""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any

# Agent scaffold dumps that should never land in product trees.
JUNK_WRITE_NAME_RE = re.compile(
    r"(?i)"
    r"(?:^|[/\\])_ref_[^/\\]+$"
    r"|(?:^|[/\\])_ex_[a-z0-9]+(?:\.[^/\\]+)?$"
    r"|(?:^|[/\\])_write_[^/\\]+\.py$"
    r"|(?:^|[/\\])_patch_[^/\\]+\.py$"
    r"|(?:^|[/\\])_vault_tail\.txt$"
)

# Existing files this large should use file_edit unless force_full_write.
FULL_WRITE_PREFER_EDIT_BYTES = 4_000
# Tiny absolute/relative size change via full rewrite → refuse (use file_edit).
TINY_REWRITE_ABS = 120
TINY_REWRITE_RATIO = 0.02

# Provider-history stubs must never be written back to disk (agent echo bug).
HISTORY_STUB_MARKERS = (
    "[file_write content omitted",
    "omitted from provider history",
    "_history_summarized",
    "<<NOT_SOURCE_CODE",
    "DO_NOT_file_write_this_string",
    "history_stub kind=",
)


def normalize_edits_arg(edits: Any) -> str:
    """Accept JSON string or already-parsed list/dict for edits= parameters."""
    import json as _json

    if edits is None:
        return ""
    if isinstance(edits, (list, dict)):
        return _json.dumps(edits, ensure_ascii=False)
    return str(edits)


def parent_hint(path: str) -> str:
    p = (path or ".").strip() or "."
    if p in (".", "./", ""):
        return "."
    parent = Path(p).parent.as_posix()
    return parent if parent not in ("", ".") else "."


def reserved_guard(path: str) -> str | None:
    from remedy.core.win_paths import check_tool_path_safe

    return check_tool_path_safe(path)


def junk_write_guard(path: str) -> str | None:
    p = (path or "").strip().replace("\\", "/")
    if not p:
        return None
    if JUNK_WRITE_NAME_RE.search(p):
        return (
            f"refusing junk scaffold path {path!r}: do not write _ref_*, "
            "_ex_*, _write_*.py, or _patch_*.py into the project. "
            "Read reference sources from their real location; edit the "
            "real target with file_edit / file_write."
        )
    return None


def note_path(runtime: Any, target: Path) -> None:
    with suppress(Exception):
        from remedy.core.work_roots import note_work_path

        note_work_path(runtime, target)


def track_read(runtime: Any, target: Path) -> None:
    with suppress(Exception):
        key = str(target.resolve()).lower()
        reads = getattr(runtime, "_files_read_this_turn", None)
        if not isinstance(reads, set):
            reads = set()
            runtime._files_read_this_turn = reads
        reads.add(key)
