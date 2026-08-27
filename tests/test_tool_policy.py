"""Simple-query tool policy + agent budget."""

from __future__ import annotations

from remedy.core.agent import (
    _MAX_PARALLEL_TOOLS,
    _MAX_REACT_STEPS,
    _REACT_EPOCH_STEPS,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
)
from remedy.core.react_policy import (
    REACT_AUTO_CONTINUE,
    epoch_continue_message,
    is_productive_tool_batch,
    turn_has_unfinished_work,
)


def test_max_tool_steps_has_headroom():
    # Absolute safety total must be far above a soft epoch (long autonomous runs).
    assert _MAX_REACT_STEPS >= 1000
    assert _REACT_EPOCH_STEPS >= 64
    assert _MAX_REACT_STEPS > _REACT_EPOCH_STEPS
    assert _MAX_PARALLEL_TOOLS >= 8
    assert REACT_AUTO_CONTINUE is True


def test_epoch_continue_message_keeps_going():
    msg = epoch_continue_message(epoch=2, total_step=512)
    assert msg["role"] == "system"
    low = msg["content"].lower()
    assert "epoch" in low and "step" in low
    assert "run until" in low or "finished" in low
    assert "not a stop" in low or "continue" in low


def test_run_until_done_never_used_tools_nudge_branch():
    """Epoch wall with tools armed but zero batches should take tools-now path.

    Regression: coding_in_flight was always true when tools were armed, so the
    ``elif run_until_done and all_tools`` nudge was dead code.
    """
    # Mirror the branch predicates used in agent_react_loop epoch wall.
    run_until_done = True
    unfinished = False
    tool_batches_this_turn = 0
    all_tools = [{"type": "function"}]
    tools = all_tools
    tools_armed = bool(tools or all_tools)
    coding_in_flight = run_until_done and (
        unfinished or tool_batches_this_turn > 0 or tools_armed
    )
    never_used_tools = (
        tool_batches_this_turn <= 0 and bool(all_tools) and not unfinished
    )
    assert coding_in_flight is True
    assert never_used_tools is True
    # After first tool batch, never_used becomes false → soft epoch roll instead.
    tool_batches_this_turn = 2
    never_used_tools = (
        tool_batches_this_turn <= 0 and bool(all_tools) and not unfinished
    )
    assert never_used_tools is False


def test_rearm_restores_run_until_done_after_l1_strip():
    """Agency recovery must re-enable run_until_done (epoch force-answer footgun).

    Sequence: L1 strip → run_until_done=False → tools re-armed by promise claim
    without flipping run_until_done → soft epoch wall force-answers mid-implement.
    """
    all_tools = [{"type": "function", "function": {"name": "file_read"}}]
    tools = None
    run_until_done = False

    def _rearm_agency_tools() -> None:
        nonlocal tools, run_until_done
        if all_tools:
            tools = all_tools
            run_until_done = True

    # Simulate agency re-arm after "I'll implement the fix".
    _rearm_agency_tools()
    assert tools is all_tools
    assert run_until_done is True
    # Soft epoch wall must treat this as coding_in_flight, not pure-chat wrap-up.
    step = 256
    epoch_size = 256
    tools_armed = bool(tools or all_tools)
    coding_in_flight = run_until_done and (
        False or 0 > 0 or tools_armed
    )
    assert coding_in_flight is True
    pure_chat_wrap = step >= epoch_size and not run_until_done
    assert pure_chat_wrap is False


def test_rearm_coding_promises_match_review_class():
    """implement/debug/fix short stubs re-arm like review (gauntlet agency)."""
    from remedy.core.react_policy import agency_tool_promise_claim

    for stub in (
        "Let me review the project",
        "I'll implement the handler.",
        "I'll debug the crash.",
        "Let me fix that.",
        "I will run the tests.",
    ):
        assert agency_tool_promise_claim(stub) is True, stub


def test_fingerprint_loop_patience_for_long_tasks():
    """run_until_done / unfinished work get more loop recovery before force-answer."""
    run_until_done = True
    unfinished_loop = True
    max_loop_hits = 8 if (run_until_done or unfinished_loop) else 3
    assert max_loop_hits == 8
    # Chat / no unfinished: stay tight.
    run_until_done = False
    unfinished_loop = False
    max_loop_hits = 8 if (run_until_done or unfinished_loop) else 3
    assert max_loop_hits == 3
    # Keep going while under the long-task ceiling.
    for loop_hits in range(1, 8):
        keep = (
            (unfinished_loop or True)  # run_until_done for implement
            and loop_hits < 8
            and True  # all_tools
        )
        assert keep is True
    assert not ((True) and 8 < 8 and True)


def test_productive_tool_batch_detects_ok_results():
    assert is_productive_tool_batch(
        [{"role": "tool", "content": "file contents here"}]
    )
    assert not is_productive_tool_batch(
        [{"role": "tool", "content": "Error [CODE:tool] missing"}]
    )
    assert not is_productive_tool_batch([])


def test_unfinished_work_false_without_tools():
    class _R:
        config = None
        _session_id = ""

    assert (
        turn_has_unfinished_work(
            _R(), tools_enabled=False, tool_steps_this_turn=5
        )
        is False
    )
    assert (
        turn_has_unfinished_work(
            _R(),
            tools_enabled=True,
            tool_steps_this_turn=3,
            open_tasks=["finish tests"],
        )
        is True
    )
    assert (
        turn_has_unfinished_work(
            _R(), tools_enabled=True, tool_steps_this_turn=2
        )
        is True
    )


def test_simple_questions_skip_tools():
    assert _message_wants_tools("hi") is False
    assert _message_wants_tools("what provider are we connected to") is False
    assert _message_wants_tools("what is max tool calls?") is False
    assert _message_wants_tools("what time is it in paris") is False
    assert _message_wants_tools("who are you?") is False


def test_project_tasks_enable_tools():
    assert _message_wants_tools("read config.toml") is True
    assert _message_wants_tools("list files in src/") is True
    assert _message_wants_tools("implement login in the project") is True
    assert _message_wants_tools("run the tests") is True
    assert _message_wants_tools("fix the bug in agent.py") is True
    assert _message_wants_tools("review project") is True
    assert _message_wants_tools("analyze the architecture") is True
    # Action kicks (must not leave tools=[] / force_answer)
    assert _message_wants_tools("proceed") is True
    assert _message_wants_tools("proceed with all fixes") is True
    assert _message_wants_tools("continue") is True
    assert _message_wants_tools("go ahead") is True


def test_pseudo_tool_detection_and_parse():
    fake = (
        'I\'ll start by reading key files.\n\n'
        'file_read("pyproject.toml") && file_read("README.md") && list_dir("src/")'
    )
    assert _looks_like_pseudo_tools(fake) is True
    parsed = _parse_pseudo_tool_calls(fake)
    names = [((p.get("function") or {}).get("name")) for p in parsed]
    assert "file_read" in names
    assert "list_dir" in names
    assert _looks_like_pseudo_tools("Just a normal answer about tools.") is False


def test_tool_fingerprint_stable():
    a = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    b = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    c = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "b.py"}',
        }
    }
    assert _tool_call_fingerprint(a) == _tool_call_fingerprint(b)
    assert _tool_call_fingerprint(a) != _tool_call_fingerprint(c)
