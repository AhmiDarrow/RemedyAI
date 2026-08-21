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
    "history_stub_only",
    "body omitted from chat history",
    "Do NOT re-file_write a history stub",
    "file body omitted from chat history",
)


def looks_like_history_stub_text(text: str | None) -> bool:
    """True when *text* is a provider-history stub, not real source."""
    if text is None:
        return False
    if isinstance(text, dict):
        return bool(
            text.get("_invalid_json")
            or text.get("_truncated")
            or text.get("_history_summarized")
            or text.get("_body_omitted")
        )
    s = text if isinstance(text, str) else str(text)
    if not s.strip():
        return False
    head = s[:400]
    if any(m in head for m in HISTORY_STUB_MARKERS):
        return True
    if any(m in s for m in HISTORY_STUB_MARKERS):
        return True
    return bool(s.strip().startswith("[") and "omitted from provider history" in s)


# Source-like extensions where empty / spam rewrites destroy the tree.
_SOURCE_WRITE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".rs",
        ".go",
        ".java",
        ".kt",
        ".cs",
        ".cpp",
        ".cc",
        ".c",
        ".h",
        ".hpp",
        ".rb",
        ".php",
        ".swift",
        ".vue",
        ".svelte",
        ".css",
        ".scss",
        ".html",
        ".htm",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".md",
        ".txt",
        ".sh",
        ".ps1",
        ".bat",
        ".cmd",
    }
)


def is_source_like_path(path: str | Path | None) -> bool:
    """True when path looks like product source (not a deliberate empty marker)."""
    if path is None:
        return False
    p = Path(str(path))
    suf = p.suffix.lower()
    if suf in _SOURCE_WRITE_SUFFIXES:
        return True
    name = p.name.lower()
    return name in ("makefile", "dockerfile", "gemfile", "procfile")


def looks_like_empty_source_write(
    path: str | Path | None,
    content: str | None,
    *,
    min_chars: int = 1,
) -> bool:
    """True when writing empty/whitespace would wipe a source file."""
    if not is_source_like_path(path):
        return False
    body = "" if content is None else (content if isinstance(content, str) else str(content))
    return len(body.strip()) < max(0, int(min_chars))


def looks_like_repetitive_spam_text(text: str | None, *, min_lines: int = 40) -> bool:
    """True when body is pathological repetition (import-spam / looped tokens).

    Partner failure 2026-08-09: file_write of main.py with the same
    ``QSplitter, QToolBar, …`` import line hundreds of times → tree poison.
    """
    if not text or not isinstance(text, str):
        return False
    s = text
    if len(s) < 800:
        return False
    lines = s.splitlines()
    if len(lines) < min_lines:
        # Also catch single-line token spam
        if len(s) >= 4000:
            # high char repetition via chunks
            chunk = s[:80]
            if chunk and s.count(chunk) >= 25:
                return True
        return False
    uniq = len(set(lines))
    ratio = uniq / max(1, len(lines))
    if ratio <= 0.12 and len(lines) >= min_lines:
        return True
    if ratio <= 0.22 and len(lines) >= 80:
        return True
    # Same non-trivial line appears too often
    from collections import Counter

    counts = Counter(L.strip() for L in lines if len(L.strip()) >= 24)
    if counts:
        top_n = counts.most_common(1)[0][1]
        if top_n >= 25 and top_n / max(1, len(lines)) >= 0.35:
            return True
    return False


def resolve_empty_write_skip(
    runtime: Any,
    path: str,
    *,
    tool_name: str = "file_write",
) -> str | None:
    """If *path* already has real source, soft-skip blank file_write (partner recovery).

    UI 2026-08-08: EMPTY_SOURCE_WRITE on app.py after a good file existed —
    model sent blank content. Soft-skip keeps the tree and tells the model to
    file_edit or re-send full source (same pattern as HISTORY_STUB skip).
    """
    path = (path or "").strip()
    if not path:
        return None
    try:
        target = runtime.resolve_tool_path(path, for_write=True)
    except Exception:
        try:
            target = runtime.resolve_tool_path(path, for_write=False)
        except Exception:
            return None
    try:
        if not target.is_file():
            return None
        existing = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(existing.strip()) < 8:
        return None
    if looks_like_history_stub_text(existing) or looks_like_repetitive_spam_text(
        existing
    ):
        return None
    n = len(existing)
    return (
        f"OK: skipped empty file_write for {path} — real file already on disk "
        f"({n} chars). Do not wipe it. Next: file_edit a small hunk, or "
        f"file_write once with the **complete** new source in content= "
        f"(never blank). tool={tool_name}"
    )


def refuse_bad_file_write(
    path: str | Path | None,
    content: str | None,
    *,
    force_full_write: bool = False,
) -> str | None:
    """Return an error code message if *content* must not land on disk, else None."""
    body = "" if content is None else (content if isinstance(content, str) else str(content))
    if looks_like_empty_source_write(path, body):
        return (
            f"refusing empty file_write to source path {path!s}. "
            "Content was blank — that would wipe the file. "
            "Pass the full source in content=, or file_edit a real hunk."
        )
    if looks_like_repetitive_spam_text(body):
        return (
            f"refusing repetitive spam file_write to {path!s} "
            f"({len(body)} chars, low line diversity). "
            "That pattern corrupts the tree (looped imports). "
            "file_read the real file, then file_edit a small hunk, or write "
            "compact real source once."
        )
    # force_full_write does not override empty/spam — those are never intentional product
    _ = force_full_write
    return None


def resolve_stub_write_skip(
    runtime: Any,
    path: str,
    *,
    tool_name: str = "file_write",
    min_chars: int = 0,
) -> str | None:
    """If *path* already has real source on disk, soft-skip a stub re-write.

    Returns a success message (not an error) so the ReAct loop does not spin
    HISTORY_STUB failures. Returns None when the caller should still refuse.

    *min_chars*: size the history note claimed is on disk. A shorter real file
    still skips (never overwrite real source with an empty body) but the
    message says so, so the model can file_read and decide.
    """
    path = (path or "").strip()
    if not path:
        return None
    try:
        target = runtime.resolve_tool_path(path, for_write=True)
    except Exception:
        try:
            target = runtime.resolve_tool_path(path, for_write=False)
        except Exception:
            return None
    try:
        if not target.is_file():
            return None
        existing = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not existing.strip():
        return None
    if looks_like_history_stub_text(existing):
        return None
    n = len(existing)
    size_note = ""
    if min_chars and n < int(min_chars):
        size_note = (
            f" Note: on-disk file is {n} chars, history said {int(min_chars)} — "
            "file_read it if you need to verify."
        )
    return (
        f"OK: skipped re-write of provider-history stub for {path} — "
        f"real file already on disk ({n} chars). "
        f"Continue with other files or file_edit; do not re-send history stubs."
        f"{size_note}"
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
    # Brace-expansion / glob mistakes (e.g. "{src,resources,docs,tests}")
    base = Path(p).name
    if "{" in p or "}" in p or ("," in base and not base.endswith((".csv", ".json"))):
        if "{" in p or "}" in p:
            return (
                f"refusing brace/glob path {path!r}: write one concrete path "
                "(e.g. src/core/app.py), not shell brace expansion."
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
