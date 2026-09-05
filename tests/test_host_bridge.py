"""Host Bridge — translator, IR, script-file, dialect, runner, session."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from remedy.core.computer import host_binding
from remedy.core.computer.host_binding import looks_like_powershell, translate_posix_to_host
from remedy.core.computer.host_binding import (
    conpty_available,
    launch_script,
    resolve_which,
)
from remedy.core.computer.host_binding import HostOp, mkdir_op, run_op, script_op
from remedy.core.computer.host_binding import (
    coerce_argv,
    prepare_host_command,
    prepare_host_op,
)
from remedy.execution.process import win_shell_prefix
from remedy.execution.runtime import ToolRuntime


def test_translate_mkdir_p() -> None:
    r = translate_posix_to_host("mkdir -p src/foo tests", host="cmd")
    assert r.get("changed")
    assert "if not exist" in r.get("text")
    assert "src\\foo" in r.get("text") or "src/foo" in r.get("text").replace("\\", "/")
    # Trailing `\"` escapes the closer in cmd — must use `\.` instead.
    assert '\\"' not in r.get("text")
    assert "\\." in r.get("text")


def test_translate_rm_rf() -> None:
    r = translate_posix_to_host("rm -rf build", host="cmd")
    assert r.get("changed")
    assert "rmdir" in r.get("text")
    assert "build" in r.get("text")
    assert '\\"' not in r.get("text")


def test_translate_export_and_dev_null() -> None:
    r = translate_posix_to_host("export FOO=bar && echo hi >/dev/null", host="cmd")
    assert 'set "FOO=bar"' in r.get("text") or "set FOO=bar" in r.get("text")
    assert "NUL" in r.get("text")
    assert "/dev/null" not in r.get("text")


def test_translate_rm_chain_and_plain_del() -> None:
    """cmd IF must not swallow `&& next`; plain rm must use _q()."""
    rec = translate_posix_to_host("rm -rf build && echo next", host="cmd")
    assert rec.get("changed")
    assert rec.get("text").strip().startswith("(")
    assert rec.get("text").count("(") >= 2
    # After the IF group, the chain operator must still be there.
    assert "&&" in rec.get("text") or "& echo" in rec.get("text").lower()
    plain = translate_posix_to_host('rm foo"&calc', host="cmd")
    assert "del" in plain.get("text")
    assert '"foo""&calc"' in plain.get("text")


def test_translate_q_escapes_quote_amp() -> None:
    """Zig cmd quoting doubles embedded quotes (``"foo""&calc"``)."""
    text = translate_posix_to_host('cat foo"&calc', host="cmd").get("text")
    assert "type" in text
    assert '"foo""&calc"' in text


def test_translate_ls_cat_pwd_which() -> None:
    assert translate_posix_to_host("ls", host="cmd").get("text") == "dir"
    assert "type" in translate_posix_to_host("cat README.md", host="cmd").get("text")
    assert translate_posix_to_host("pwd", host="cmd").get("text") == "cd"
    assert translate_posix_to_host("which git", host="cmd").get("text").startswith("where")
    assert translate_posix_to_host("which 'foo&calc'", host="cmd").get("text") == 'where "foo&calc"'


def test_refuse_os_open_text_document() -> None:
    from remedy.core.computer.desktop_win import refuse_os_open_text_document

    try:
        refuse_os_open_text_document("README.md")
    except ValueError as e:
        msg = str(e)
        assert "file_read" in msg
        assert "README.md" in msg
    else:
        raise AssertionError("expected ValueError")


def test_translate_start_md_types_instead_of_os_open() -> None:
    r = translate_posix_to_host("start README.md", host="cmd")
    assert "type" in r.get("text").lower()
    assert "notepad" not in r.get("text").lower()
    assert "README.md" in r.get("text")
    r2 = translate_posix_to_host('start "" notes.md', host="cmd")
    assert "type" in r2.get("text").lower()
    assert "notepad" not in r2.get("text").lower()
    r3 = translate_posix_to_host("explorer README.md", host="cmd")
    assert "type" in r3.get("text").lower()
    r4 = translate_posix_to_host("cmd /c start index.html", host="cmd")
    assert "type" in r4.get("text").lower()
    r5 = translate_posix_to_host("start package.json", host="cmd")
    assert "type" in r5.get("text").lower()


def _exe_stem(name: str) -> str:
    head = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return head[:-4] if head.endswith(".exe") else head


def _chain_hops(argv: list[str]) -> list[dict] | None:
    """Zig ``shell_chain_expand`` hops; None when not a multi-hop chain."""
    result = host_binding.shell_chain_expand({"argv": argv})
    hops = result.get("hops")
    if not hops or not isinstance(hops, list) or len(hops) < 2:
        return None
    return [h for h in hops if isinstance(h, dict)]


def _all_run_argvs(argv: list[str]) -> list[list[str]] | None:
    hops = _chain_hops(argv)
    if not hops or any(str(h.get("kind") or "") != "run" for h in hops):
        return None
    return [[str(a) for a in (h.get("argv") or [])] for h in hops]


def test_expand_and_chain_splits_git_without_cmd() -> None:
    hops = _all_run_argvs(["cmd.exe", "/c", 'git add . && git commit -m "wip"'])
    assert hops is not None
    assert _exe_stem(hops[0][0]) == "git"
    assert hops[0][1:] == ["add", "."]
    assert _exe_stem(hops[1][0]) == "git"
    assert hops[1][1:3] == ["commit", "-m"]
    assert hops[1][3] == "wip"
    # Quote-aware && stays one hop (Zig shell_chain — no Python twin).
    quoted = _all_run_argvs(
        ["cmd.exe", "/c", 'git commit -m "fix: a && b" && git status']
    )
    assert quoted is not None
    assert len(quoted) == 2
    assert _exe_stem(quoted[0][0]) == "git"
    assert quoted[0][1:3] == ["commit", "-m"]
    assert quoted[0][3] == "fix: a && b"
    assert _exe_stem(quoted[1][0]) == "git"
    assert quoted[1][1:] == ["status"]
    assert _all_run_argvs(["cmd", "/c", "git status"]) is None
    # mkdir is not a plain run hop — Zig returns mkdir+run.
    assert _all_run_argvs(["cmd.exe", "/c", "mkdir -p a && git add ."]) is None


def test_expand_shell_chain_cd_and_mkdir() -> None:
    cd_hops = _chain_hops(["cmd.exe", "/c", "cd src && pytest -q"])
    assert cd_hops is not None
    assert [str(h.get("kind")) for h in cd_hops] == ["cd", "run"]
    assert list(cd_hops[0].get("paths") or []) == ["src"]
    assert _exe_stem((cd_hops[1].get("argv") or ["?"])[0]) == "pytest"
    mk_hops = _chain_hops(
        ["cmd.exe", "/c", '(if not exist "out\\." mkdir "out") && git add .']
    )
    assert mk_hops is not None
    assert [str(h.get("kind")) for h in mk_hops] == ["mkdir", "run"]
    assert list(mk_hops[0].get("paths") or []) == ["out"]
    posix_mk = _chain_hops(["sh", "-c", "mkdir -p build && git status"])
    assert posix_mk is not None
    assert str(posix_mk[0].get("kind")) == "mkdir"
    assert "build" in list(posix_mk[0].get("paths") or [])


@pytest.mark.asyncio
async def test_sandbox_mkdir_chain_creates_dir(tmp_path) -> None:
    from remedy.execution.result import SubprocessSandbox

    py = sys.executable

    def q(s: str) -> str:
        return f'"{s}"' if " " in str(s) else str(s)

    dest = tmp_path / "made"
    body = f"mkdir -p {q(dest)} && {q(py)} -c \"print('mkdir-ok')\""
    argv = ["cmd.exe", "/c", body] if os.name == "nt" else ["sh", "-c", body]
    res = await SubprocessSandbox(allowed_paths=[tmp_path]).execute(
        argv, workdir=tmp_path, timeout_seconds=20
    )
    assert res.exit_code == 0, res.stderr
    assert dest.is_dir()
    assert "mkdir-ok" in (res.stdout or "")


@pytest.mark.asyncio
async def test_sandbox_cd_chain_runs_in_subdir(tmp_path) -> None:
    from remedy.execution.result import SubprocessSandbox

    py = sys.executable

    def q(s: str) -> str:
        return f'"{s}"' if " " in str(s) else str(s)

    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "marker.py").write_text("print('from-src')\n", encoding="utf-8")
    body = f"cd src && {q(py)} marker.py"
    argv = ["cmd.exe", "/c", body] if os.name == "nt" else ["sh", "-c", body]
    res = await SubprocessSandbox(allowed_paths=[tmp_path]).execute(
        argv, workdir=tmp_path, timeout_seconds=20
    )
    assert res.exit_code == 0, res.stderr
    assert "from-src" in (res.stdout or "")


@pytest.mark.asyncio
async def test_sandbox_cd_chain_stays_in_jail(tmp_path) -> None:
    from remedy.execution.result import SubprocessSandbox

    py = sys.executable

    def q(s: str) -> str:
        return f'"{s}"' if " " in str(s) else str(s)

    outside = tmp_path.parent
    body = f"cd {q(outside)} && {q(py)} -c \"print('escaped')\""
    argv = ["cmd.exe", "/c", body] if os.name == "nt" else ["sh", "-c", body]
    res = await SubprocessSandbox(allowed_paths=[tmp_path]).execute(
        argv, workdir=tmp_path, timeout_seconds=20
    )
    assert res.exit_code != 0
    assert "escaped" not in (res.stdout or "")
    assert "allowed" in (res.stderr or "").lower() or "jail" in (res.stderr or "").lower() or "not in allowed" in (res.stderr or "")


@pytest.mark.asyncio
async def test_sandbox_and_chain_runs_both_hops() -> None:
    from remedy.execution.result import SubprocessSandbox

    py = sys.executable

    def q(s: str) -> str:
        return f'"{s}"' if " " in s else s

    body = f"{q(py)} -c \"print('chain-a')\" && {q(py)} -c \"print('chain-b')\""
    argv = (
        ["cmd.exe", "/c", body] if os.name == "nt" else ["sh", "-c", body]
    )
    res = await SubprocessSandbox().execute(argv, timeout_seconds=20)
    assert res.exit_code == 0
    assert "chain-a" in (res.stdout or "")
    assert "chain-b" in (res.stdout or "")


@pytest.mark.asyncio
async def test_sandbox_and_chain_stops_on_failure() -> None:
    from remedy.execution.result import SubprocessSandbox

    py = sys.executable

    def q(s: str) -> str:
        return f'"{s}"' if " " in s else s

    body = (
        f"{q(py)} -c \"raise SystemExit(3)\" && {q(py)} -c \"print('chain-nope')\""
    )
    argv = (
        ["cmd.exe", "/c", body] if os.name == "nt" else ["sh", "-c", body]
    )
    res = await SubprocessSandbox().execute(argv, timeout_seconds=20)
    assert res.exit_code == 3
    assert "chain-nope" not in (res.stdout or "")


def test_prepare_deflates_uv_run_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Zig ``host_op_prepare`` owns uv-run deflate (no Python twin)."""
    py = tmp_path / ("python.exe" if os.name == "nt" else "python")
    py.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("REMEDY_PYTHON", str(py))
    prep = prepare_host_command("uv run pytest -q", host="cmd", project_path=tmp_path)
    joined = " ".join(prep.argv).lower()
    assert "pytest" in joined
    assert prep.kind in {"argv", "translated", "raw", "script"}


