"""Error vector + syntax gate + green-gate for machine builds."""

from __future__ import annotations

from remedy.core.build_engine import (
    BuildTurnState,
    build_blocks_final_answer,
    build_has_open_drive,
    green_gate_cap_allows_final,
    observe_tool_batch,
)
from remedy.core.build_error_vector import (
    format_repair_ticket,
    parse_verify_output,
)
from remedy.core.build_syntax import check_path_syntax, format_syntax_gate_message


def test_parse_pytest_failures():
    text = """
=========================== short test summary info ===========================
FAILED tests/test_foo.py::test_bar - assert 1 == 2
FAILED tests/test_foo.py::test_baz
======================== 2 failed, 1 passed in 0.12s =========================
exit_code=1
"""
    vec = parse_verify_output(text, command="pytest -q", ok=False)
    assert vec.ok is False
    assert any("test_foo" in n for n in vec.failing_nodes)
    ticket = format_repair_ticket(vec)
    assert "REPAIR TICKET" in ticket
    assert "pytest" in ticket


def test_parse_green():
    vec = parse_verify_output("exit_code=0\n5 passed", command="pytest -q", ok=True)
    assert vec.ok is True
    assert "GREEN" in format_repair_ticket(vec)


def test_syntax_gate_py(tmp_path):
    good = tmp_path / "ok.py"
    good.write_text("x = 1\n", encoding="utf-8")
    bad = tmp_path / "bad.py"
    bad.write_text("def foo(\n", encoding="utf-8")
    assert check_path_syntax(good)["ok"] is True
    r = check_path_syntax(bad)
    assert r["ok"] is False
    msg = format_syntax_gate_message([r])
    assert msg is not None
    assert "SYNTAX GATE" in msg["content"]


def test_write_set_and_green_gate():
    st = BuildTurnState(active=True, require_green_to_finish=True)
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"app.py","content":"x"}',
                }
            }
        ],
    )
    assert st.write_steps == 1
    assert "app.py" in st.write_set or st.write_set
    assert build_blocks_final_answer(st) is True
    # Green verify clears gate
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "bash_exec",
                    "arguments": '{"command":"pytest -q"}',
                }
            }
        ],
        [{"role": "tool", "content": "exit_code=0\nall passed"}],
    )
    assert st.last_verify_ok is True
    assert build_blocks_final_answer(st) is False


def test_green_gate_cap_holds_when_todos_open():
    st = BuildTurnState(
        active=True,
        write_steps=2,
        last_verify_ok=True,
        write_set=[],
        open_todo_count=3,
        require_green_to_finish=True,
    )
    assert build_blocks_final_answer(st) is True
    assert build_has_open_drive(st) is True
    assert green_gate_cap_allows_final(st, reopen_count=6, max_reopens=6) is False
    st.open_todo_count = 0
    assert build_has_open_drive(st) is False
    assert green_gate_cap_allows_final(st, reopen_count=6, max_reopens=6) is True


def test_green_gate_cap_still_ends_verify_loop_without_open_work():
    st = BuildTurnState(
        active=True,
        write_steps=1,
        last_verify_ok=False,
        write_set=["app.py"],
        open_todo_count=0,
        ship_required=False,
        require_green_to_finish=True,
    )
    assert build_blocks_final_answer(st) is True
    assert build_has_open_drive(st) is False
    assert green_gate_cap_allows_final(st, reopen_count=5, max_reopens=6) is False
    assert green_gate_cap_allows_final(st, reopen_count=6, max_reopens=6) is True


def test_file_edit_batch_counts_as_write():
    st = BuildTurnState(active=True)
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "file_edit_batch",
                    "arguments": '{"path":"a.py"}',
                }
            }
        ],
    )
    assert st.write_steps == 1
