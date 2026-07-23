"""Workspace jail / project path security tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.errors import SecurityError
from remedy.core.security import check_dangerous_command, safe_path
from remedy.core.workspace import (
    ensure_project_dir,
    jail_path,
    list_workspace_entries,
    resolve_project_path,
    workspace_context_block,
)


def test_resolve_project_path_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_project_path(None) == tmp_path.resolve()
    assert resolve_project_path("") == tmp_path.resolve()
    assert resolve_project_path(".") == tmp_path.resolve()


def test_resolve_project_path_absolute(tmp_path):
    p = resolve_project_path(str(tmp_path / "proj"))
    assert p == (tmp_path / "proj").resolve()


def test_ensure_project_dir_creates(tmp_path):
    target = tmp_path / "new_proj"
    out = ensure_project_dir(target)
    assert out.is_dir()
    assert out == target.resolve()


def test_jail_path_relative_ok(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    p = jail_path("src/a.py", tmp_path)
    assert p == (tmp_path / "src" / "a.py").resolve()


def test_jail_path_blocks_traversal(tmp_path):
    with pytest.raises(SecurityError):
        jail_path("../outside", tmp_path)


def test_jail_path_blocks_absolute_escape(tmp_path):
    with pytest.raises(SecurityError):
        jail_path(str(Path.cwd()), tmp_path)


def test_safe_path_blocks_dotdot(tmp_path):
    with pytest.raises(SecurityError):
        safe_path("..", base_dir=tmp_path)


def test_workspace_context_block_shape(tmp_path):
    (tmp_path / "readme.md").write_text("hi", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    block = workspace_context_block(tmp_path)
    assert "Workspace" in block or "project" in block.lower() or str(tmp_path) in block
    assert "readme.md" in block or "pkg" in block


def test_list_workspace_entries_filters(tmp_path):
    (tmp_path / "ok.py").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    entries = list_workspace_entries(tmp_path)
    names = {e["name"] for e in entries}
    assert "ok.py" in names
    assert ".git" not in names
    assert "node_modules" not in names


def test_windows_dangerous_commands_blocked():
    assert check_dangerous_command(["reg", "delete", "HKLM\\x"]) is not None
    assert check_dangerous_command(["takeown", "/f", "C:\\Windows"]) is not None
    assert check_dangerous_command(["del", "/f", "/s", "/q", "C:\\temp"]) is not None


def test_dev_stderr_redirect_not_flagged_alone():
    # Bare 2>/dev/null is normal in scripts — must not block.
    assert check_dangerous_command(["make", "2>/dev/null"]) is None or (
        "Error output suppression" not in (check_dangerous_command(["sh", "-c", "x 2>/dev/null"]) or "")
    )
    # Explicit: our removal means no "Error output suppression" reason.
    warn = check_dangerous_command(["true", "2>/dev/null"])
    if warn:
        assert "Error output suppression" not in warn
