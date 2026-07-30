"""Access scope + multi-root path resolution (read vs write jail)."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.errors import SecurityError
from remedy.core.workspace import (
    allowed_roots_for_scope,
    effective_access_scope,
    normalize_access_scope,
    resolve_under_roots,
    write_roots_for_scope,
)


def test_normalize_access_scope():
    assert normalize_access_scope("project") == "project"
    assert normalize_access_scope("home") == "home"
    assert normalize_access_scope("full") == "full"
    assert normalize_access_scope("PROJECT+HOME") == "home"
    assert normalize_access_scope(None) == "project"


def test_empty_project_forces_full_access():
    assert effective_access_scope("project", None) == "full"
    assert effective_access_scope("untrusted", "") == "full"
    assert effective_access_scope("project", ".") == "full"
    assert effective_access_scope("project", "C:/MyApp") == "project"


def test_allowed_roots_project_includes_project_and_profile_folders(tmp_path: Path):
    """project *read* roots include project + Desktop/Documents/Downloads when present."""
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


def test_write_roots_project_are_project_only(tmp_path: Path):
    """project *write* roots exclude Desktop/Documents/Downloads."""
    home = tmp_path / "homeuser"
    home.mkdir()
    desk = home / "Desktop"
    desk.mkdir()
    docs = home / "Documents"
    docs.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    wroots = write_roots_for_scope("project", proj, home=home)
    resolved = [r.resolve() for r in wroots]
    assert proj.resolve() in resolved
    assert desk.resolve() not in resolved
    assert docs.resolve() not in resolved
    assert home.resolve() not in resolved
    assert len(resolved) == 1


def test_write_roots_full_with_project_still_project_only(tmp_path: Path):
    """full scope must not open Desktop writes when a project is bound."""
    home = tmp_path / "homeuser"
    home.mkdir()
    desk = home / "Desktop"
    desk.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    wroots = write_roots_for_scope("full", proj, home=home)
    assert [r.resolve() for r in wroots] == [proj.resolve()]
    with pytest.raises(SecurityError):
        resolve_under_roots(
            str(desk / "escape.txt"), wroots, access_scope="project"
        )


def test_write_roots_untrusted_project_only(tmp_path: Path):
    home = tmp_path / "homeuser"
    home.mkdir()
    (home / "Desktop").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    wroots = write_roots_for_scope("untrusted", proj, home=home)
    assert [r.resolve() for r in wroots] == [proj.resolve()]


def test_write_roots_home_includes_home(tmp_path: Path):
    home = tmp_path / "homeuser"
    home.mkdir()
    (home / "Desktop").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    wroots = write_roots_for_scope("home", proj, home=home)
    resolved = [r.resolve() for r in wroots]
    assert proj.resolve() in resolved
    assert home.resolve() in resolved


def test_allowed_roots_home_includes_home(tmp_path: Path):
    home = tmp_path / "homeuser"
    home.mkdir()
    (home / "Desktop").mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    roots = allowed_roots_for_scope("home", proj, home=home)
    assert proj.resolve() in [r.resolve() for r in roots]
    assert home.resolve() in [r.resolve() for r in roots]


def test_resolve_desktop_path_under_project_scope_read_ok(tmp_path: Path):
    """Reading Desktop under project scope is allowed via read roots."""
    home = tmp_path / "homeuser"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    f = desk / "tool_test.txt"
    roots = allowed_roots_for_scope("project", proj, home=home)
    p = resolve_under_roots(str(f), roots, access_scope="project")
    assert p == f.resolve()


def test_resolve_desktop_blocked_on_write_roots(tmp_path: Path):
    """Writing Desktop under project scope is denied via write roots."""
    home = tmp_path / "homeuser"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    f = desk / "escape.txt"
    wroots = write_roots_for_scope("project", proj, home=home)
    with pytest.raises(SecurityError):
        resolve_under_roots(str(f), wroots, access_scope="project")


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
