"""Targeted file edit (search/replace) for agency parity with IDE-style agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EditResult:
    ok: bool
    message: str
    occurrences: int = 0
    previous: str | None = None
    new_content: str | None = None


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
    )