def test_prepare_strips_pytest_last_failed() -> None:
    from remedy.core.computer.host_binding import prepare_host_command

    prep = prepare_host_command("pytest -q --lf", host="cmd")
    blob = " ".join(prep.argv) + " " + prep.display
    assert "--lf" not in blob
    assert "pytest" in blob.lower() or "pytest" in prep.display.lower()


def test_translate_chain_mkdir_and_true() -> None:
    r = translate_posix_to_host("mkdir -p a && true", host="cmd")
    assert "if not exist" in r.get("text")
    # Parens so `&& true` still runs when `a` already exists (cmd IF line-eat).
    assert r.get("text").strip().startswith("(")
    assert "&&" in r.get("text")
    assert r.get("text").index(")") < r.get("text").index("&&")
    assert "cd ." in r.get("text")


def test_translate_leaves_powershell_alone() -> None:
    src = "Get-ChildItem -Recurse | Where-Object { $_.Name -eq 'x' }"
    r = translate_posix_to_host(src, host="cmd")
    assert r.get("text") == src
    assert looks_like_powershell(src)


def test_translate_untranslatable_subshell() -> None:
    r = translate_posix_to_host("echo $(pwd)", host="cmd")
    assert r.get("untranslatable")


def test_start_server_and_posix_test_are_not_powershell() -> None:
    assert not looks_like_powershell("./start-server.sh")
    assert not looks_like_powershell("start-server.sh")
    assert not looks_like_powershell("start-dev")
    assert not looks_like_powershell('[ 1 -eq 1 ] && echo ok')
    assert not looks_like_powershell("test -eq 0")
    assert looks_like_powershell("Get-ChildItem -Recurse")
    assert looks_like_powershell("Start-Process notepad")


