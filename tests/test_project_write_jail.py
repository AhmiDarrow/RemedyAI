"""Project write jail: reads may leave the folder; writes may not (project scope)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from remedy.core.errors import SecurityError
from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    extract_path_candidates,
    looks_like_mutation,
)
from remedy.core.workspace import (
    allowed_roots_for_scope,
    resolve_under_roots,
    write_roots_for_scope,
    workspace_context_block,
)


def _make_runtime(proj: Path, *, scope: str = "project", home: Path | None = None):
    """Minimal runtime-shaped object with real resolve/write roots."""
    from remedy.core.workspace import (
        allowed_roots_for_scope as _ar,
        write_roots_for_scope as _wr,
        resolve_under_roots as _ru,
        effective_access_scope,
    )

    home = home or (proj.parent / "homeuser")
    if not home.exists():
        home.mkdir(parents=True, exist_ok=True)

    class RT:
        def access_scope(self) -> str:
            return effective_access_scope(scope, str(proj))

        def effective_project_path(self) -> Path:
            return proj.resolve()

        def allowed_roots(self):
            return _ar(self.access_scope(), proj, home=home)

        def write_roots(self):
            return _wr(self.access_scope(), proj, home=home)

        def project_path_is_unset(self) -> bool:
            return False

        def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
            # Mirror BasicRuntime: project-bound writes never use full bypass.
            if for_write:
                roots = self.write_roots()
                scope = self.access_scope()
                enforce = "home" if scope == "home" else "project"
                return _ru(path or ".", roots, access_scope=enforce)
            return _ru(
                path or ".", self.allowed_roots(), access_scope=self.access_scope()
            )

        def _track_artifact(self, _p: str) -> None:
            pass

        def _register_comfyui_tools(self) -> None:
            pass

        def _register_vision_tools(self) -> None:
            pass

        def _register_local_discover_tools(self) -> None:
            pass

        def _register_skill_tools(self) -> None:
            pass

    return RT()


def test_write_roots_exclude_profile_folders(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    for name in ("Desktop", "Documents", "Downloads"):
        (home / name).mkdir()
    proj = tmp_path / "MyProject"
    proj.mkdir()

    reads = [r.resolve() for r in allowed_roots_for_scope("project", proj, home=home)]
    writes = [r.resolve() for r in write_roots_for_scope("project", proj, home=home)]

    assert proj.resolve() in reads
    assert (home / "Desktop").resolve() in reads
    assert writes == [proj.resolve()]


def test_runtime_resolve_read_desktop_ok_write_denied(tmp_path: Path):
    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    note = desk / "notes.txt"
    note.write_text("research", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()

    rt = _make_runtime(proj, scope="project", home=home)
    # Read path OK
    assert rt.resolve_tool_path(str(note), for_write=False) == note.resolve()
    # Write path denied
    with pytest.raises(SecurityError):
        rt.resolve_tool_path(str(desk / "escape.txt"), for_write=True)
    with pytest.raises(SecurityError):
        rt.resolve_tool_path(str(note), for_write=True)


def test_runtime_write_inside_project_ok(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    target = proj / "ok.txt"
    rt = _make_runtime(proj, scope="project", home=home)
    resolved = rt.resolve_tool_path("ok.txt", for_write=True)
    assert resolved == target.resolve()
    resolved = rt.resolve_tool_path(str(target), for_write=True)
    assert resolved == target.resolve()


def test_home_scope_allows_write_under_home(tmp_path: Path):
    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    rt = _make_runtime(proj, scope="home", home=home)
    p = rt.resolve_tool_path(str(desk / "x.txt"), for_write=True)
    assert p == (desk / "x.txt").resolve()


def test_full_scope_with_project_still_blocks_write_outside(tmp_path: Path):
    """full access expands reads, not project write jail."""
    home = tmp_path / "home"
    home.mkdir()
    desk = home / "Desktop"
    desk.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "y.txt"
    rt = _make_runtime(proj, scope="full", home=home)
    # Reads may still be broad under full; writes stay in project.
    with pytest.raises(SecurityError):
        rt.resolve_tool_path(str(target), for_write=True)
    with pytest.raises(SecurityError):
        rt.resolve_tool_path(str(desk / "nope.txt"), for_write=True)
    ok = rt.resolve_tool_path("in_proj.txt", for_write=True)
    assert ok == (proj / "in_proj.txt").resolve()


@pytest.mark.asyncio
async def test_file_write_tool_blocks_desktop(tmp_path: Path):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.skills.tool_registry import ToolRegistry

    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    proj = tmp_path / "proj"
    proj.mkdir()

    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    # Attach registry + config-like attrs expected by tools
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "test-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)

    result = await reg.execute(
        "file_write", path=str(desk / "escape_jail.txt"), content="nope"
    )
    assert "PATH_DENIED" in result or "path not allowed" in result.lower()
    assert not (desk / "escape_jail.txt").exists()

    # Inside project still works
    ok = await reg.execute("file_write", path="inside.txt", content="yes")
    assert "PATH_DENIED" not in ok
    assert (proj / "inside.txt").read_text(encoding="utf-8") == "yes"


@pytest.mark.asyncio
async def test_file_read_tool_allows_desktop(tmp_path: Path):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.skills.tool_registry import ToolRegistry

    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    note = desk / "research.txt"
    note.write_text("outside knowledge", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()

    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "test-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)

    result = await reg.execute("file_read", path=str(note))
    assert "outside knowledge" in result
    assert "PATH_DENIED" not in result


@pytest.mark.asyncio
async def test_file_edit_tool_blocks_desktop(tmp_path: Path):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.skills.tool_registry import ToolRegistry

    home = tmp_path / "home"
    desk = home / "Desktop"
    desk.mkdir(parents=True)
    note = desk / "research.txt"
    note.write_text("alpha", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "local.txt").write_text("alpha", encoding="utf-8")

    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "test-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)

    denied = await reg.execute(
        "file_edit", path=str(note), old_string="alpha", new_string="beta"
    )
    assert "PATH_DENIED" in denied or "path not allowed" in denied.lower()
    assert note.read_text(encoding="utf-8") == "alpha"

    ok = await reg.execute(
        "file_edit", path="local.txt", old_string="alpha", new_string="beta"
    )
    assert "PATH_DENIED" not in ok
    assert (proj / "local.txt").read_text(encoding="utf-8") == "beta"


def test_workspace_context_mentions_write_jail(tmp_path: Path):
    block = workspace_context_block(tmp_path, access_scope="project")
    assert "focus folder" in block.lower() or "project" in block.lower()
    assert "file_write" in block or "edit" in block.lower()


def test_shell_write_jail_blocks_sibling_set_content(tmp_path: Path):
    """SecretSticky must not Set-Content into SecretFolder (sibling trees)."""
    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()
    roots = [sticky.resolve()]

    cmd = (
        f'Set-Content -Path "{folder / "vault.rs"}" -Value "pwned" -Encoding utf8'
    )
    assert looks_like_mutation(cmd)
    hit = check_shell_write_jail(
        cmd,
        write_roots=roots,
        cwd=sticky,
        project_bound=True,
        access_scope="project",
    )
    assert hit is not None
    assert "WRITE" in hit.upper() or "outside" in hit.lower() or "jail" in hit.lower()

    # Inside focus is fine
    ok_cmd = f'Set-Content -Path "{sticky / "ok.txt"}" -Value "yes"'
    assert (
        check_shell_write_jail(
            ok_cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is None
    )

    # Read-only with absolute sibling path is allowed (research)
    read_cmd = f'Get-Content "{folder / "README.md"}"'
    assert (
        check_shell_write_jail(
            read_cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is None
    )


def test_shell_write_jail_blocks_sc_copy_python_and_opaque(tmp_path: Path):
    """Review P0: sc/copy/python -c/env path mutation must not slip past jail."""
    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()
    roots = [sticky.resolve()]
    target = folder / "pwn.txt"

    cases = [
        f'sc "{target}" "hi"',
        f'copy a.txt "{target}"',
        f'xcopy a.txt "{target}"',
        f'python -c "open(r\'{target}\', \'w\').write(\'x\')"',
        r'Set-Content -Path $env:USERPROFILE\Desktop\leak.txt -Value z',
        r'Set-Content -Path (Join-Path $env:USERPROFILE "SecretFolder\x") -Value z',
    ]
    for cmd in cases:
        assert looks_like_mutation(cmd), cmd
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for: {cmd}"


def test_shell_write_jail_blocks_relative_escape(tmp_path: Path):
    proj = tmp_path / "SecretSticky"
    sibling = tmp_path / "SecretFolder"
    proj.mkdir()
    sibling.mkdir()
    cmd = r'Set-Content -Path "..\SecretFolder\leak.txt" -Value x'
    hit = check_shell_write_jail(
        cmd,
        write_roots=[proj.resolve()],
        cwd=proj,
        project_bound=True,
        access_scope="project",
    )
    assert hit is not None


def test_shell_write_jail_extracts_windows_paths():
    paths = extract_path_candidates(
        r'Set-Content C:\Users\Administrator\SecretFolder\x.rs -Value a'
    )
    assert any("SecretFolder" in p for p in paths)


@pytest.mark.asyncio
async def test_bash_exec_enforces_shell_write_jail(tmp_path: Path, monkeypatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    rt = _make_runtime(sticky, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "jail-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)

    bad = (
        f'Set-Content -Path "{folder / "pwn.txt"}" -Value "nope" -Encoding utf8'
    )
    result = await reg.execute("bash_exec", command=bad)
    assert "WRITE_JAIL" in result or "jail" in result.lower() or "outside" in result.lower()
    assert not (folder / "pwn.txt").exists()


@pytest.mark.asyncio
async def test_bash_exec_write_roots_fail_closed(tmp_path: Path, monkeypatch):
    """write_roots() failure must not fall back to Desktop/Docs read roots."""
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    proj = tmp_path / "proj"
    proj.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "jail-session"  # type: ignore[attr-defined]

    def _boom():
        raise RuntimeError("roots unavailable")

    rt.write_roots = _boom  # type: ignore[method-assign]
    register_workspace_tools(rt)
    out = await reg.execute("bash_exec", command="echo hi")
    assert "WRITE_JAIL" in out
    assert "roots" in out.lower()


@pytest.mark.asyncio
async def test_update_settings_refuses_project_switch_without_force(tmp_path: Path):
    """Regression: agent must not silently retarget SecretSticky → SecretFolder."""
    from remedy.core.agent_settings_tools import register_settings_tools
    from remedy.skills.tool_registry import ToolRegistry

    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()

    class RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()
            self._proj = sticky.resolve()

        def project_path_is_unset(self) -> bool:
            return False

        def effective_project_path(self) -> Path:
            return self._proj

    rt = RT()
    register_settings_tools(rt)
    out = await rt.tool_registry.execute(
        "update_settings",
        project_path=str(folder),
    )
    assert "PROJECT_JAIL" in out or "refusing to switch" in out.lower()

