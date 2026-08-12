"""Targeted file edit (search/replace) for agency parity with IDE-style agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EditResult:
    ok: bool
    message: str
    occurrences: int = 0
    previous: str | None = None
    new_content: str | None = None
    hunks_applied: int = 0


def _find_flex_span(content: str, old: str) -> tuple[int, int] | None:
    """Unique match of *old* in *content* ignoring CRLF and trailing spaces.

    Returns ``(start, end)`` into the original *content*, or None when the
    match is missing or not unique. Used so file_edit survives Windows
    line-ending / trailing-whitespace drift instead of failing closed.
    """
    if not old:
        return None
    old_lines = [ln.rstrip() for ln in old.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    # Drop a trailing empty line from old if the snippet ended with a newline
    if len(old_lines) > 1 and old_lines[-1] == "":
        old_lines = old_lines[:-1]
    if not old_lines:
        return None
    raw = content.splitlines(keepends=True)
    stripped = [ln.rstrip("\r\n").rstrip() for ln in raw]
    n = len(old_lines)
    if n > len(stripped):
        return None
    hits: list[int] = []
    for i in range(len(stripped) - n + 1):
        if stripped[i : i + n] == old_lines:
            hits.append(i)
            if len(hits) > 1:
                return None
    if len(hits) != 1:
        return None
    i = hits[0]
    start = sum(len(raw[j]) for j in range(i))
    end = start + sum(len(raw[j]) for j in range(i, i + n))
    # If the snippet did not include a trailing newline, do not consume the
    # last matched line's keepends — that glued the next source line on.
    if not old.endswith("\n") and not old.endswith("\r"):
        last = raw[i + n - 1]
        extra = len(last) - len(last.rstrip("\r\n"))
        end -= extra
    return start, end


def apply_search_replace(
    content: str,
    old_string: str,
    new_string: str,
    *,
    replace_all: bool = False,
) -> EditResult:
    """Apply a precise string replacement.

    Defaults to a single occurrence (must be unique) unless *replace_all*.
    """
    if old_string == "":
        return EditResult(ok=False, message="old_string must not be empty")
    if old_string == new_string:
        return EditResult(ok=False, message="old_string and new_string are identical")
    count = content.count(old_string)
    if count == 0:
        # CRLF / trailing-space mismatch is the usual Windows write failure.
        flex = _find_flex_span(content, old_string)
        if flex is not None:
            start, end = flex
            chunk = content[start:end]
            ns = new_string
            # Don't inject LF into a CRLF file (or the reverse).
            if "\r\n" in chunk and "\n" in ns and "\r\n" not in ns:
                ns = ns.replace("\n", "\r\n")
            elif "\r\n" not in chunk and "\n" in chunk and "\r\n" in ns:
                ns = ns.replace("\r\n", "\n")
            new_content = content[:start] + ns + content[end:]
            return EditResult(
                ok=True,
                message="Replaced 1 occurrence (whitespace-tolerant match).",
                occurrences=1,
                previous=content,
                new_content=new_content,
                hunks_applied=1,
            )
        # Helpful near-miss hint: first 80 chars of old_string
        hint = old_string[:80].replace("\n", "\\n")
        return EditResult(
            ok=False,
            message=(
                f"old_string not found in file (0 matches). "
                f"Re-read the file and copy the exact text. Snippet: {hint!r}"
            ),
            occurrences=0,
            previous=content,
        )
    if not replace_all and count > 1:
        return EditResult(
            ok=False,
            message=(
                f"old_string matched {count} times — pass replace_all=true to change all, "
                "or include more surrounding context so the match is unique."
            ),
            occurrences=count,
            previous=content,
        )
    if replace_all:
        new_content = content.replace(old_string, new_string)
        n = count
    else:
        new_content = content.replace(old_string, new_string, 1)
        n = 1
    return EditResult(
        ok=True,
        message=f"Replaced {n} occurrence(s).",
        occurrences=n,
        previous=content,
        new_content=new_content,
        hunks_applied=1,
    )


def apply_multi_hunk(
    content: str,
    hunks: list[dict[str, Any]] | list[tuple[str, str]],
) -> EditResult:
    """Apply multiple search/replace hunks sequentially on *content*.

    Each hunk is either:
      - dict with keys old_string/new_string (optional replace_all)
      - tuple (old_string, new_string)

    Stops on first failure and leaves content unchanged for that failure
    (returns previous successful partial only via message — actually we
    return failure without writing; caller should not write on !ok).
    On failure, *new_content* is None; *previous* is original content.
    """
    if not hunks:
        return EditResult(
            ok=False,
            message="edits list is empty",
            previous=content,
        )

    current = content
    total_occ = 0
    applied = 0
    for i, hunk in enumerate(hunks):
        if isinstance(hunk, (tuple, list)) and len(hunk) >= 2:
            old_s = str(hunk[0])
            new_s = str(hunk[1])
            rep_all = bool(hunk[2]) if len(hunk) > 2 else False
        elif isinstance(hunk, dict):
            old_s = str(hunk.get("old_string") or hunk.get("old") or "")
            new_s = str(hunk.get("new_string") if "new_string" in hunk else hunk.get("new") or "")
            rep_all = bool(hunk.get("replace_all") or False)
        else:
            return EditResult(
                ok=False,
                message=f"hunk {i}: invalid shape (need old_string/new_string)",
                previous=content,
                hunks_applied=applied,
            )
        r = apply_search_replace(current, old_s, new_s, replace_all=rep_all)
        if not r.ok or r.new_content is None:
            return EditResult(
                ok=False,
                message=f"hunk {i} failed: {r.message}",
                previous=content,
                occurrences=total_occ,
                hunks_applied=applied,
            )
        current = r.new_content
        total_occ += r.occurrences
        applied += 1

    return EditResult(
        ok=True,
        message=f"Applied {applied} hunk(s), {total_occ} total replacement(s).",
        occurrences=total_occ,
        previous=content,
        new_content=current,
        hunks_applied=applied,
    )
