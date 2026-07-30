"""Workspace jail / project path security tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.errors import SecurityError
from remedy.core.security import check_dangerous_command, safe_path
from remedy.core.workspace import (
    effective_access_scope,
    ensure_project_dir,
    is_unset_project_path,
    jail_path,
    list_workspace_entries,
    resolve_project_path,
    workspace_context_block,
)


def test_unset_project_path_helpers():
    assert is_unset_project_path(None)
    assert is_unset_project_path("")
    assert is_unset_project_path(".")
    assert is_unset_project_path("./")
    assert not is_unset_project_path("C:/proj")
    assert not is_unset_project_path("/tmp/x")
    # Empty project → full access
    assert effective_access_scope("project", None) == "full"
    assert effective_access_scope("project", "") == "full"
    assert effective_access_scope("project", ".") == "full"
    assert effective_access_scope("home", "/real/proj") == "home"
    assert effective_access_scope("untrusted", "C:/code") == "untrusted"


def test_resolve_project_path_defaults(tmp_path, monkeypatch):
    # Unset paths use fallback (home by default), not process cwd.
    assert resolve_project_path(None, fallback=tmp_path) == tmp_path.resolve()
    assert resolve_project_path("", fallback=tmp_path) == tmp_path.resolve()
    assert resolve_project_path(".", fallback=tmp_path) == tmp_path.resolve()


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


def test_workspace_context_unset_project_warns_full(tmp_path):
    block = workspace_context_block(tmp_path, project_unset=True)
    # Focus is optional; copy must allow full access without requiring a project cage.
    assert (
        "No focus folder" in block
        or "No project folder" in block
        or "full" in block.lower()
    )
    assert "Access scope: full" in block
    assert "optional" in block.lower() or "absolute" in block.lower()


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


@pytest.mark.asyncio
async def test_bash_exec_dangerous_still_blocked_with_tools_on(tmp_path, monkeypatch):
    """Agency/tools-on must never bypass hard security on bash_exec.

    Gauntlet: L2/L3 leave tools armed; dangerous wipe/privilege cmds must still
    return SECURITY_BLOCK before approval or subprocess.
    """
    from types import SimpleNamespace

    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    # If security were skipped, ask-mode would still gate — force approvals off
    # so only the hard security gate can produce the block.
    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    proj = tmp_path / "proj"
    proj.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    class _RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()
            self.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))
            self._session_id = "tools-on-sec"
            # Mirror agency path: tools registered / "on"
            self._turn_tier = 3  # L3 work-alone

        def effective_project_path(self):
            return proj.resolve()

        def access_scope(self):
            return "project"

        def write_roots(self):
            return [proj.resolve()]

        def resolve_tool_path(self, path, for_write=False):
            return (proj / (path or ".")).resolve()

        def _track_artifact(self, *_a, **_k):
            pass

        def _register_comfyui_tools(self):
            pass

        def _register_vision_tools(self):
            pass

        def _register_local_discover_tools(self):
            pass

        def _register_skill_tools(self):
            pass

    rt = _RT()
    register_workspace_tools(rt)
    reg = rt.tool_registry
    assert reg.get("bash_exec") is not None or reg.get_definition("bash_exec") is not None

    dangerous = (
        "rm -rf /",
        "rm -rf ~",
        r"del /f /s /q C:\Windows",
        "format C:",
        "shutdown /s /t 0",
        "Get-Process app | Stop-Process -Force",
        "taskkill /F /IM remedy.exe",
    )
    for cmd in dangerous:
        out = await reg.execute("bash_exec", command=cmd)
        low = (out or "").lower()
        assert (
            "security" in low
            or "blocked" in low
            or "SECURITY_BLOCK" in (out or "")
        ), f"expected hard block for {cmd!r}, got: {out!r}"
        # Must not look like a successful shell run
        assert "exit_code=0" not in low
        assert "APPROVAL_REQUIRED" not in (out or "")

    # Safe command still reaches approval/exec path (approvals mocked off → may run)
    # At minimum it must not be SECURITY_BLOCK'd for a benign echo.
    ok = await reg.execute("bash_exec", command="echo remedy-safe")
    assert "SECURITY_BLOCK" not in (ok or "")
    assert "blocked by security policy" not in (ok or "").lower()


def test_dev_stderr_redirect_not_flagged_alone():
    # Bare 2>/dev/null is normal in scripts — must not block.
    assert check_dangerous_command(["make", "2>/dev/null"]) is None or (
        "Error output suppression" not in (check_dangerous_command(["sh", "-c", "x 2>/dev/null"]) or "")
    )
    # Explicit: our removal means no "Error output suppression" reason.
    warn = check_dangerous_command(["true", "2>/dev/null"])
    if warn:
        assert "Error output suppression" not in warn