def test_untranslatable_prepare_does_not_exec() -> None:
    with pytest.raises(ValueError, match="untranslatable"):
        prepare_host_command("echo $(pwd)", host="cmd")


def test_chmod_is_host_noop() -> None:
    r = translate_posix_to_host("chmod +x run.sh", host="cmd")
    assert r.get("noop")
    prep = prepare_host_command("chmod +x run.sh", host="cmd")
    assert prep.kind == "noop"
    assert prep.argv == []


def test_chmod_dropped_from_chain() -> None:
    r = translate_posix_to_host("chmod +x a && mkdir -p b", host="cmd")
    assert not r.get("noop")
    assert "if not exist" in r.get("text")
    assert "chmod" not in r.get("text").lower()


def test_grep_falls_back_to_findstr(monkeypatch) -> None:
    import remedy.core.computer.host_binding as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: "")
    r = translate_posix_to_host("grep foo bar.py", host="cmd")
    assert r.get("changed")
    assert "findstr" in r.get("text")
    assert "foo" in r.get("text")


def test_find_rg_returns_path_string_not_tuple() -> None:
    import remedy.core.computer.host_binding as tr

    got = tr._find_rg()
    assert isinstance(got, str)
    assert "WindowsPath" not in got
    assert "bundled" not in got
    if got:
        assert Path(got).name.lower().startswith("rg")


