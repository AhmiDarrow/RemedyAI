"""Access scope + multi-root path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.errors import SecurityError
from remedy.core.workspace import (
    allowed_roots_for_scope,
    normalize_access_scope,
    resolve_under_roots,
)


def test_normalize_access_scope():
    assert normalize_access_scope("project") == "project"
    assert normalize_access_scope("home") == "home"
    assert normalize_access_scope("full") == "full"
    assert normalize_access_scope("PROJECT+HOME") == "home"
    assert normalize_access_scope(None) == "project"


def test_allowed_roots_project_includes_project_and_profile_folders(tmp_path: Path):
    """project scope always includes project + Desktop/Documents/Downloads when present."""
    home = tmp_path / "homeuser"
    home.mkdir()
    desk = home / "Desktop"
    desk.mkdir()
    roots = allowed_roots_for_scope("project", tmp_path, home=home)
    resolved = [r.resolve() for r in roots]
    assert tmp_path.resolve() in resolved
    assert desk.resolve() in resolved
    # Full home is NOT required on project scope
    assert home.resolve() not in resolved


def test_allowed_roots_home_includes_home(tmp_path: Path):
    home = tmp_path / "homeuser"
    home.mkdir()
    (home / "Desktop").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    roots = allowed_roots_for_scope("home", proj, home=home)
    assert proj.resolve() in [r.resolve() for r in roots]
    assert home.resolve() in [r.resolve() for r in roots]


def test_resolve_desktop_path_under_project_scope(tmp_path: Path):
    home = tmp_path / "homeuser"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    f = desk / "tool_test.txt"
    roots = allowed_roots_for_scope("project", proj, home=home)
    p = resolve_under_roots(str(f), roots, access_scope="project")
    assert p == f.resolve()


def test_resolve_relative_under_project(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hi", encoding="utf-8")
    p = resolve_under_roots("a.txt", [tmp_path], access_scope="project")
    assert p == f.resolve()


def test_resolve_blocks_outside_project(tmp_path: Path):
    outside = tmp_path.parent / "nope.txt"
    with pytest.raises(SecurityError):
        resolve_under_roots(str(outside), [tmp_path], access_scope="project")


def test_full_scope_allows_absolute_under_user(tmp_path: Path):
    f = tmp_path / "x.txt"
    f.write_text("ok", encoding="utf-8")
    p = resolve_under_roots(str(f), [tmp_path], access_scope="full")
    assert p == f.resolve()
