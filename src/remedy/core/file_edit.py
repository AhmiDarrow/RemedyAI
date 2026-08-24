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


def _leading_ws(line: str) -> str:
    i = 0
    while i < len(line) and line[i] in " \t":
        i += 1
    return line[:i]


def _ws_width(ws: str) -> int:
    return sum(4 if ch == "\t" else 1 for ch in ws)


def _find_indent_flex_span(content: str, old: str) -> tuple[int, int] | None:
    """Unique match ignoring leading *and* trailing whitespace per line.

    Claude-class agents often copy a snippet with the wrong indent (4 vs 8).
    Require a unique sequence of stripped lines so ``return x`` alone cannot
    smash every return in the file.
    """
    if not old or "\n" not in old:
        # Single-line indent drift is too ambiguous (many `return` / `pass`).
        return None
    old_lines = list(old.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    if len(old_lines) > 1 and old_lines[-1] == "":
        old_lines = old_lines[:-1]
    if len(old_lines) < 2:
        return None
    old_stripped = [ln.strip() for ln in old_lines]
    if not any(old_stripped):
        return None
    raw = content.splitlines(keepends=True)
    stripped = [ln.rstrip("\r\n").strip() for ln in raw]
    n = len(old_stripped)
    if n > len(stripped):
        return None
    hits: list[int] = []
    for i in range(len(stripped) - n + 1):
        if stripped[i : i + n] == old_stripped:
            hits.append(i)
            if len(hits) > 1:
                return None
    if len(hits) != 1:
        return None
    i = hits[0]
    start = sum(len(raw[j]) for j in range(i))
    end = start + sum(len(raw[j]) for j in range(i, i + n))
    if not old.endswith("\n") and not old.endswith("\r"):
        last = raw[i + n - 1]
        extra = len(last) - len(last.rstrip("\r\n"))
        end -= extra
    return start, end


def _reindent_new(new: str, old: str, file_span: str) -> str:
    """Shift *new* so its first-line indent matches the file span, not *old*."""
    file_first = file_span.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
    old_first = old.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0]
    delta = _ws_width(_leading_ws(file_first)) - _ws_width(_leading_ws(old_first))
    if delta == 0:
        return new
    crlf = "\r\n" in file_span
    nl = "\r\n" if crlf else "\n"
    ends = new.endswith("\n") or new.endswith("\r")
    lines = new.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    keep_end = bool(lines and lines[-1] == "" and ends)
    if keep_end:
        lines = lines[:-1]
    out: list[str] = []
    for ln in lines:
        if not ln.strip():
            out.append(ln)
            continue
        if delta > 0:
            out.append((" " * delta) + ln)
            continue
        cut = -delta
        i = 0
        while cut > 0 and i < len(ln) and ln[i] == " ":
            i += 1
            cut -= 1
        out.append(ln[i:])
    body = nl.join(out)
    if keep_end:
        body += nl
    return body


def nearby_lines_hint(content: str, old: str, *, radius: int = 2) -> str:
    """Show a small window around the first needle from *old* (repeat-fail help)."""
    needle = ""
    for ln in (old or "").splitlines():
        if ln.strip():
            needle = ln.strip()
            break
    if not needle or not content:
        return ""
    lines = content.splitlines()
    for i, ln in enumerate(lines):
        if needle in ln or ln.strip() == needle:
            lo = max(0, i - radius)
            hi = min(len(lines), i + radius + 1)
            window = "\n".join(f"{j + 1}: {lines[j]}" for j in range(lo, hi))
            return f" Nearby lines:\n{window}"
    return ""


def note_failed_edit(runtime: Any, path: str, old: str) -> str:
    """Remember a failed hunk this turn. Repeat → do-not-resend warning."""
    if runtime is None:
        return ""
    key = (str(path or "").replace("\\", "/").lower(), (old or "")[:160])
    bag = getattr(runtime, "_failed_edit_keys", None)
    if not isinstance(bag, set):
        bag = set()
        runtime._failed_edit_keys = bag
    if key in bag:
        return (
            " This exact hunk already failed this turn — do NOT resend it. "
            "file_read the current file and copy a unique contiguous snippet."
        )
    bag.add(key)
    return ""


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
        # Identical strings are a *no-op*, not a failure — and when the text is
        # already in the file the model's desired end state is already true.
        # Returning an error invited a retry of the same hunk: a local model
        # sent this 44 times in one turn, burning the whole step budget while
        # nothing on disk needed to change. Only report failure when the target
        # is genuinely absent, which is a real miss the model must correct.
        if content.count(old_string) > 0:
            return EditResult(
                ok=True,
                message=(
                    "No change needed: the file already contains exactly that "
                    "text (old_string and new_string are identical). Move on to "
                    "the next step — do not resend this hunk."
                ),
                occurrences=content.count(old_string),
                previous=content,
                new_content=content,
                hunks_applied=0,
            )
        return EditResult(
            ok=False,
            message=(
                "old_string and new_string are identical, and that text is not "
                "in the file. Read the file and copy an exact snippet to change."
            ),
        )
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
        indent = _find_indent_flex_span(content, old_string)
        if indent is not None:
            start, end = indent
            chunk = content[start:end]
            ns = _reindent_new(new_string, old_string, chunk)
            if "\r\n" in chunk and "\n" in ns and "\r\n" not in ns:
                ns = ns.replace("\n", "\r\n")
            elif "\r\n" not in chunk and "\n" in chunk and "\r\n" in ns:
                ns = ns.replace("\r\n", "\n")
            new_content = content[:start] + ns + content[end:]
            return EditResult(
                ok=True,
                message="Replaced 1 occurrence (indent-tolerant match).",
                occurrences=1,
                previous=content,
                new_content=new_content,
                hunks_applied=1,
            )
        # Helpful near-miss hint: first 80 chars of old_string
        hint = old_string[:80].replace("\n", "\\n")
        near = nearby_lines_hint(content, old_string)
        return EditResult(
            ok=False,
            message=(
                f"old_string not found in file (0 matches). "
                f"Re-read the file and copy the exact text. Snippet: {hint!r}"
                f"{near}"
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
