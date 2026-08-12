"""Machine-native build engine — phase tracking and force nudges."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    build_protocol_block,
    looks_like_build_request,
    monologue_block_nudge,
    next_machine_nudge,
    observe_tool_batch,
    should_force_tools_for_build,
)


def test_detects_build_requests():
    assert looks_like_build_request("implement a REST API for todos")
    assert looks_like_build_request("build me a CLI that greets")
    assert looks_like_build_request("fix the pytest failures in tests/")
    assert looks_like_build_request("review the auth module and fix bugs")
    assert looks_like_build_request("please set up a calculator.py in the project")
    assert not looks_like_build_request("thanks")
    assert not looks_like_build_request("what is a monad?")


def test_begin_build_turn_stamps_runtime():
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
    )
    st = begin_build_turn(rt, "implement soul field tests")
    assert st is not None
    assert st.active
    assert rt._build_turn is st
    proto = build_protocol_block(st)
    assert "RESEARCH" in proto
    assert "PLAN" in proto
    assert "BUILD" in proto


def test_serial_explore_forces_implement():
    st = BuildTurnState(active=True, max_serial_explore=2, require_verify_after_writes=2)
    for _ in range(2):
        observe_tool_batch(
            st,
            [
                {
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path":"a.py"}',
                    }
                }
            ],
        )
    assert st.serial_explore_streak >= 2
    nudge = next_machine_nudge(st)
    assert nudge is not None
    assert "IMPLEMENT" in nudge["content"]
    # Second call does not re-emit
    assert next_machine_nudge(st) is None


def test_writes_force_verify():
    st = BuildTurnState(active=True, require_verify_after_writes=1)
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
    assert st.phase == "implement"
    nudge = next_machine_nudge(st)
    assert nudge is not None
    assert "VERIFY" in nudge["content"]


def test_verify_green_marks_done():
    st = BuildTurnState(active=True)
    observe_tool_batch(
        st,
        [{"function": {"name": "bash_exec", "arguments": '{"command":"pytest -q"}'}}],
        [{"role": "tool", "content": "verify exit_code=0\n5 passed"}],
    )
    assert st.last_verify_ok is True
    assert st.phase == "done"


def test_file_read_passed_does_not_false_green():
    """Reading a test file that says '5 passed' must not mark verify green."""
    st = BuildTurnState(active=True, write_steps=1, write_set=["app.py"])
    observe_tool_batch(
        st,
        [{"function": {"name": "file_read", "arguments": '{"path":"test_app.py"}'}}],
        [{"role": "tool", "content": "def test_ok():\n    assert 1\n# 5 passed\n"}],
    )
    assert st.last_verify_ok is not True


def test_mkdir_exit_zero_does_not_false_green():
    """Successful mkdir / echo is not a test run."""
    st = BuildTurnState(active=True, write_steps=1, write_set=["app.py"])
    observe_tool_batch(
        st,
        [{"function": {"name": "bash_exec", "arguments": '{"command":"mkdir src"}'}}],
        [{"role": "tool", "content": "exit_code=0\ncwd=C:\\proj\n"}],
    )
    assert st.last_verify_ok is not True
    assert st.write_set == ["app.py"]


def test_cat_hello_c_is_not_verify():
    from remedy.core.build_engine import _blob_is_verify_command

    assert _blob_is_verify_command("gcc -o hello.exe hello.c && hello.exe")
    assert _blob_is_verify_command("pytest -q")
    assert not _blob_is_verify_command("cat hello.c")
    assert not _blob_is_verify_command("type hello.c")
    assert not _blob_is_verify_command("gcc --version")
    assert not _blob_is_verify_command("mkdir src")


def test_parallel_file_read_does_not_green_verify_batch():
    """gcc/pytest in the same batch as file_read must only score the verify result."""
    st = BuildTurnState(active=True, write_steps=1, write_set=["hello.c"])
    observe_tool_batch(
        st,
        [
            {
                "id": "call_read",
                "function": {
                    "name": "file_read",
                    "arguments": '{"path":"test_hello.py"}',
                },
            },
            {
                "id": "call_gcc",
                "function": {
                    "name": "bash_exec",
                    "arguments": '{"command":"gcc -o hello.exe hello.c && hello.exe"}',
                },
            },
        ],
        [
            {
                "role": "tool",
                "tool_call_id": "call_read",
                "content": "def test_ok():\n    assert 1\n# 5 passed\n",
            },
            {
                "role": "tool",
                "tool_call_id": "call_gcc",
                "content": "exit_code=1\nstderr:\nundefined reference\n",
            },
        ],
    )
    assert st.last_verify_ok is False
    assert st.phase == "repair"


def test_keep_agency_after_green_play():
    from remedy.core.build_engine import green_continue_message, keep_agency_after_green

    st = BuildTurnState(active=True, goal="build a pygame snake and play it")
    st.last_verify_ok = True
    assert keep_agency_after_green(st) is True
    msg = green_continue_message(st, command="python -m py_compile game.py")
    assert "play" in msg["content"].lower()
    assert "Tools stay on" in msg["content"]
    assert keep_agency_after_green(
        BuildTurnState(active=True, goal="add a helper function")
    ) is False


def test_monologue_block():
    st = BuildTurnState(active=True)
    n = monologue_block_nudge(st)
    assert n is not None
    assert "MONOLOGUE" in n["content"] or "tool_calls" in n["content"]
    # Up to 3 blocks for local re-essay; fourth is None
    assert monologue_block_nudge(st) is not None
    assert monologue_block_nudge(st) is not None
    assert monologue_block_nudge(st) is None


def test_force_tools_for_build():
    rt = SimpleNamespace(
        _llm_provider="anthropic",
        _llm_model="claude-sonnet-4",
        _llm_base_url="",
        _build_turn=BuildTurnState(active=True),
    )
    assert should_force_tools_for_build(rt, "hi") is True
    rt2 = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _build_turn=None,
    )
    assert should_force_tools_for_build(rt2, "build a web scraper") is True
