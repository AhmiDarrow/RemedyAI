"""Post-release roadmap 1–10: auto-verify cooldown, ship gate, path hygiene."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    build_blocks_final_answer,
    build_has_open_drive,
    format_ship_report_line,
    frontier_continue_inject,
    green_continue_message,
    green_gate_cap_allows_final,
    looks_like_ship_goal,
    observe_tool_batch,
    unfinished_green_gate_message,
)
from remedy.core.build_ledger import (
    build_tmp_dir,
    build_tmp_script_path,
    merge_turn_into_ledger,
)
from remedy.core.build_oracle import should_auto_verify
from remedy.core.local_agent_optimize import is_frontier_binding, needs_agent_harness


def test_looks_like_ship_goal():
    assert looks_like_ship_goal("push and gh release v1.2.0")
    assert looks_like_ship_goal("ship to github")
    assert not looks_like_ship_goal("fix the unit tests only")


def test_begin_sets_ship_required():
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=SimpleNamespace(home_dir=None),
    )
    st = begin_build_turn(rt, "pytest green then push and release v1")
    assert st is not None
    assert st.ship_required is True


def test_source_write_invalidates_green_docs_do_not():
    st = BuildTurnState(
        active=True,
        last_verify_ok=True,
        auto_verify_ran=True,
        write_steps=2,
        write_steps_at_last_green=2,
    )
    # Doc write — must NOT clear green
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"README.md","content":"x"}',
                }
            }
        ],
    )
    assert st.last_verify_ok is True
    assert st.auto_verify_ran is True
    # Source write — invalidates
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"app.py","content":"x=1"}',
                }
            }
        ],
    )
    assert st.last_verify_ok is None
    assert st.auto_verify_ran is False
    assert any(p.endswith("app.py") for p in st.write_set)


def test_auto_verify_cooldown_after_green():
    st = BuildTurnState(
        active=True,
        write_steps=3,
        write_steps_at_last_green=3,
        auto_verify_ran=True,
        last_verify_ok=True,
        verify_steps=1,
        verify_command="pytest -q",
        write_set=[],
        phase="ship",
        ship_required=True,
    )
    assert should_auto_verify(st) is False
    # Zero writes this turn — never thrash
    st2 = BuildTurnState(
        active=True,
        write_steps=0,
        write_set=[],
        last_verify_ok=True,
        auto_verify_ran=True,
        verify_command="npm test",
    )
    assert should_auto_verify(st2) is False
    # New source after green
    st.write_set = ["src/foo.py"]
    st.write_steps = 4
    st.auto_verify_ran = False
    st.last_verify_ok = None
    assert should_auto_verify(st) is True


def test_ship_gate_blocks_final_until_push():
    st = BuildTurnState(
        active=True,
        ship_required=True,
        last_verify_ok=True,
        write_steps=2,
        write_set=[],
        ship_pushed=False,
        require_green_to_finish=True,
    )
    assert build_blocks_final_answer(st) is True
    msg = unfinished_green_gate_message(st)
    assert "SHIP GATE" in msg["content"]
    st.ship_pushed = True
    # goal without release keyword → complete after push
    st.goal = "push to origin"
    assert st.ship_complete() is True
    assert build_blocks_final_answer(st) is False


def test_green_gate_cap_holds_when_ship_unfinished():
    st = BuildTurnState(
        active=True,
        ship_required=True,
        last_verify_ok=True,
        write_steps=2,
        write_set=[],
        ship_pushed=False,
        require_green_to_finish=True,
    )
    assert build_blocks_final_answer(st) is True
    assert build_has_open_drive(st) is True
    assert green_gate_cap_allows_final(st, reopen_count=6, max_reopens=6) is False
    st.ship_pushed = True
    st.goal = "push to origin"
    assert st.ship_complete() is True
    assert build_has_open_drive(st) is False
    assert green_gate_cap_allows_final(st, reopen_count=6, max_reopens=6) is True


def test_green_continue_ship_message():
    st = BuildTurnState(
        active=True,
        ship_required=True,
        ship_pushed=False,
        last_verify_ok=True,
        verify_command="pytest -q",
    )
    m = green_continue_message(st, command="pytest -q")
    assert "continue SHIP" in m["content"]
    assert "git_push" in m["content"]
    st2 = BuildTurnState(active=True, ship_required=False, last_verify_ok=True)
    m2 = green_continue_message(st2, command="pytest -q")
    assert "stop building" in m2["content"]


def test_ship_report_line():
    st = BuildTurnState(
        active=True,
        phase="ship",
        ship_required=True,
        ship_pushed=True,
        ship_url="https://github.com/o/r",
        last_verify_ok=True,
    )
    line = format_ship_report_line(st)
    assert line.startswith("@@ship_report:")
    assert "ship_pushed" in line


def test_ledger_path_hygiene(tmp_path):
    st = BuildTurnState(
        active=True,
        paths_touched=[
            "app.py",
            "git push origin main",
            "pytest -q",
            "src/mod.py",
        ],
        write_steps=1,
        phase="implement",
        goal="build",
        project_path=str(tmp_path),
    )
    entry = merge_turn_into_ledger(st, project_path=str(tmp_path), session_id="t")
    assert "app.py" in entry.paths_touched or any(
        "app.py" in p for p in entry.paths_touched
    )
    assert not any(str(p).startswith("git ") for p in entry.paths_touched)
    assert not any(str(p).startswith("pytest") for p in entry.paths_touched)


def test_build_tmp_helpers(tmp_path):
    d = build_tmp_dir(tmp_path)
    assert d.is_dir()
    assert d.name == "tmp"
    assert ".remedy-build" in str(d)
    p = build_tmp_script_path("retag.py", tmp_path)
    assert p.parent == d
    assert p.name == "retag.py"
    p2 = build_tmp_script_path("../evil.py", tmp_path)
    assert p2.parent == d
    assert ".." not in p2.name


def test_frontier_continue_inject():
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="https://api.x.ai/v1",
        _session_brief=SimpleNamespace(
            intent="finish RemedyPDF release",
            open_tasks=["push", "gh release"],
            next_steps=["git_push"],
            key_paths=[],
            user_constraints=[],
        ),
        _build_turn=BuildTurnState(
            active=True,
            ship_required=True,
            ship_pushed=False,
            last_verify_ok=True,
            phase="ship",
        ),
        config=SimpleNamespace(home_dir=None),
    )

    def _proj():
        return ""

    rt.effective_project_path = _proj  # type: ignore[attr-defined]
    msg = frontier_continue_inject(rt, "continue")
    assert msg is not None
    assert "Frontier continue" in msg["content"]
    assert "finish ship" in msg["content"].lower() or "git_push" in msg["content"]
    # Local binding → no inject
    rt._llm_provider = "rmb"
    rt._llm_base_url = "http://127.0.0.1:8787/v1"
    assert frontier_continue_inject(rt, "continue") is None


def test_harness_split_still_holds():
    assert needs_agent_harness("rmb", "qwen", "http://127.0.0.1:8787/v1") is True
    assert is_frontier_binding("xai", "grok-4", "https://api.x.ai/v1") is True


def test_shell_commands_not_in_paths_via_touch():
    st = BuildTurnState(active=True)
    st.touch_path("git push origin main")
    st.touch_path("app.py")
    assert "app.py" in st.paths_touched
    assert "git push origin main" not in st.paths_touched
    assert any("git push" in s for s in st.shell_log)


def test_observe_verify_green_goes_ship_when_required():
    st = BuildTurnState(active=True, ship_required=True, write_steps=1)
    observe_tool_batch(
        st,
        [{"function": {"name": "bash_exec", "arguments": '{"command":"pytest -q"}'}}],
        [{"role": "tool", "content": "exit_code=0\n3 passed"}],
    )
    assert st.last_verify_ok is True
    assert st.phase == "ship"
    assert st.auto_verify_ran is True
