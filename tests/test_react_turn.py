"""ReAct turn control — resolve_tools, synthesis, phases, disconnect."""

from __future__ import annotations

from remedy.core.react_loop.binding import resolve_and_apply_tools
from remedy.core.react_policy import REACT_MAX_STALE_EPOCHS
from remedy.core.react_turn import (
    LOCAL_MAX_TOOLS_PER_STEP,
    MAX_PSEUDO_RECOVERIES,
    TurnState,
    apply_tools_decision,
    cap_tools_for_step,
    effective_stale_epochs,
    is_disconnect_error,
    resolve_tools,
    synthesize_from_tools,
)


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_resolve_tools_never_strips_task():
    all_t = [_tool("file_write"), _tool("bash_exec"), _tool("list_dir")]
    d = resolve_tools(
        message="create a calculator app in the project",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason in ("task", "task_write_first", "message_wants_tools") or "task" in d.reason


def test_resolve_tools_verbal_only_disarms_even_with_history():
    """Continuity rebound must not arm tools for 'Reply only STILLALIVE'."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    history = [
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "file_read"}}]},
        {"role": "tool", "content": "ok"},
    ]
    d = resolve_tools(
        message="Reply only STILLALIVE",
        all_tools=all_t,
        turn_tier=1,
        history=history,
        open_tasks=["finish the settings dialog"],
    )
    assert d.tools is None
    assert d.reason == "non_work"
    d2 = resolve_tools(
        message="Turn 0: say only T0OK",
        all_tools=all_t,
        turn_tier=1,
        history=history,
    )
    assert d2.tools is None
    assert d2.reason == "non_work"


def test_resolve_tools_arith_trivia_disarms_even_if_build_active():
    all_t = [_tool("file_write"), _tool("file_read")]
    d = resolve_tools(
        message="1 + 1",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["ship the calculator"],
    )
    assert d.tools is None
    assert d.reason == "non_work"


def test_resolve_tools_l1_strips_pure_chat():
    all_t = [_tool("file_write")]
    d = resolve_tools(
        message="thanks",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is None
    assert d.reason == "l1_pure_chat"


def test_resolve_tools_keyword_miss_product_ask_stays_armed():
    """Any non-social ask must keep tools — do not depend on verb lists."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    d = resolve_tools(
        message="add a dark mode toggle to the about window",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason != "l1_pure_chat"


def test_resolve_tools_noun_free_product_ask_stays_armed():
    """Fail-open: a product change with no special-case verbs stays armed."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    d = resolve_tools(
        message=(
            "the idle lock should be fifteen minutes and the about window "
            "must not require scrolling"
        ),
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason != "l1_pure_chat"


def test_resolve_tools_secretfolder_autolock_not_l1():
    """Live 2026-08-14: product-change prompt disarmed as l1_pure_chat."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    msg = (
        "we need a 15minute autolock timeout and as well can we resize the "
        "settings and about ui they require scrolling to see and don't need "
        "to be that large"
    )
    d = resolve_tools(message=msg, all_tools=all_t, turn_tier=1)
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason != "l1_pure_chat"