def test_grep_no_files_is_stdin_not_star(monkeypatch) -> None:
    import remedy.core.computer.host_binding as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: "")
    r = translate_posix_to_host("grep foo", host="cmd")
    assert "findstr" in r.get("text")
    assert "*" not in r.get("text")
    assert "/s" not in r.get("text")
    monkeypatch.setattr(tr, "_find_rg", lambda: r"C:\tools\rg.exe")
    rg = translate_posix_to_host("grep foo", host="cmd")
    assert "*" not in rg.get("text")
    assert "WindowsPath" not in rg.get("text")
    assert "bundled" not in rg.get("text")


def test_piped_grep_does_not_embed_tuple_repr(monkeypatch) -> None:
    import remedy.core.computer.host_binding as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: r"C:\tools\rg.exe")
    r = translate_posix_to_host("dir | grep foo", host="cmd")
    assert "WindowsPath" not in r.get("text")
    assert "bundled" not in r.get("text")
    assert "foo" in r.get("text")


def test_powershell_word_in_args_does_not_skip_posix() -> None:
    src = "mkdir -p docs && echo use powershell"
    assert looks_like_powershell(src) is False
    r = translate_posix_to_host(src, host="cmd")
    assert "if not exist" in r.get("text")
    assert looks_like_powershell("where powershell") is False
    assert looks_like_powershell("pwsh -File x.ps1") is True
    assert looks_like_powershell("powershell -File x.ps1") is True
    assert looks_like_powershell("$_") is True
    assert looks_like_powershell("Get-ChildItem") is True


def test_service_cmdlets_are_powershell_not_filenames() -> None:
    assert looks_like_powershell("Get-Service") is True
    assert looks_like_powershell("Start-Service wuauserv") is True
    assert looks_like_powershell("start-server") is False
    assert looks_like_powershell("start-dev") is False


def test_translate_posix_host_noop() -> None:
    r = translate_posix_to_host("mkdir -p a", host="posix")
    assert r.get("text") == "mkdir -p a"
    assert not r.get("changed")


def test_zig_extracts_powershell_wrapper(tmp_path: Path) -> None:
    """Zig prepare owns extract/is_encoded; wrapper unwraps to -File."""
    prep = prepare_host_command(
        'pwsh -NoProfile -Command "Get-ChildItem -Name"',
        scratch_dir=tmp_path,
    )
    assert prep.kind == "script"
    assert prep.script_path is not None
    assert "Get-ChildItem" in prep.script_path.read_text(encoding="utf-8-sig")
    enc_dir = tmp_path / "enc"
    enc_dir.mkdir()
    encoded = prepare_host_command(
        "powershell -EncodedCommand QQ==",
        scratch_dir=enc_dir,
    )
    # EncodedCommand stays raw for the write jail (Zig encoded_ps_note).
    assert encoded.kind == "raw"
    assert encoded.script_path is None
    assert any("encoded" in n.lower() for n in encoded.notes)
    assert "EncodedCommand" in encoded.display


def test_prepare_powershell_uses_file_not_command(tmp_path: Path) -> None:
    prep = prepare_host_command(
        "Get-ChildItem -Name",
        scratch_dir=tmp_path,
    )
    assert prep.kind == "script"
    assert prep.script_path is not None
    assert prep.script_path.suffix == ".ps1"
    assert "-File" in prep.argv
    assert "-Command" not in prep.argv
    assert prep.script_path.is_file()


def test_prepare_pwsh_wrapper_unwraps(tmp_path: Path) -> None:
    prep = prepare_host_command(
        "powershell.exe -NoProfile -Command Get-Date",
        scratch_dir=tmp_path,
    )
    assert prep.kind == "script"
    assert "-File" in prep.argv
    text = prep.script_path.read_text(encoding="utf-8-sig")
    assert "Get-Date" in text


