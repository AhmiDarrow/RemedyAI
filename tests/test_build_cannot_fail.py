"""Build must finish pages: stay armed, skip Ask, no fake done, jail allows copy-in."""

from __future__ import annotations

from pathlib import Path

from remedy.core.approvals import ApprovalQueue
from remedy.core.build_engine import (
    BuildTurnState,
    build_blocks_final_answer,
    enable_build_host_drive,
    enable_work_host_drive,
    looks_like_build_request,
    next_machine_nudge,
    observe_tool_batch,
)
from remedy.core.build_ledger import BuildLedgerEntry, save_ledger
from remedy.core.build_oracle import should_auto_verify
from remedy.core.build_scoped import scoped_verify_command
from remedy.core.build_todos import sync_todos_with_build, upsert_todos
from remedy.core.react_policy import build_keeps_tools_armed
from remedy.core.react_turn import resolve_tools
from remedy.core.shell_write_jail import (
    check_shell_write_jail,
    is_runtime_executable_path,
    scan_script_source_for_outside_writes,
)
from remedy.core.turn_context import begin_turn, end_turn, set_turn_skip_ask, turn_skip_ask


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {}}}


def _write_call(path: str, cid: str = "w1") -> dict:
    return {
        "id": cid,
        "function": {
            "name": "file_write",
            "arguments": f'{{"path": "{path}", "content": ""}}',
        },
    }


def test_landing_page_goal_is_a_build() -> None:
    assert looks_like_build_request(
        "Create a beautiful marketing landing page for Remedy reachable from the homepage"
    )


def test_empty_write_does_not_count_as_success_or_done() -> None:
    st = BuildTurnState(
        active=True,
        phase="implement",
        goal="Create remedy.html marketing landing + wiki",
        project_path="",
        required_files=["remedy.html"],
    )
    observe_tool_batch(
        st,
        [_write_call("remedy.html", "c1")],
        [
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": (
                    "Error [EMPTY_SOURCE_WRITE:file_write]: refusing empty "
                    "file_write to source path remedy.html. Content was blank."
                ),
            }
        ],
    )
    assert "remedy.html" in st.empty_write_paths
    assert st.write_steps == 0
    assert "remedy.html" not in (st.write_set or [])
    assert st.missing_required_files()
    assert build_blocks_final_answer(st) is True
    st.last_verify_ok = True
    st.require_green_to_finish = True
    st.write_steps = 1  # other files written
    assert build_blocks_final_answer(st) is True
    nudge = next_machine_nudge(st)
    assert nudge is not None
    assert "remedy.html" in nudge["content"]


def test_green_verify_without_named_page_is_not_done(tmp_path: Path) -> None:
    st = BuildTurnState(
        active=True,
        phase="verify",
        goal="Create remedy.html marketing landing page",
        project_path=str(tmp_path),
        last_verify_ok=True,
        write_steps=2,
        write_set=["css/remedy.css"],
    )
    st.clear_write_set_on_green()
    assert st.phase != "done"
    assert "remedy.html" in st.missing_required_files()
    (tmp_path / "remedy.html").write_text("<html>ok</html>\n", encoding="utf-8")
    st.write_set = ["css/remedy.css", "remedy.html"]
    st.last_verify_ok = True
    st.clear_write_set_on_green()
    assert st.phase == "done"


def test_frustrated_why_keeps_build_armed() -> None:
    assert build_keeps_tools_armed("why is everything failing?", build_active=True)
    all_t = [_tool("file_write"), _tool("bash_exec")]
    d = resolve_tools(
        message="why is everything failing?",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
    )
    assert d.tools is not None
    assert d.reason == "build_active"


