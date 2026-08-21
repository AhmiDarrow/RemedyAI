"""Jail contract: reads never jailed; writes follow access_scope; copy source is a read.

Pins the owner's complaint from the ExampleProject build session
(``access_scope=full`` + ``approval_mode=auto``): the write jail kept saying
"raise access_scope" while the scope was already full, read-only shell runs
were refused for their workdir, and copying an mp3 from Downloads *into* the
project was denied because the *source* was outside.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.errors import SecurityError
from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    copy_source_operands,
    looks_like_mutation,
)
from remedy.core.workspace import (
    is_remedy_installed_code_path,
    resolve_read_path,
    resolve_under_roots,
    resolve_write_path,
    write_roots_for_scope,
)
from tests.test_project_write_jail import _make_runtime


def _register(tmp_path: Path, proj: Path, monkeypatch, *, scope: str, home: Path):
    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.core.approvals import APPROVALS
    from remedy.skills.tool_registry import ToolRegistry

    monkeypatch.setattr(APPROVALS, "needs_ask", lambda *a, **k: None)
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "auto", "access_scope": scope},
    )
    APPROVALS.set_mode("auto")
    rt = _make_runtime(proj, scope=scope, home=home)
    reg = ToolRegistry()
    rt.tool_registry = reg  # type: ignore[attr-defined]
    rt.config = SimpleNamespace(home_dir=str(tmp_path / "remedy_home"))  # type: ignore[attr-defined]
    rt._session_id = "contract-session"  # type: ignore[attr-defined]
    register_workspace_tools(rt)
    return rt, reg


def _layout(tmp_path: Path):
    home = tmp_path / "home"
    for name in ("Desktop", "Documents", "Downloads"):
        (home / name).mkdir(parents=True)
    proj = tmp_path / "ExampleProject"
    (proj / "public" / "samples").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    return home, proj, elsewhere


# ---------------------------------------------------------------------------
# Write roots follow access_scope
# ---------------------------------------------------------------------------


def test_write_roots_follow_scope(tmp_path: Path):
    home, proj, _ = _layout(tmp_path)
    assert [r.resolve() for r in write_roots_for_scope("project", proj, home=home)] == [
        proj.resolve()
    ]
    assert [r.resolve() for r in write_roots_for_scope("untrusted", proj, home=home)] == [
        proj.resolve()
    ]
    assert [r.resolve() for r in write_roots_for_scope("home", proj, home=home)] == [
        proj.resolve(),
        home.resolve(),
    ]
    assert [r.resolve() for r in write_roots_for_scope("full", proj, home=home)] == [
        proj.resolve(),
        home.resolve(),
    ]


def test_resolve_write_path_scope_matrix(tmp_path: Path):
    home, proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "y.txt"
    in_home = home / "Documents" / "n.txt"

    def w(scope: str, path: Path, approval: str = "auto") -> Path:
        return resolve_write_path(
            str(path),
            roots=write_roots_for_scope(scope, proj, home=home),
            access_scope=scope,
            approval_mode=approval,
            project_bound=True,
        )

    # project: project only
    with pytest.raises(SecurityError):
        w("project", target)
    with pytest.raises(SecurityError):
        w("project", in_home)
    assert w("project", proj / "a.txt") == (proj / "a.txt").resolve()
    # home: project + home
    assert w("home", in_home) == in_home.resolve()
    with pytest.raises(SecurityError):
        w("home", target)
    # full: machine-wide — the owner's configured scope is authoritative
    assert w("full", target) == target.resolve()
    assert w("full", in_home) == in_home.resolve()
    # approval full == full at any scope
    assert w("project", target, approval="full") == target.resolve()


def test_full_scope_never_told_to_raise_scope(tmp_path: Path):
    """A denial under full can only be auth or installed code — never 'raise scope'."""
    home, proj, _ = _layout(tmp_path)
    sidecar = home / ".remedy" / "voice" / "runtime" / "app" / "remedy" / "core" / "b.py"
    with pytest.raises(SecurityError) as ei:
        resolve_write_path(
            str(sidecar),
            roots=write_roots_for_scope("full", proj, home=home),
            access_scope="full",
            approval_mode="auto",
            project_bound=True,
        )
    msg = str(ei.value)
    assert "Remedy's own installed code" in msg
    assert "raise access_scope" not in msg
    assert "Approvals" not in msg


def test_installed_code_detection_and_read_ok(tmp_path: Path, monkeypatch):
    rh = tmp_path / "rh"
    monkeypatch.setenv("REMEDY_HOME", str(rh))
    assert is_remedy_installed_code_path(rh / "voice" / "runtime" / "python.exe")
    assert is_remedy_installed_code_path(
        r"C:\Users\Administrator\.remedy\voice\runtime\app\remedy\core\build_lang_oracle.py"
    )
    assert not is_remedy_installed_code_path(rh / "voice" / "clone" / "x.bin")
    assert not is_remedy_installed_code_path(tmp_path / "proj" / "runtime" / "x.py")
    # reads of installed code are fine at project scope
    p = rh / "voice" / "runtime" / "app" / "x.py"
    out = resolve_read_path(str(p), roots=[tmp_path / "proj"], access_scope="project")
    assert out == p.resolve()


def test_installed_code_refused_under_home_scope_even_though_under_home(tmp_path: Path):
    home, proj, _ = _layout(tmp_path)
    sidecar = home / ".remedy" / "voice" / "runtime" / "app" / "remedy" / "core" / "b.py"
    with pytest.raises(SecurityError) as ei:
        resolve_under_roots(
            str(sidecar), [proj, home], access_scope="home", for_write=True
        )
    assert "installed code" in str(ei.value)
    # the shell jail says the same thing, at every scope / mode
    for scope, mode in (("project", "auto"), ("home", "auto"), ("full", "auto"), ("project", "full")):
        hit = check_shell_write_jail(
            f'Set-Content -Path "{sidecar}" -Value x',
            write_roots=write_roots_for_scope(scope, proj, home=home),
            cwd=proj,
            access_scope=scope,
            approval_mode=mode,
        )
        assert hit and "Remedy's own installed code" in hit, (scope, mode, hit)
    # reading it from the shell is not a mutation
    assert (
        check_shell_write_jail(
            f'type "{sidecar}"',
            write_roots=[proj],
            cwd=proj,
            access_scope="project",
            approval_mode="auto",
        )
        is None
    )


# ---------------------------------------------------------------------------
# Reads are never jailed
# ---------------------------------------------------------------------------


def test_reads_resolve_anywhere_under_project_scope(tmp_path: Path):
    home, proj, elsewhere = _layout(tmp_path)
    rt = _make_runtime(proj, scope="project", home=home)
    for p in (
        home / "Downloads" / "a.mp3",
        home / "Documents" / "comfy" / "ComfyUI",
        home / "AppData" / "Local" / "Remedy Desktop" / "log.txt",
        elsewhere / "z.txt",
    ):
        assert rt.resolve_tool_path(str(p)) == p.resolve()
    # untrusted is the one explicit sandbox
    rt = _make_runtime(proj, scope="untrusted", home=home)
    with pytest.raises(SecurityError):
        rt.resolve_tool_path(str(elsewhere / "z.txt"))


@pytest.mark.asyncio
async def test_read_tools_work_outside_project_under_project_scope(tmp_path: Path, monkeypatch):
    home, proj, elsewhere = _layout(tmp_path)
    dl = home / "Downloads"
    (dl / "Born to Run.mp3").write_bytes(b"ID3")
    docs = home / "Documents" / "comfy" / "ComfyUI"
    docs.mkdir(parents=True)
    (docs / "main.py").write_text("def needle():\n    return 1\n", encoding="utf-8")
    appd = home / "AppData" / "Local" / "Remedy Desktop"
    appd.mkdir(parents=True)
    (appd / "sidecar.log").write_text("hello from log", encoding="utf-8")
    (elsewhere / "notes.txt").write_text("far away", encoding="utf-8")

    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="project", home=home)

    out = await reg.execute("file_glob", pattern="*.mp3", path=str(dl))
    assert "Born to Run.mp3" in out and "PATH_DENIED" not in out
    out = await reg.execute("list_dir", path=str(appd))
    assert "sidecar.log" in out and "PATH_DENIED" not in out
    out = await reg.execute("file_read", path=str(appd / "sidecar.log"))
    assert "hello from log" in out
    out = await reg.execute("file_read", path=str(elsewhere / "notes.txt"))
    assert "far away" in out
    out = await reg.execute("repo_search", pattern="needle", path=str(docs))
    assert "main.py" in out and "PATH_DENIED" not in out


@pytest.mark.asyncio
async def test_host_run_readonly_workdir_outside_project_ok(tmp_path: Path, monkeypatch):
    home, proj, _ = _layout(tmp_path)
    comfy = home / "Documents" / "comfy" / "ComfyUI"
    comfy.mkdir(parents=True)
    (comfy / "marker.txt").write_text("x", encoding="utf-8")
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="project", home=home)

    argv = [sys.executable, "-c", "import os; print(sorted(os.listdir('.')))"]
    out = await reg.execute("host_run", argv=argv, workdir=str(comfy))
    assert "BAD_WORKDIR" not in out, out
    assert "WRITE_JAIL" not in out, out
    assert "marker.txt" in out, out

    # A mutating command still needs its workdir under the write roots.
    out = await reg.execute(
        "bash_exec", command="echo pwn > dropped.txt", workdir=str(comfy)
    )
    assert "BAD_WORKDIR" in out, out
    assert not (comfy / "dropped.txt").exists()
    assert "access_scope=project" in out or "project" in out


@pytest.mark.asyncio
async def test_run_python_file_interpreter_path_is_not_bad_path(tmp_path: Path, monkeypatch):
    """Passing python.exe as *path* is a wrong-tool mistake, not a jail denial."""
    home, proj, _ = _layout(tmp_path)
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="project", home=home)
    out = await reg.execute("run_python_file", path=sys.executable)
    assert "BAD_PATH" not in out, out
    assert "NOT_PYTHON" in out, out


@pytest.mark.asyncio
async def test_run_python_file_script_outside_project_readable(tmp_path: Path, monkeypatch):
    home, proj, _ = _layout(tmp_path)
    helper = home / "Documents" / "probe.py"
    helper.write_text("print('from docs')\n", encoding="utf-8")
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="project", home=home)
    out = await reg.execute("run_python_file", path=str(helper))
    assert "BAD_PATH" not in out, out
    assert "from docs" in out, out


# ---------------------------------------------------------------------------
# Copy: source is a read, destination is the write
# ---------------------------------------------------------------------------


def _copy_cases(src: Path, dst: Path) -> list[tuple[str, str]]:
    return [
        ("cmd copy", f'copy "{src}" "{dst}"'),
        ("cmd copy /Y", f'copy /Y "{src}" "{dst}"'),
        ("xcopy", f'xcopy /Y "{src}" "{dst.parent}\\"'),
        ("robocopy", f'robocopy "{src.parent}" "{dst.parent}" "{src.name}" /NJH'),
        ("Copy-Item named", f'Copy-Item -Path "{src}" -Destination "{dst}" -Force'),
        ("Copy-Item positional", f'Copy-Item "{src}" "{dst}"'),
        ("Copy-Item LiteralPath", f'Copy-Item -LiteralPath "{src}" -Destination "{dst.parent}" -Recurse'),
        ("cp", f'cp "{src}" "{dst}"'),
        ("cp -r", f'cp -r "{src}" "{dst}"'),
        (
            "python shutil.copy",
            f"python -c \"import shutil; shutil.copy(r'{src}', r'{dst}')\"",
        ),
        (
            "python shutil.copy2",
            f"python -c \"import shutil; shutil.copy2(r'{src}', r'{dst}')\"",
        ),
    ]


def test_copy_source_operands():
    assert copy_source_operands(r'copy "C:\Users\X\Downloads\Born to Run.mp3" "C:\p\a.mp3"') == {
        r"c:\users\x\downloads\born to run.mp3"
    }
    assert copy_source_operands(r"robocopy C:\Users\X\Downloads C:\p\samples *.mp3 /E") == {
        r"c:\users\x\downloads"
    }
    assert copy_source_operands(r"cp -r /home/x/Downloads/a.mp3 /proj/a.mp3") == {
        "/home/x/downloads/a.mp3"
    }
    assert copy_source_operands(
        r'Copy-Item -Destination "C:\p\a.mp3" -Path "C:\Users\X\Downloads\a.mp3"'
    ) == {r"c:\users\x\downloads\a.mp3"}
    # move / rename are not copies — the source is mutated
    assert copy_source_operands(r'move "C:\Users\X\Downloads\a.mp3" "C:\p\a.mp3"') == set()
    assert copy_source_operands(r'Move-Item "C:\Users\X\Downloads\a.mp3" "C:\p\a.mp3"') == set()


def test_copy_into_project_from_downloads_allowed(tmp_path: Path):
    home, proj, _ = _layout(tmp_path)
    src = home / "Downloads" / "Born to Run.mp3"
    src.write_bytes(b"ID3")
    dst = proj / "public" / "samples" / "born.mp3"
    for label, cmd in _copy_cases(src, dst):
        assert looks_like_mutation(cmd), label
        hit = check_shell_write_jail(
            cmd,
            write_roots=[proj],
            cwd=proj,
            project_bound=True,
            access_scope="project",
            approval_mode="auto",
        )
        assert hit is None, (label, hit)


def test_copy_out_of_project_still_jailed(tmp_path: Path):
    home, proj, _ = _layout(tmp_path)
    inside = proj / "public" / "samples" / "born.mp3"
    outside = home / "Downloads" / "leak.mp3"
    for label, cmd in _copy_cases(inside, outside):
        hit = check_shell_write_jail(
            cmd,
            write_roots=[proj],
            cwd=proj,
            project_bound=True,
            access_scope="project",
            approval_mode="auto",
        )
        assert hit is not None, (label, cmd)
        assert "(project)" in hit or "cannot be proven" in hit, (label, hit)
        assert "raise access_scope to home/full" in hit or "cannot be proven" in hit, hit


def test_copy_from_auth_secrets_still_refused(tmp_path: Path):
    home, proj, _ = _layout(tmp_path)
    secret = home / ".remedy" / "auth" / "provider_keys.json"
    dst = proj / "keys.json"
    for _label, cmd in _copy_cases(secret, dst):
        hit = check_shell_write_jail(
            cmd,
            write_roots=[proj],
            cwd=proj,
            project_bound=True,
            access_scope="project",
            approval_mode="auto",
        )
        assert hit is not None and "auth" in hit.lower(), (cmd, hit)


@pytest.mark.asyncio
async def test_bash_exec_copies_mp3_from_downloads_into_project(tmp_path: Path, monkeypatch):
    home, proj, _ = _layout(tmp_path)
    src = home / "Downloads" / "Born to Run.mp3"
    src.write_bytes(b"ID3mp3")
    dst = proj / "public" / "samples" / "born.mp3"
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="project", home=home)
    # host_run: argv form, no shell translation in the way
    argv = [sys.executable, "-c", f"import shutil; shutil.copy(r'{src}', r'{dst}')"]
    out = await reg.execute("host_run", argv=argv)
    assert "WRITE_JAIL" not in out, out
    assert dst.exists() and dst.read_bytes() == b"ID3mp3", out


# ---------------------------------------------------------------------------
# Full scope end-to-end: writes outside the project are fine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_scope_file_write_outside_project(tmp_path: Path, monkeypatch):
    home, proj, elsewhere = _layout(tmp_path)
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="full", home=home)
    target = elsewhere / "full.txt"
    out = await reg.execute("file_write", path=str(target), content="machine-wide")
    assert "PATH_DENIED" not in out, out
    assert target.read_text(encoding="utf-8") == "machine-wide"


@pytest.mark.asyncio
async def test_full_scope_bash_exec_outside_project(tmp_path: Path, monkeypatch):
    home, proj, elsewhere = _layout(tmp_path)
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="full", home=home)
    target = elsewhere / "shell.txt"
    out = await reg.execute(
        "bash_exec",
        command=f'Set-Content -Path "{target}" -Value hi'
        if os.name == "nt"
        else f'echo hi > "{target}"',
        workdir=str(elsewhere),
    )
    assert "WRITE_JAIL" not in out and "BAD_WORKDIR" not in out, out
    assert target.exists()


@pytest.mark.asyncio
async def test_full_scope_sidecar_write_refused_with_clear_message(tmp_path: Path, monkeypatch):
    home, proj, _ = _layout(tmp_path)
    _rt, reg = _register(tmp_path, proj, monkeypatch, scope="full", home=home)
    sidecar = home / ".remedy" / "voice" / "runtime" / "app" / "remedy" / "core" / "build_lang_oracle.py"
    out = await reg.execute("file_write", path=str(sidecar), content="patched")
    assert "PATH_DENIED" in out
    assert "Remedy's own installed code" in out
    assert "raise access_scope" not in out and "Approvals" not in out
    assert not sidecar.exists()
    out = await reg.execute("bash_exec", command=f'Set-Content -Path "{sidecar}" -Value x')
    assert "WRITE_JAIL" in out and "Remedy's own installed code" in out
    assert not sidecar.exists()


def test_shell_jail_denial_messages_match_scope(tmp_path: Path):
    home, proj, elsewhere = _layout(tmp_path)
    target = elsewhere / "x.txt"
    hit = check_shell_write_jail(
        f'Set-Content -Path "{target}" -Value x',
        write_roots=write_roots_for_scope("project", proj, home=home),
        cwd=proj,
        access_scope="project",
        approval_mode="auto",
    )
    assert hit and "(project)" in hit and "access_scope=project" in hit
    hit = check_shell_write_jail(
        f'Set-Content -Path "{target}" -Value x',
        write_roots=write_roots_for_scope("home", proj, home=home),
        cwd=proj,
        access_scope="home",
        approval_mode="auto",
    )
    assert hit and "(home)" in hit and "access_scope=full" in hit
    assert "raise access_scope to home" not in hit
    assert (
        check_shell_write_jail(
            f'Set-Content -Path "{target}" -Value x',
            write_roots=write_roots_for_scope("full", proj, home=home),
            cwd=proj,
            access_scope="full",
            approval_mode="auto",
        )
        is None
    )