def test_prepare_plain_argv_no_shell() -> None:
    prep = prepare_host_command("python -m py_compile app.py", host="cmd")
    assert prep.kind == "argv"
    assert "python" in Path(prep.argv[0]).name.lower()
    assert "py_compile" in prep.argv
    # Builtins / chains still go through a shell (Zig prepare — no Python twin).
    echo = prepare_host_command("echo hello", host="cmd")
    assert echo.kind in {"translated", "raw", "script", "session"}
    chain = prepare_host_command("mkdir -p a && ls", host="cmd")
    assert chain.kind in {"translated", "raw", "script", "session", "argv"}


def test_prepare_translated_mkdir() -> None:
    prep = prepare_host_command("mkdir -p src/x", host="cmd")
    assert prep.kind in {"translated", "raw"}
    joined = " ".join(prep.argv).lower()
    assert "if not exist" in " ".join(prep.argv).lower() or "mkdir" in joined


def test_coerce_argv_json_and_string() -> None:
    assert coerce_argv(["git", "status"]) == ["git", "status"]
    assert coerce_argv('["git","status"]') == ["git", "status"]
    assert coerce_argv("git status") == ["git", "status"]
    assert coerce_argv(None) == []


def test_ir_roundtrip() -> None:
    op = run_op(["python", "-m", "pytest", "-q"], cwd=".")
    d = op.to_dict()
    back = HostOp.from_dict(d)
    assert back.kind == "run"
    assert back.argv[-1] == "-q"
    mk = mkdir_op(["src", "tests"])
    assert mk.kind == "mkdir"
    assert len(mk.paths) == 2


def test_prepare_host_op_script(tmp_path: Path) -> None:
    prep = prepare_host_op(
        script_op("pwsh", "Write-Output 'hi'"),
        scratch_dir=tmp_path,
    )
    assert prep.kind == "script"
    assert "-File" in prep.argv


def test_diagnose_mkdir_powershell() -> None:
    d = host_binding.diagnose_host_failure(
        command="mkdir -p a",
        stderr="mkdir: A positional parameter cannot be found that accepts argument '-p'.",
        translated='if not exist "a\\." mkdir "a"',
    )
    assert d.get("code") == "HOST_DIALECT"
    assert d.get("rewritten")


def test_diagnose_not_found_grep() -> None:
    d = host_binding.diagnose_host_failure(
        command="grep -n foo bar.py",
        stderr="'grep' is not recognized as an internal or external command",
    )
    assert d.get("code") == "HOST_NOT_FOUND"
    assert "POSIX" in str(d.get("hint") or "") or "grep" in str(d.get("message") or "")


def test_diagnose_timeout_interactive() -> None:
    d = host_binding.diagnose_host_failure(
        command="Read-Host pw",
        stdout="Password:",
        timed_out=True,
    )
    assert d.get("code") == "HOST_INTERACTIVE"


def test_dialect_rg_cmd_is_path_not_tuple(tmp_path: Path) -> None:
    home = tmp_path / "remedy-home"
    home.mkdir()
    # Zig prefers <home>/bin/rg over PATH.
    bin_dir = home / "bin"
    bin_dir.mkdir()
    fake = bin_dir / ("rg.exe" if os.name == "nt" else "rg")
    fake.write_bytes(b"")
    d = host_binding.dialect_probe(str(home), persist=True)
    rg = str(d.get("rg_cmd") or "")
    assert rg
    assert not rg.startswith("(")
    # Unix-style absolute paths stay POSIX (forward slashes).
    if rg.startswith("/"):
        assert "\\" not in rg


def test_dialect_heals_tuple_rg_cmd(tmp_path: Path) -> None:
    home = tmp_path / "remedy-home"
    (home / "host").mkdir(parents=True)
    (home / "host" / "dialect.json").write_text(
        '{"host":"posix","rg_cmd":"(PosixPath(\'/opt/rg\'), \'bundled\')"}',
        encoding="utf-8",
    )
    bin_dir = home / "bin"
    bin_dir.mkdir()
    fake = bin_dir / ("rg.exe" if os.name == "nt" else "rg")
    fake.write_bytes(b"")
    loaded = host_binding.dialect_load(str(home))
    rg = str(loaded.get("rg_cmd") or "")
    assert rg
    assert not rg.startswith("(")
    assert "rg" in Path(rg).name.lower()


