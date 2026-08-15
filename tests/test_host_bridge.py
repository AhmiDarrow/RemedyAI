"""Host Bridge — translator, IR, script-file, dialect, runner, session."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from remedy.execution.host.diagnose import diagnose_host_failure
from remedy.execution.host.dialect import (
    HostDialect,
    format_dialect_line,
    load_dialect,
    probe_host_dialect,
    record_success,
    save_dialect,
)
from remedy.execution.host.ir import HostOp, mkdir_op, run_op, script_op
from remedy.execution.host.runner import (
    coerce_argv,
    looks_like_plain_argv,
    prepare_host_command,
    prepare_host_op,
    resolve_which,
)
from remedy.execution.host.scriptfile import (
    extract_powershell_payload,
    is_encoded_powershell,
    launch_script,
)
from remedy.execution.host.session import _cwd_command, conpty_available
from remedy.execution.host.translate import looks_like_powershell, translate_posix_to_host
from remedy.execution.process import win_shell_prefix
from remedy.execution.runtime import ToolRuntime


def test_translate_mkdir_p() -> None:
    r = translate_posix_to_host("mkdir -p src/foo tests", host="cmd")
    assert r.changed
    assert "if not exist" in r.text
    assert "src\\foo" in r.text or "src/foo" in r.text.replace("\\", "/")


def test_translate_rm_rf() -> None:
    r = translate_posix_to_host("rm -rf build", host="cmd")
    assert r.changed
    assert "rmdir" in r.text
    assert "build" in r.text


def test_translate_export_and_dev_null() -> None:
    r = translate_posix_to_host("export FOO=bar && echo hi >/dev/null", host="cmd")
    assert "set FOO=bar" in r.text
    assert "NUL" in r.text
    assert "/dev/null" not in r.text


def test_translate_q_escapes_quote_amp() -> None:
    from remedy.execution.host.translate import _q, translate_posix_to_host

    assert '&' not in _q('foo"&calc') or '""' in _q('foo"&calc')
    assert _q('foo"&calc') == '"foo""&calc"'
    text = translate_posix_to_host('cat foo"&calc', host="cmd").text
    assert "type" in text
    assert '"foo""&calc"' in text


def test_translate_ls_cat_pwd_which() -> None:
    assert translate_posix_to_host("ls", host="cmd").text == "dir"
    assert "type" in translate_posix_to_host("cat README.md", host="cmd").text
    assert translate_posix_to_host("pwd", host="cmd").text == "cd"
    assert translate_posix_to_host("which git", host="cmd").text.startswith("where")


def test_translate_chain_mkdir_and_true() -> None:
    r = translate_posix_to_host("mkdir -p a && true", host="cmd")
    assert "if not exist" in r.text
    # Parens so `&& true` still runs when `a` already exists (cmd IF line-eat).
    assert r.text.strip().startswith("(")
    assert "&&" in r.text
    assert r.text.index(")") < r.text.index("&&")
    assert "cd ." in r.text


def test_translate_leaves_powershell_alone() -> None:
    src = "Get-ChildItem -Recurse | Where-Object { $_.Name -eq 'x' }"
    r = translate_posix_to_host(src, host="cmd")
    assert r.text == src
    assert looks_like_powershell(src)


def test_translate_untranslatable_subshell() -> None:
    r = translate_posix_to_host("echo $(pwd)", host="cmd")
    assert r.untranslatable


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
    assert r.noop
    prep = prepare_host_command("chmod +x run.sh", host="cmd")
    assert prep.kind == "noop"
    assert prep.argv == []


def test_chmod_dropped_from_chain() -> None:
    r = translate_posix_to_host("chmod +x a && mkdir -p b", host="cmd")
    assert not r.noop
    assert "if not exist" in r.text
    assert "chmod" not in r.text.lower()


def test_grep_falls_back_to_findstr(monkeypatch) -> None:
    import remedy.execution.host.translate as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: "")
    r = translate_posix_to_host("grep foo bar.py", host="cmd")
    assert r.changed
    assert "findstr" in r.text
    assert "foo" in r.text


def test_find_rg_returns_path_string_not_tuple() -> None:
    import remedy.execution.host.translate as tr

    got = tr._find_rg()
    assert isinstance(got, str)
    assert "WindowsPath" not in got
    assert "bundled" not in got
    if got:
        assert Path(got).name.lower().startswith("rg")


def test_grep_no_files_is_stdin_not_star(monkeypatch) -> None:
    import remedy.execution.host.translate as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: "")
    r = translate_posix_to_host("grep foo", host="cmd")
    assert "findstr" in r.text
    assert "*" not in r.text
    assert "/s" not in r.text
    monkeypatch.setattr(tr, "_find_rg", lambda: r"C:\tools\rg.exe")
    rg = translate_posix_to_host("grep foo", host="cmd")
    assert "*" not in rg.text
    assert "WindowsPath" not in rg.text
    assert "bundled" not in rg.text


def test_piped_grep_does_not_embed_tuple_repr(monkeypatch) -> None:
    import remedy.execution.host.translate as tr

    monkeypatch.setattr(tr, "_find_rg", lambda: r"C:\tools\rg.exe")
    r = translate_posix_to_host("dir | grep foo", host="cmd")
    assert "WindowsPath" not in r.text
    assert "bundled" not in r.text
    assert "foo" in r.text


def test_powershell_word_in_args_does_not_skip_posix() -> None:
    src = "mkdir -p docs && echo use powershell"
    assert looks_like_powershell(src) is False
    r = translate_posix_to_host(src, host="cmd")
    assert "if not exist" in r.text
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
    assert r.text == "mkdir -p a"
    assert not r.changed


def test_extract_powershell_command_wrapper() -> None:
    body = extract_powershell_payload(
        "pwsh -NoProfile -Command \"Get-ChildItem -Name\""
    )
    assert body is not None
    assert "Get-ChildItem" in body
    assert is_encoded_powershell("powershell -EncodedCommand QQ==")
    assert extract_powershell_payload("powershell -EncodedCommand QQ==") is None


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
    assert looks_like_plain_argv("python -m py_compile app.py")
    assert not looks_like_plain_argv("echo hello")
    assert not looks_like_plain_argv("mkdir -p a && ls")


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
    d = diagnose_host_failure(
        "mkdir -p a",
        stderr="mkdir: A positional parameter cannot be found that accepts argument '-p'.",
        translated='if not exist "a\\" mkdir "a"',
    )
    assert d.code == "HOST_DIALECT"
    assert d.rewritten


def test_diagnose_not_found_grep() -> None:
    d = diagnose_host_failure(
        "grep -n foo bar.py",
        stderr="'grep' is not recognized as an internal or external command",
    )
    assert d.code == "HOST_NOT_FOUND"
    assert "POSIX" in d.hint or "grep" in d.message


def test_diagnose_timeout_interactive() -> None:
    d = diagnose_host_failure(
        "Read-Host pw",
        stdout="Password:",
        timed_out=True,
    )
    assert d.code == "HOST_INTERACTIVE"


def test_dialect_persist_and_success(tmp_path: Path) -> None:
    home = tmp_path / "remedy-home"
    home.mkdir()
    d = probe_host_dialect(home=home, persist=True)
    assert d.python_cmd
    path = save_dialect(d, home)
    assert path.is_file()
    loaded = load_dialect(home)
    assert loaded.python_cmd == d.python_cmd
    rec = record_success("python -m pytest -q", home=home, note="argv")
    assert rec.successes >= 1
    assert rec.last_good_verify.startswith("python")
    line = format_dialect_line(rec, home=home)
    assert "Host bridge" in line
    assert "host_run" in line


def test_resolve_which_python() -> None:
    found = resolve_which("python")
    assert found
    assert Path(found).name.lower().startswith("python") or "python" in found.lower()


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
    from remedy.execution.host.scriptfile import write_script

    with pytest.raises(ValueError, match="exceeds"):
        write_script("python", "x" * 1_000_001, tmp_path / "too_big.py")


def test_launch_script_python(tmp_path: Path) -> None:
    launch = launch_script("python", "print('ok')", scratch_dir=tmp_path)
    assert launch.path.suffix == ".py"
    assert launch.argv[0] == sys.executable
    assert launch.path.read_text(encoding="utf-8").startswith("print")


def test_conpty_available_does_not_raise() -> None:
    flag = conpty_available()
    assert flag in (True, False)
    if sys.platform != "win32":
        assert flag is False


@pytest.mark.asyncio
async def test_host_session_echo() -> None:
    from remedy.execution.host.session import HostSession

    host = "cmd" if os.name == "nt" else "posix"
    sess = HostSession(host=host)
    try:
        await sess.start()
        cmd = "echo host-session-ok" if host == "cmd" else "echo host-session-ok"
        result = await sess.run(cmd, timeout=20)
        assert not result.timed_out
        assert "host-session-ok" in (result.stdout or "")
    finally:
        await sess.close()


@pytest.mark.asyncio
async def test_read_until_poll_timeout_does_not_end_command() -> None:
    """0.4s wait_for slices must not mark a still-running command timed out."""
    from remedy.execution.host.session import HostSession

    class _Stdout:
        def __init__(self) -> None:
            self.t0 = asyncio.get_running_loop().time()

        async def read(self, _n: int) -> bytes:
            while asyncio.get_running_loop().time() - self.t0 < 0.7:
                await asyncio.sleep(0.05)
            return b"MARK:0\n"

    class _Proc:
        stdout = None
        returncode = None

        def __init__(self) -> None:
            self.stdout = _Stdout()

    sess = HostSession(host="posix")
    sess._proc = _Proc()
    raw, timed_out, _ = await sess._read_until(b"MARK", timeout=3.0)
    assert timed_out is False
    assert b"MARK" in raw


@pytest.mark.asyncio
async def test_read_until_real_deadline_still_times_out() -> None:
    from remedy.execution.host.session import HostSession

    cancelled = {"n": 0}

    class _Stdout:
        async def read(self, _n: int) -> bytes:
            try:
                await asyncio.sleep(10)
                return b"MARK:0\n"
            except asyncio.CancelledError:
                cancelled["n"] += 1
                raise

    class _Proc:
        returncode = None

        def __init__(self) -> None:
            self.stdout = _Stdout()

    sess = HostSession(host="posix")
    sess._proc = _Proc()
    _raw, timed_out, _ = await sess._read_until(b"MARK", timeout=0.7)
    assert timed_out is True
    # ConPTY ReadFile cannot be cancelled; one outstanding read must survive
    # the poll timeout (wait_for-cancel used to leak the overlapped I/O).
    assert cancelled["n"] == 0
    assert sess._stdout_pending is not None
    assert not sess._stdout_pending.done()
    sess._stdout_pending.cancel()


@pytest.mark.asyncio
async def test_host_session_cd_persists(tmp_path: Path) -> None:
    from remedy.execution.host.session import HostSession

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


def test_dialect_from_dict_tolerates_junk() -> None:
    d = HostDialect.from_dict({"notes": "nope", "successes": "3"})
    assert d.successes == 3
    assert d.notes == []


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
    from remedy.execution.host.session import (
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
    from remedy.core.turn_context import abort_session, begin_turn, end_turn
    from remedy.execution.host.session import (
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


def test_cwd_command_matches_host_dialect() -> None:
    assert _cwd_command("cmd") == "cd"
    assert _cwd_command("pwsh") == "(Get-Location).Path"
    assert _cwd_command("posix") == "pwd"
    assert _cwd_command("bash") == "pwd"


@pytest.mark.asyncio
async def test_current_cwd_empty_when_closed() -> None:
    from remedy.execution.host.session import HostSession

    host = "cmd" if os.name == "nt" else "posix"
    sess = HostSession(host=host, cwd=".")
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
    assert h.changed
    assert "python" in h.text.lower() or "-c" in h.text
    t = translate_posix_to_host("tail -n 3 log.txt", host="cmd")
    assert t.changed
    f = translate_posix_to_host("find . -name *.py", host="cmd")
    assert "dir /s /b" in f.text
    tf = translate_posix_to_host("test -f app.py", host="cmd")
    assert "if exist" in tf.text
    br = translate_posix_to_host("[ -f app.py ]", host="cmd")
    assert "if exist" in br.text


def test_cleanup_host_script(tmp_path: Path) -> None:
    from remedy.execution.host.scriptfile import cleanup_host_script, write_script

    p = tmp_path / "host_abc123.py"
    write_script("python", "print(1)", p)
    assert p.is_file()
    cleanup_host_script(p)
    assert not p.is_file()
    other = tmp_path / "keep_me.py"
    other.write_text("x", encoding="utf-8")
    cleanup_host_script(other)
    assert other.is_file()


def test_default_script_lang_posix() -> None:
    from remedy.execution.host.runner import default_script_lang

    if os.name != "nt":
        assert default_script_lang() == "python"
    else:
        assert default_script_lang() in {"pwsh", "cmd"}
