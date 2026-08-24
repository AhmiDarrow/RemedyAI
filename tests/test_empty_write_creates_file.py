"""A blank ``file_write`` that creates a file is not a wipe.

The empty-write guard stops a model blanking real source. It judged on path and
content alone, never checking whether anything was on disk — so *creating* an
empty file was refused too. That blocks an ordinary move: ``mypkg/__init__.py``
is supposed to be empty. Observed live as a local model getting
``EMPTY_SOURCE_WRITE`` on ``__init__.py`` and abandoning the package build.

These tests pin both sides: creating blank files is allowed, wiping real source
is still refused.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remedy.core.workspace_tools.guards import (
    empty_write_creates_nothing,
    looks_like_empty_source_write,
)


def _runtime(root: Path) -> SimpleNamespace:
    def resolve_tool_path(path: str, *, for_write: bool = False) -> Path:
        p = Path(path)
        return p if p.is_absolute() else (root / p)

    return SimpleNamespace(resolve_tool_path=resolve_tool_path)


def test_blank_write_to_missing_file_is_a_create(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    assert empty_write_creates_nothing(rt, str(tmp_path / "pkg" / "core.py"))


def test_init_py_is_always_allowed_blank(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    init = tmp_path / "__init__.py"
    init.write_text("# not actually empty\n", encoding="utf-8")
    # Canonically blank: rewriting it empty is normal, not destructive.
    assert empty_write_creates_nothing(rt, str(init))


def test_blank_write_over_an_empty_file_is_harmless(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    target = tmp_path / "empty.py"
    target.write_text("", encoding="utf-8")
    assert empty_write_creates_nothing(rt, str(target))


def test_blank_write_over_real_source_is_still_a_wipe(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("def main():\n    return 42\n", encoding="utf-8")
    assert not empty_write_creates_nothing(rt, str(target))


def test_underlying_detector_is_unchanged(tmp_path: Path) -> None:
    """The predicate itself still flags blank source writes."""
    assert looks_like_empty_source_write("app.py", "")
    assert looks_like_empty_source_write("app.py", "   \n ")
    assert not looks_like_empty_source_write("app.py", "print(1)")


def test_empty_path_is_not_waved_through(tmp_path: Path) -> None:
    assert not empty_write_creates_nothing(_runtime(tmp_path), "")
