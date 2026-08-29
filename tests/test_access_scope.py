"""Access scope + multi-root path resolution (read vs write jail)."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.errors import SecurityError
from remedy.core.security import is_protected_secret_path, refuse_protected_secret_path
from remedy.core.workspace import (
    allowed_roots_for_scope,
    effective_access_scope,
    normalize_access_scope,
    path_in_roots,
    resolve_existing_path,
    resolve_under_roots,
    write_roots_for_scope,
)


def test_path_in_roots_empty_is_no_jail(tmp_path: Path):
    assert path_in_roots(tmp_path / "x", []) is True
    assert path_in_roots(tmp_path / "x", None) is True


def test_path_in_roots_blocks_escape(tmp_path: Path):
    inside = tmp_path / "proj" / "a.txt"
    inside.parent.mkdir()
    inside.write_text("x", encoding="utf-8")
    assert path_in_roots(inside, [tmp_path / "proj"]) is True
    assert path_in_roots(tmp_path / "proj", [tmp_path / "proj"]) is True
    assert path_in_roots(tmp_path / "other.txt", [tmp_path / "proj"]) is False
    escaped = resolve_existing_path(tmp_path / "proj" / ".." / "other.txt")
    assert path_in_roots(escaped, [tmp_path / "proj"]) is False


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


def test_write_roots_full_with_project_are_machine_wide(tmp_path: Path):
    """The owner's access_scope=full is authoritative for writes (not silently project)."""
    home = tmp_path / "homeuser"
    home.mkdir()
    desk = home / "Desktop"
    desk.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    wroots = write_roots_for_scope("full", proj, home=home)
    assert [r.resolve() for r in wroots] == [proj.resolve(), home.resolve()]
    out = resolve_under_roots(
        str(elsewhere / "y.txt"), wroots, access_scope="full", for_write=True
    )
    assert out == (elsewhere / "y.txt").resolve()
    out = resolve_under_roots(
        str(desk / "escape.txt"), wroots, access_scope="full", for_write=True
    )
    assert out == (desk / "escape.txt").resolve()


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


def test_full_scope_allows_relative_escape(tmp_path: Path):
    """Full (warn) must resolve ../sibling — Ask/Auto still jail that."""
    proj = tmp_path / "proj"
    sib = tmp_path / "sibling"
    proj.mkdir()
    sib.mkdir()
    target = sib / "out.txt"
    p = resolve_under_roots("../sibling/out.txt", [proj], access_scope="full")
    assert p == target.resolve()
    with pytest.raises(SecurityError):
        resolve_under_roots("../sibling/out.txt", [proj], access_scope="project")


def test_protected_secret_path_detects_auth_tree(tmp_path: Path, monkeypatch):
    """.../.remedy/auth and $REMEDY_HOME/auth are always protected."""
    from remedy.core.security import clear_protected_auth_roots_cache

    clear_protected_auth_roots_cache()
    # Path-part form (works even when Path.home is unrelated)
    remedy = tmp_path / "uhome" / ".remedy"
    auth = remedy / "auth"
    auth.mkdir(parents=True)
    secret = auth / "provider_keys.json"
    secret.write_text('{"x":1}', encoding="utf-8")

    assert is_protected_secret_path(secret) is True
    assert is_protected_secret_path(auth) is True
    assert is_protected_secret_path(remedy / "config.toml") is False
    with pytest.raises(SecurityError) as ei:
        refuse_protected_secret_path(secret)
    assert ei.value.details.get("rule") == "protected_secret_path"

    # REMEDY_HOME form (custom home name, no ".remedy" segment)
    custom = tmp_path / "custom_remedy_home"
    c_auth = custom / "auth"
    c_auth.mkdir(parents=True)
    c_secret = c_auth / "xai.json"
    c_secret.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("REMEDY_HOME", str(custom))
    clear_protected_auth_roots_cache()
    assert is_protected_secret_path(c_secret) is True
    # Nested under auth + case-insensitive segment still blocked
    nested = c_auth / "oauth" / "tokens.json"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}", encoding="utf-8")
    assert is_protected_secret_path(nested) is True
    # Non-auth sibling under REMEDY_HOME is allowed
    assert is_protected_secret_path(custom / "config.toml") is False
    # Auth roots cache is stable across repeated checks (same env)
    assert is_protected_secret_path(c_secret) is True


def test_resolve_under_roots_blocks_auth_even_on_full_scope(tmp_path: Path):
    """access_scope=full must still refuse reading/writing auth secrets."""
    auth = tmp_path / "uhome" / ".remedy" / "auth"
    auth.mkdir(parents=True)
    secret = auth / "local_api_token"
    secret.write_text("tokensecretvalue123456", encoding="utf-8")

    proj = tmp_path / "proj"
    proj.mkdir()
    with pytest.raises(SecurityError) as ei:
        resolve_under_roots(str(secret), [proj], access_scope="full")
    assert ei.value.details.get("rule") == "protected_secret_path"
    assert "protected" in str(ei.value).lower() or "auth" in str(ei.value).lower()


def test_resolve_under_roots_blocks_auth_junction(tmp_path: Path):
    """Symlink/junction into auth must not open secrets via project-relative path."""
    auth = tmp_path / "uhome" / ".remedy" / "auth"
    auth.mkdir(parents=True)
    secret = auth / "provider_keys.json"
    secret.write_text('{"api_key":"sk-leaked"}', encoding="utf-8")

    proj = tmp_path / "proj"
    proj.mkdir()
    link = proj / "leak"
    try:
        link.symlink_to(secret, target_is_directory=False)
    except OSError:
        pytest.skip("symlinks not available on this host")

    with pytest.raises(SecurityError) as ei:
        resolve_under_roots("leak", [proj], access_scope="project")
    rule = ei.value.details.get("rule")
    assert rule in ("protected_secret_path", "path_traversal", "path_chars")

    # Absolute path to the symlink (not the target) still resolves into auth.
    with pytest.raises(SecurityError) as ei2:
        resolve_under_roots(str(link), [proj], access_scope="full")
    assert ei2.value.details.get("rule") == "protected_secret_path"

    # is_protected_secret_path must detect the resolved symlink target itself.
    assert is_protected_secret_path(link) is True


def test_is_protected_secret_path_dir_symlink_into_auth(tmp_path: Path, monkeypatch):
    """Directory symlink/junction into $REMEDY_HOME/auth is always protected."""
    from remedy.core.security import clear_protected_auth_roots_cache

    custom = tmp_path / "custom_home"
    c_auth = custom / "auth"
    c_auth.mkdir(parents=True)
    secret = c_auth / "local_api_token"
    secret.write_text("tok-secret-value-xyz", encoding="utf-8")
    monkeypatch.setenv("REMEDY_HOME", str(custom))
    clear_protected_auth_roots_cache()

    proj = tmp_path / "proj"
    proj.mkdir()
    link_dir = proj / "auth_link"
    try:
        link_dir.symlink_to(c_auth, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks not available on this host")

    via_link = link_dir / "local_api_token"
    assert is_protected_secret_path(via_link) is True
    assert is_protected_secret_path(link_dir) is True
    with pytest.raises(SecurityError) as ei:
        resolve_under_roots(str(via_link), [proj], access_scope="full")
    assert ei.value.details.get("rule") == "protected_secret_path"
    # Relative path through the dir link
    with pytest.raises(SecurityError):
        resolve_under_roots("auth_link/local_api_token", [proj], access_scope="project")