def test_dialect_persist_and_success(tmp_path: Path) -> None:
    home = tmp_path / "remedy-home"
    home.mkdir()
    d = host_binding.dialect_probe(str(home), persist=True)
    assert d.get("python_cmd")
    path = home / "host" / "dialect.json"
    assert path.is_file()
    loaded = host_binding.dialect_load(str(home))
    assert loaded.get("python_cmd") == d.get("python_cmd")
    rec = host_binding.dialect_record_success(
        "python -m pytest -q", home=str(home), note="argv"
    )
    assert int(rec.get("successes") or 0) >= 1
    assert str(rec.get("last_good_verify") or "").startswith("python")
    line = host_binding.dialect_format_line(str(home), rec)
    assert "Host bridge" in line
    assert "host_run" in line


def test_resolve_which_python() -> None:
    found = resolve_which("python")
    assert found
    name = Path(found).name.lower()
    assert name.startswith("python") or name.startswith("py") or "python" in found.lower()
    assert "remedy" not in name


def test_probe_dialect_never_stamps_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Zig reads PATH from the process env — empty PATH + no REMEDY_PYTHON.
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    monkeypatch.delenv("REMEDY_PYTHON", raising=False)
    monkeypatch.delenv("PATHEXT", raising=False)
    d = host_binding.dialect_probe(str(tmp_path / "home"), persist=False)
    assert d.get("python_cmd") == ""
    assert "remedy" not in str(d.get("python_cmd") or "").lower()


def test_load_dialect_heals_sidecar_python_cmd(tmp_path: Path) -> None:
    import json

    home = tmp_path / "remedy-home"
    (home / "host").mkdir(parents=True)
    sidecar = tmp_path / "remedy-desktop.exe"
    sidecar.write_bytes(b"")
    (home / "host" / "dialect.json").write_text(
        json.dumps({"host": "cmd", "python_cmd": str(sidecar)}),
        encoding="utf-8",
    )
    loaded = host_binding.dialect_load(str(home))
    py = str(loaded.get("python_cmd") or "")
    assert py
    assert "remedy-desktop" not in Path(py).name.lower()
    assert Path(py).name.lower().startswith("python") or Path(py).name.lower() in {
        "py",
        "py.exe",
        "python.exe",
        "python3",
        "python3.exe",
    }


def test_resolve_which_python_skips_sidecar_dialect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sidecar = tmp_path / "remedy-desktop.exe"
    sidecar.write_bytes(b"")
    monkeypatch.setattr(
        "remedy.core.computer.host_binding.dialect_load",
        lambda home="": {"python_cmd": str(sidecar)},
    )
    found = resolve_which("python")
    assert found
    assert "remedy-desktop" not in Path(found).name.lower()
    assert Path(found).name.lower().startswith("python") or "python" in found.lower()


def test_python_exe_rewrite_skips_sidecar_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from remedy.core.computer import host_binding as translate

    sidecar = tmp_path / "remedy-desktop.exe"
    sidecar.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(sidecar))
    monkeypatch.setattr(
        "remedy.core.build_python.host_python_executable", lambda: ""
    )
    exe = translate._python_exe()
    assert "remedy" not in Path(exe).name.lower()
    h = translate_posix_to_host("head -n 2 README.md", host="cmd")
    assert "remedy-desktop" not in h.get("text").lower()


def test_line_rewrites_fall_back_to_pwsh_without_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CPython at all: head/tail/wc still work via PowerShell."""
    from remedy.core.computer import host_binding as translate

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "remedy.core.build_python.host_python_executable", lambda: ""
    )
    pw = tmp_path / "pwsh.exe"
    pw.write_bytes(b"")
    monkeypatch.setattr(translate, "_pwsh_exe", lambda: str(pw))

    for cmd, marker in (
        ("head -n 2 file.txt", "-TotalCount 2"),
        ("tail -n 5 file.txt", "-Tail 5"),
        ("wc -l file.txt", "Measure-Object -Line"),
    ):
        h = translate_posix_to_host(cmd, host="cmd")
        assert marker in h.get("text"), (cmd, h.get("text"))
        assert "Get-Content" in h.get("text")
        assert "python" not in h.get("text").lower()

    data = translate.rewrite_posix_argv(["wc", "-l", "file.txt"])
    argv = list(data.get("argv") or [])
    notes = list(data.get("notes") or [])
    assert argv[0] == str(pw)
    assert any("pwsh" in n for n in notes)


def test_line_rewrites_skip_with_hint_without_python_or_pwsh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither CPython nor pwsh: leave the command, surface REMEDY_PYTHON."""
    from remedy.core.computer import host_binding as translate

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "remedy.core.build_python.host_python_executable", lambda: ""
    )
    monkeypatch.setattr(translate, "_pwsh_exe", lambda: "")
    h = translate_posix_to_host("head -n 2 file.txt", host="cmd")
    assert "head" in h.get("text")
    assert "9009" not in h.get("text")
    assert any("REMEDY_PYTHON" in n for n in h.get("notes"))

    data = translate.rewrite_posix_argv(["wc", "-l", "file.txt"])
    argv = list(data.get("argv") or [])
    notes = list(data.get("notes") or [])
    assert argv == ["wc", "-l", "file.txt"]
    assert any("REMEDY_PYTHON" in n for n in notes)