def test_resolve_tools_full_bugsweep_not_l1():
    """Live 2026-08-13: 'full bugsweep' disarmed as l1_pure_chat."""
    all_t = [_tool("file_write"), _tool("list_dir"), _tool("file_read")]
    d = resolve_tools(
        message="full bugsweep",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason != "l1_pure_chat"


def test_mid_turn_keep_armed_does_not_override_non_work():
    """Pseudo-tool rearm must not pin tools on a verbal-only turn."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    turn = TurnState(all_tools=all_t)
    turn.rearm(reason="rearm_agency")

    class _Rt:
        _turn_tier = 1

    tools, run = resolve_and_apply_tools(
        runtime=_Rt(),
        turn=turn,
        message="Reply only STILLALIVE",
        plan_mode=False,
        history=[],
        pure_action_kick=False,
        clear_goals_only=False,
        browse_pre_url=None,
        page_interaction=False,
        open_only_browse=False,
        build_state=None,
        open_tasks_for_wall=None,
        step_index=2,
    )
    assert tools is None
    assert run is False
    assert turn.arm_reason == "non_work"


def test_mid_turn_resolve_cannot_disarm_armed_turn():
    """Per-step re-resolve may narrow a pack; it must not strip tools."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    turn = TurnState(all_tools=all_t)
    turn.rearm(reason="rearm_agency")
    assert turn.tools

    class _Rt:
        _turn_tier = 1

    tools, run = resolve_and_apply_tools(
        runtime=_Rt(),
        turn=turn,
        message="thanks",
        plan_mode=False,
        history=[],
        pure_action_kick=False,
        clear_goals_only=False,
        browse_pre_url=None,
        page_interaction=False,
        open_only_browse=False,
        build_state=None,
        open_tasks_for_wall=None,
        step_index=2,
    )
    assert tools is not None
    assert run is True
    assert turn.arm_reason == "keep_armed"


def test_resolve_tools_plan_mode():
    all_t = [_tool("file_write"), _tool("plan_list")]
    d = resolve_tools(
        message="implement everything",
        all_tools=all_t,
        plan_mode=True,
    )
    assert d.pack == "plan" or d.reason.startswith("plan")


def test_stale_epochs_default_is_policy_constant():
    assert effective_stale_epochs(None) == max(1, int(REACT_MAX_STALE_EPOCHS))
    assert effective_stale_epochs(type("R", (), {"_react_max_stale_epochs": 8})()) == 8
    # Explicit runtime value wins
    assert effective_stale_epochs(type("R", (), {"_react_max_stale_epochs": 3})()) == 3


def test_tools_armed_is_schemas_sent_not_all_tools():
    st = TurnState(all_tools=[_tool("a")], tools=None)
    assert st.tools_armed() is False
    st.rearm(reason="test")
    assert st.tools_armed() is True


def test_pseudo_recovery_multi_shot():
    st = TurnState()
    for _ in range(MAX_PSEUDO_RECOVERIES):
        assert st.allow_pseudo_recovery()
        st.note_pseudo_recovery()
    assert not st.allow_pseudo_recovery()


def test_synthesize_from_tools_with_paths():
    msgs = [
        {"role": "tool", "name": "file_write", "content": "Wrote C:/proj/a.py ok"},
        {"role": "tool", "name": "bash_exec", "content": "Error: failed py_compile"},
    ]
    text = synthesize_from_tools(msgs, paths_written=["C:/proj/a.py"])
    assert "Files touched" in text
    assert "a.py" in text
    assert "Issues" in text or "failed" in text.lower()


def test_synthesize_empty_when_no_tools():
    text = synthesize_from_tools([])
    low = text.lower()
    assert "resume" in low or "continue" in low or "history is intact" in low


def test_is_disconnect_error():
    assert is_disconnect_error(Exception("Server disconnected"))
    assert is_disconnect_error("Connection reset by peer")
    assert not is_disconnect_error("model_not_found")


def test_cap_tools_local():
    tools = [_tool(f"t{i}") for i in range(20)]
    capped = cap_tools_for_step(tools, local=True, max_tools=LOCAL_MAX_TOOLS_PER_STEP)
    assert capped is not None
    assert len(capped) == LOCAL_MAX_TOOLS_PER_STEP
    assert cap_tools_for_step(tools, local=False) is tools


def test_phase_nudge_research_to_plan():
    st = TurnState(run_until_done=True, phase="research")
    st.record_tool_batch(["file_read", "list_dir"])
    assert st.research_batches >= 1
    n = st.phase_nudge()
    assert n is not None
    assert "PLAN" in n["content"]
    assert st.plan_seen is True


def test_apply_tools_decision_sets_research_phase():
    st = TurnState()
    d = resolve_tools(
        message="fix the login bug please",
        all_tools=[_tool("file_edit")],
    )
    apply_tools_decision(st, d)
    assert st.run_until_done
    assert st.phase == "research"