def test_build_drives_host_skips_ask(monkeypatch) -> None:
    """Active Build must not pause for permission (jail still on)."""
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "approval_mode": "ask"},
    )
    toks = begin_turn("build-drive", project_raw=None, active_path=".")
    try:
        st = BuildTurnState(active=True, phase="implement", goal="Create remedy.html")
        enable_build_host_drive(runtime=None, state=st)
        assert turn_skip_ask() is True
        assert q.needs_ask("write remedy.html", tool_name="file_write") is None
        assert q.needs_ask("npm test", tool_name="bash_exec") is None
        set_turn_skip_ask(False)
        assert q.needs_ask("write remedy.html", tool_name="file_write") is not None
    finally:
        end_turn("build-drive", *toks)
    assert turn_skip_ask() is False
    # Host-drive skip is turn-local — Settings Ask still applies next turn.
    q2 = ApprovalQueue()
    q2.set_mode("ask")
    assert q2.needs_ask("write remedy.html", tool_name="file_write") is not None


def test_work_turn_drives_the_pc(monkeypatch) -> None:
    """Any work turn drives this PC — Ask is not a roadblock. Jail stays on."""
    q = ApprovalQueue()
    q.set_mode("ask")
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "approval_mode": "ask"},
    )
    toks = begin_turn("work-drive", project_raw=None, active_path=".")
    try:
        enable_work_host_drive(
            runtime=None,
            message="Create index.html for the landing page",
            plan_mode=False,
        )
        assert turn_skip_ask() is True
        assert q.needs_ask("write index.html", tool_name="file_write") is None
        assert q.needs_ask("dir", tool_name="bash_exec") is None
        set_turn_skip_ask(False)
        enable_work_host_drive(runtime=None, message="thanks", plan_mode=False)
        assert turn_skip_ask() is False
        enable_work_host_drive(
            runtime=None,
            message="Create index.html",
            plan_mode=True,
        )
        assert turn_skip_ask() is False
    finally:
        end_turn("work-drive", *toks)


def test_copy_over_cmd_exe_still_jails() -> None:
    """Sidecar leftover skip must not waive overwrite of a real runtime dest."""
    roots = [Path(r"C:\Users\Administrator\AhmiDarrow-Website")]
    hit = check_shell_write_jail(
        r"copy payload.exe C:\Windows\System32\cmd.exe",
        write_roots=roots,
        cwd=roots[0],
        project_bound=True,
    )
    assert hit is not None
    assert "cmd.exe" in (hit or "").lower() or "outside" in (hit or "").lower()


def test_sidecar_exe_is_runtime_not_write_dest() -> None:
    assert is_runtime_executable_path(
        r"C:\Users\Administrator\AppData\Local\Remedy Desktop\remedy-desktop.exe"
    )
    roots = [Path(r"C:\Users\Administrator\AhmiDarrow-Website")]
    # Unquoted join of sidecar + in-project script must not jail the exe.
    cmd = (
        r"C:\Users\Administrator\AppData\Local\Remedy Desktop\remedy-desktop.exe "
        r"C:\Users\Administrator\AhmiDarrow-Website\.remedy-build\tmp\copy.py"
    )
    hit = check_shell_write_jail(
        cmd,
        write_roots=roots,
        cwd=roots[0],
        project_bound=True,
    )
    assert hit is None or "remedy-desktop.exe" not in (hit or "")


def test_copy_in_script_may_read_sibling_assets(tmp_path: Path) -> None:
    proj = tmp_path / "site"
    proj.mkdir()
    src = tmp_path / "Old-Remedy" / "assets"
    src.mkdir(parents=True)
    (src / "hero.png").write_bytes(b"x")
    helper = proj / "copy_in.py"
    helper.write_text(
        "from pathlib import Path\n"
        "import shutil\n"
        "ROOT = Path(__file__).resolve().parent\n"
        "DST = ROOT / 'assets'\n"
        f"SRC = Path(r'{src}')\n"
        "DST.mkdir(exist_ok=True)\n"
        "shutil.copy2(SRC / 'hero.png', DST / 'hero.png')\n",
        encoding="utf-8",
    )
    assert (
        scan_script_source_for_outside_writes(helper, write_roots=[proj.resolve()])
        is None
    )
    evil = proj / "pwn.py"
    evil.write_text(
        "open(r'C:\\\\Users\\\\Public\\\\pwn.txt','w').write('x')\n",
        encoding="utf-8",
    )
    assert (
        scan_script_source_for_outside_writes(evil, write_roots=[proj.resolve()])
        is not None
    )