def test_resolve_which_finds_venv_pytest(tmp_path: Path) -> None:
    import os

    scripts = tmp_path / (".venv/Scripts" if os.name == "nt" else ".venv/bin")
    scripts.mkdir(parents=True)
    name = "pytest.exe" if os.name == "nt" else "pytest"
    stub = scripts / name
    stub.write_text("", encoding="utf-8")
    if os.name != "nt":
        stub.chmod(0o755)
    found = resolve_which("pytest", cwd=tmp_path)
    assert found
    assert Path(found).resolve() == stub.resolve()


def test_win_shell_prefix_and_runtime_agree() -> None:
    prefix = win_shell_prefix()
    rt = ToolRuntime()
    argv = rt._shell_command("echo host-bridge-ok")
    if sys.platform == "win32":
        assert prefix[0].lower().endswith("cmd") or "cmd.exe" in prefix[0].lower()
        # Must NOT be pwsh -Command
        joined = " ".join(argv).lower()
        assert "-command" not in joined
    else:
        assert prefix[-1] == "-c"
        assert argv[0] == prefix[0]


def test_script_body_size_cap(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exceeds"):
        launch_script("python", "x" * 1_000_001, scratch_dir=tmp_path)


def test_launch_script_python(tmp_path: Path) -> None:
    from remedy.core.build_python import python_cmd_for_subprocess

    launch = launch_script("python", "print('ok')", scratch_dir=tmp_path)
    assert launch.path.suffix == ".py"
    py = python_cmd_for_subprocess()
    assert py
    assert launch.argv[: len(py)] == py
    stem = Path(py[0]).name.lower()
    assert "remedy-desktop" not in stem
    assert launch.path.read_text(encoding="utf-8").startswith("print")


def test_conpty_available_does_not_raise() -> None:
    flag = conpty_available()
    assert flag in (True, False)
    if sys.platform != "win32":
        assert flag is False


@pytest.mark.asyncio
async def test_host_session_echo() -> None:
    from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError
    from remedy.core.computer.host_binding import HostSession

    if os.name != "nt":
        sess = HostSession(host="posix")
        with pytest.raises(HostError) as raised:
            await sess.start()
        assert raised.value.status == STATUS_UNSUPPORTED
        return

    sess = HostSession(host="cmd")
    try:
        await sess.start()
        result = await sess.run("echo host-session-ok", timeout=20)
        assert not result.timed_out
        assert "host-session-ok" in (result.stdout or "")
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_host_session_cd_persists(tmp_path: Path) -> None:
    from remedy.core.computer.host_binding import HostSession

    if os.name != "nt":
        pytest.skip("cmd session cwd check is Windows-oriented")
    sess = HostSession(host="cmd", cwd=str(tmp_path))
    try:
        await sess.start()
        sub = tmp_path / "nested"
        sub.mkdir()
        result = await sess.run(f"cd {sub.name}", timeout=15)
        assert result.exit_code == 0 or result.cwd
        here = await sess.current_cwd()
        assert "nested" in here.replace("/", "\\") or str(sub) in here
    finally:
        await sess.close()


def test_host_op_from_bad_dict() -> None:
    op = HostOp.from_dict({"kind": "nope", "argv": [1, 2]})
    assert op.kind == "raw"
    empty = HostOp.from_dict(None)
    assert empty.kind == "raw"


def test_join_and_normalize_wrappers() -> None:
    from remedy.core.workspace_tools.shell import (
        _join_argv_for_jail,
        _normalize_shell_command_for_host,
    )

    joined = _join_argv_for_jail(["python", "my file.py"])
    assert '"my file.py"' in joined
    # On Windows this rewrites; on POSIX it returns the original
    out = _normalize_shell_command_for_host("mkdir -p a")
    assert "mkdir" in out or "if not exist" in out


def test_runtime_host_run_mapping() -> None:
    from remedy.models import ToolCall

    rt = ToolRuntime()
    tc = ToolCall(
        tool_name="host_run",
        arguments={"argv": ["git", "status"]},
    )
    argv = rt._build_command(tc)
    assert argv == ["git", "status"]


@pytest.mark.asyncio
async def test_shared_session_scoped_by_id_and_start_cwd(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Zig HostSession live open is Windows-only")
    from remedy.core.computer.host_binding import (
        close_all_shared_sessions,
        close_shared_session,
        get_shared_session,
    )

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    try:
        s1 = await get_shared_session(cwd=str(a), session_id="chat-1")
        s2 = await get_shared_session(cwd=str(a), session_id="chat-1")
        assert s1 is s2
        s3 = await get_shared_session(cwd=str(a), session_id="chat-2")
        assert s3 is not s1
        s4 = await get_shared_session(cwd=str(b), session_id="chat-1")
        assert s4 is not s1
        g = await get_shared_session(cwd=str(a), session_id=None)
        await close_shared_session(None)
        assert not g._alive()
        s3b = await get_shared_session(cwd=str(a), session_id="chat-2")
        assert s3b is s3
        assert s3._alive()
    finally:
        await close_all_shared_sessions()


@pytest.mark.asyncio
async def test_abort_session_closes_shared_host_shell() -> None:
    if os.name != "nt":
        pytest.skip("Zig HostSession live open is Windows-only")
    from remedy.core.turn_context import abort_session, begin_turn, end_turn
    from remedy.core.computer.host_binding import (
        close_all_shared_sessions,
        get_shared_session,
    )

    toks = begin_turn("host-abort", project_raw=None, active_path=".")
    try:
        sess = await get_shared_session(session_id="host-abort")
        assert sess._alive()
        abort_session("host-abort")
        await asyncio.sleep(0.05)
        assert not sess._alive()
    finally:
        end_turn("host-abort", *toks)
        await close_all_shared_sessions()


@pytest.mark.asyncio
async def test_current_cwd_empty_when_closed() -> None:
    if os.name != "nt":
        pytest.skip("Zig HostSession live open is Windows-only")
    from remedy.core.computer.host_binding import HostSession

    sess = HostSession(host="cmd", cwd=".")
    assert await sess.current_cwd() == ""
    try:
        await sess.start()
        here = await sess.current_cwd()
        assert here
    finally:
        await sess.close()
    assert await sess.current_cwd() == ""


def test_head_tail_find_test_f_rewrite() -> None:
    h = translate_posix_to_host("head -n 5 README.md", host="cmd")
    assert h.get("changed")
    assert "python" in h.get("text").lower() or "-c" in h.get("text")
    t = translate_posix_to_host("tail -n 3 log.txt", host="cmd")
    assert t.get("changed")
    f = translate_posix_to_host("find . -name *.py", host="cmd")
    assert "dir /s /b" in f.get("text")
    tf = translate_posix_to_host("test -f app.py", host="cmd")
    assert "if exist" in tf.get("text")
    br = translate_posix_to_host("[ -f app.py ]", host="cmd")
    assert "if exist" in br.get("text")
    wc = translate_posix_to_host("wc -l src/data/curriculum.ts", host="cmd")
    assert wc.get("changed")
    assert "python" in wc.get("text").lower() or "-c" in wc.get("text")
    assert "len(p)" in wc.get("text")


def test_host_run_argv_rewrites_wc_dash_l() -> None:
    from remedy.core.computer import host_binding

    data = host_binding.rewrite_posix_argv(["wc", "-l", "src/data/curriculum.ts"])
    out = list(data.get("argv") or [])
    notes = list(data.get("notes") or [])
    assert notes
    assert out[0] != "wc"
    assert "-c" in out
    assert any("len(p)" in a for a in out)


def test_diagnose_not_found_wc() -> None:
    d = host_binding.diagnose_host_failure(
        command="wc -l curriculum.ts",
        stderr="'wc' is not recognized as an internal or external command",
    )
    assert d.get("code") == "HOST_NOT_FOUND"
    hint = str(d.get("hint") or "")
    assert "POSIX" in hint or "wc" in hint.lower()


def test_cleanup_host_script(tmp_path: Path) -> None:
    from remedy.core.computer.host_binding import cleanup_host_script

    p = tmp_path / "host_abc123.py"
    p.write_text("print(1)\n", encoding="utf-8")
    assert p.is_file()
    cleanup_host_script(p)
    assert not p.is_file()
    other = tmp_path / "keep_me.py"
    other.write_text("x", encoding="utf-8")
    cleanup_host_script(other)
    assert other.is_file()


def test_default_script_lang_posix() -> None:
    from remedy.core.computer.host_binding import default_script_lang

    if os.name != "nt":
        assert default_script_lang() == "python"
    else:
        assert default_script_lang() in {"pwsh", "cmd"}
