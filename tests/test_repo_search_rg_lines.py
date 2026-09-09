"""rg output parsing: matches, context lines, and paths that look like both.

A context line is ``path-N-text`` while a match is ``path:N:text``. Anchoring
only on ``:`` silently discarded every context line, so the ``context`` option
on search returned nothing extra and the model lost the lines around a hit.
"""

from __future__ import annotations

import pytest

from remedy.core.repo_search import _split_rg_line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("a.py:2:hit", ("a.py", 2, True, "hit")),
        ("a.py-1-before", ("a.py", 1, False, "before")),
        (r"C:\dir\a.py:2:hit", (r"C:\dir\a.py", 2, True, "hit")),
        (r"C:\dir\a.py-3-ctx", (r"C:\dir\a.py", 3, False, "ctx")),
        # A filename that itself looks like a context separator must not be
        # split at its own hyphen-digit-hyphen run.
        ("my-2-file.py:5:hit", ("my-2-file.py", 5, True, "hit")),
        # Text may contain colons.
        ("a.py:7:time is 12:30:00", ("a.py", 7, True, "time is 12:30:00")),
        ("nonsense", None),
        ("", None),
        (":5:no path", None),
    ],
)
def test_split_rg_line(line: str, expected: tuple[str, int, bool, str] | None) -> None:
    assert _split_rg_line(line) == expected