def test_ledger_write_does_not_count_as_product_write() -> None:
    st = BuildTurnState(active=True, phase="implement", goal="Create page.html")
    observe_tool_batch(
        st,
        [
            {
                "id": "l1",
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":".remedy-build/ledger.json","content":"{}"}',
                },
            }
        ],
        [{"role": "tool", "tool_call_id": "l1", "content": "wrote ledger"}],
    )
    assert st.write_steps == 0
    assert not any("ledger.json" in str(w) for w in (st.write_set or []))


def test_scoped_verify_never_uses_last_failed(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / ".pytest_cache").mkdir()
    (tmp_path / "index.html").write_text("<html>ok</html>\n", encoding="utf-8")
    from types import SimpleNamespace

    rt = SimpleNamespace(effective_project_path=lambda: tmp_path)
    cmd = scoped_verify_command(
        rt, [str(tmp_path / "index.html")], base_command="pytest -q"
    )
    assert cmd == "" or "--lf" not in cmd


def test_timeout_does_not_rearm_auto_verify() -> None:
    st = BuildTurnState(
        active=True,
        phase="repair",
        goal="Create index.html",
        write_steps=2,
        write_set=["index.html"],
        last_verify_ok=False,
        last_verify_summary=(
            "verify exit_code=-1\ntimeout_s=300.0\ncommand=pytest -q --lf\n"
            "stderr:\nCommand timed out after 300.0s"
        ),
        auto_verify_ran=True,
        write_steps_at_last_green=2,
    )
    assert should_auto_verify(st) is False


def test_stale_todos_close_when_files_exist(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>hello world</html>\n", encoding="utf-8")
    from types import SimpleNamespace

    rt = SimpleNamespace(
        effective_project_path=lambda: tmp_path,
        config=SimpleNamespace(home_dir=tmp_path),
        _build_turn=None,
    )
    upsert_todos(
        rt,
        [
            {"id": "1", "content": "Scout project folder", "status": "in_progress"},
            {"id": "2", "content": "Write index.html", "status": "pending"},
            {"id": "3", "content": "Verify green", "status": "pending"},
        ],
        merge=False,
    )
    st = BuildTurnState(
        active=True,
        phase="verify",
        goal="Create index.html landing page",
        project_path=str(tmp_path),
        write_steps=2,
        write_set=["index.html"],
        last_verify_ok=True,
        required_files=["index.html"],
    )
    sync_todos_with_build(rt, st)
    assert st.open_todo_count == 0
    assert build_blocks_final_answer(st) is False


def test_timeout_ledger_does_not_resume_last_failed(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from remedy.core.build_engine import begin_build_turn

    proj = tmp_path / "site"
    proj.mkdir()
    (proj / "index.html").write_text("<html>ok enough</html>\n", encoding="utf-8")
    save_ledger(
        BuildLedgerEntry(
            goal="Create index.html",
            phase="repair",
            project_path=str(proj),
            verify_command="pytest -q",
            last_verify_ok=False,
            last_verify_summary="verify exit_code=-1\nCommand timed out after 300.0s",
            last_scoped_command="pytest -q --lf",
            write_steps=4,
            write_set=[str(proj / "index.html"), str(proj / ".remedy-build" / "ledger.json")],
        ),
        home=tmp_path,
    )
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        _project_path_raw=str(proj),
        effective_project_path=lambda: proj,
        config=SimpleNamespace(home_dir=tmp_path),
    )
    toks = begin_turn("ledger-stuck", project_raw=str(proj), active_path=str(proj))
    try:
        st = begin_build_turn(rt, "Create index.html marketing landing page")
        assert st is not None
        assert "--lf" not in (st.last_scoped_command or "")
        assert st.auto_verify_ran is True
        assert st.phase != "repair" or not st.missing_required_files()
        assert should_auto_verify(st) is False
    finally:
        end_turn("ledger-stuck", *toks)
