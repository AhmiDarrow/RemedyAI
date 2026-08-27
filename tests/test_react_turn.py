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
    assert d.reason in ("task", "task_write_first", "message_wants_tools", "build_active") or "task" in d.reason or "message_wants" in d.reason


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


def test_resolve_tools_frustrated_why_keeps_build_armed():
    """A failing site build: 'why is everything failing?' must not strip Build tools."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("bash_exec")]
    d = resolve_tools(
        message="why is everything failing?",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["Create remedy.html marketing landing"],
    )
    assert d.tools is not None
    assert d.run_until_done is True
    assert d.reason == "build_active"
    idle = resolve_tools(
        message="why is everything failing?",
        all_tools=all_t,
        turn_tier=1,
        build_active=False,
    )
    assert idle.tools is not None
    assert idle.reason != "non_work"


def test_resolve_tools_chat_mode_disarms_even_with_work_ask():
    from remedy.core.turn_context import begin_turn, end_turn

    all_t = [_tool("file_write"), _tool("bash_exec")]
    toks = begin_turn("chat-pin", project_raw=None, active_path=".", chat_mode=True)
    try:
        d = resolve_tools(
            message="implement the installer",
            all_tools=all_t,
            turn_tier=1,
            build_active=True,
        )
        assert d.tools is None
        assert d.reason == "chat_mode"
    finally:
        end_turn("chat-pin", *toks)


def test_resolve_tools_attachments_keep_agency():
    """A file in the hand is a request — Chat pin / blank caption must not blind tools."""
    all_t = [_tool("file_read"), _tool("companion_context")]
    d = resolve_tools(
        message="what do you think?",
        all_tools=all_t,
        turn_tier=1,
        has_attachments=True,
    )
    assert d.tools is not None
    assert d.reason == "attachments"
    blank = resolve_tools(
        message="",
        all_tools=all_t,
        turn_tier=1,
        has_attachments=True,
    )
    assert blank.tools is not None
    from remedy.core.turn_context import begin_turn, end_turn

    toks = begin_turn("chat-att", project_raw=None, active_path=".", chat_mode=True)
    try:
        pinned = resolve_tools(
            message="look at this",
            all_tools=all_t,
            turn_tier=1,
            has_attachments=True,
        )
        assert pinned.tools is not None
    finally:
        end_turn("chat-att", *toks)


def test_resolve_tools_game_create_stays_armed():
    """Do not strip Godot / create-app ability when tightening chat gating."""
    all_t = [_tool("file_write"), _tool("file_read")]
    d = resolve_tools(
        message="make a tiny platformer in godot",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is not None
    assert d.reason != "l1_pure_chat"
    assert d.reason != "no_work_request"
    assert d.reason != "ask_first"


def test_resolve_tools_browser_requirement_stays_armed():
    """Live 2026-08-27: leftover todos must not turn 'needs to work for Firefox' into ask_first."""
    all_t = [_tool("file_write"), _tool("bash_exec"), _tool("list_dir"), _tool("file_read")]
    d = resolve_tools(
        message="yes need to work for both firefox AND chrome",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["Chrome extension"],
        history=[
            {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "file_write"}}]},
            {"role": "tool", "content": "ok"},
        ],
    )
    assert d.run_until_done is True
    assert d.reason != "ask_first"
    names = {((t.get("function") or {}).get("name") or "") for t in (d.tools or [])}
    assert "file_write" in names


def test_resolve_tools_yes_after_offer_arms():
    all_t = [_tool("file_write"), _tool("list_dir")]
    d = resolve_tools(
        message="yes",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        history=[
            {
                "role": "assistant",
                "content": "Want me to add Firefox now, or are we just talking?",
            }
        ],
    )
    assert d.reason != "l1_pure_chat"
    assert d.reason != "ask_first"
    assert d.run_until_done is True
    assert d.tools is not None


def test_resolve_tools_ask_first_peek_spent_after_step_zero():
    all_t = [_tool("file_write"), _tool("list_dir")]
    d0 = resolve_tools(
        message="Good deal",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["finish the project review"],
        step_index=0,
    )
    assert d0.reason == "ask_first"
    assert d0.pack == "peek"
    d1 = resolve_tools(
        message="Good deal",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["finish the project review"],
        step_index=1,
    )
    assert d1.reason == "ask_first"
    assert d1.tools is None
    assert d1.pack == "none"


def test_disconnect_retry_sticks_nonstream():
    t = TurnState()
    assert t.force_nonstream is False
    t.note_disconnect_retry()
    assert t.force_nonstream is True
    assert t.allow_disconnect_retry() is True


def test_resolve_tools_open_work_followup_stays_armed():
    """Long session: leftover job + a real follow-up is a continue, not a chat."""
    all_t = [_tool("file_write"), _tool("bash_exec"), _tool("list_dir")]
    history = [
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "file_write"}}]},
        {"role": "tool", "content": "ok"},
    ]
    for msg in (
        "ok on to assets, they need work overall",
        "fix the movement issues, we'll circle back to sprites",
        "now the sprite remake with pixellab",
    ):
        d = resolve_tools(
            message=msg,
            all_tools=all_t,
            turn_tier=1,
            build_active=True,
            open_tasks=["cohesive sprite remake"],
            history=history,
        )
        assert d.run_until_done is True, msg
        assert d.reason != "ask_first", msg
        names = {((t.get("function") or {}).get("name") or "") for t in (d.tools or [])}
        assert "file_write" in names, msg


def test_resolve_tools_ack_does_not_inherit_leftover_build():
    """'Good deal' is not a work request — leftover review todos never re-arm.

    Ambiguous acks keep only the read-only peek pack (look before asking):
    no write/shell tools, and the turn is never driven (run_until_done off).
    """
    all_t = [_tool("file_write"), _tool("bash_exec"), _tool("list_dir")]
    d = resolve_tools(
        message="Good deal",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["finish the project review"],
        history=[
            {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "file_read"}}]},
            {"role": "tool", "content": "ok"},
        ],
    )
    assert d.reason == "ask_first"
    assert d.run_until_done is False
    names = {((t.get("function") or {}).get("name") or "") for t in (d.tools or [])}
    assert names == {"list_dir"}
    assert "file_write" not in names and "bash_exec" not in names


def test_resolve_tools_sounds_good_asks_when_work_is_open():
    """Soft agree is not a continue — ask, don't assume leftover tools."""
    all_t = [_tool("file_write"), _tool("bash_exec")]
    d = resolve_tools(
        message="sounds good",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["finish the logos"],
    )
    assert d.tools is None
    assert d.reason == "ask_first"
    idle = resolve_tools(message="sounds good", all_tools=all_t, turn_tier=1)
    assert idle.tools is None
    assert idle.reason == "no_work_request"


def test_resolve_tools_feeling_question_does_not_inherit_job_tools():
    """Live 2026-08-26: 'how does a local model feel?' got empty bash_exec."""
    all_t = [
        _tool("file_write"),
        _tool("bash_exec"),
        _tool("host_run"),
        _tool("list_dir"),
        _tool("file_read"),
    ]
    d = resolve_tools(
        message="how does a local model feel?",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["wire Qwen 3.8 MTP"],
        history=[
            {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "host_run"}}]},
        ],
    )
    assert d.tools is None
    assert d.run_until_done is False
    assert d.reason == "knowledge"


def test_resolve_tools_l1_strips_pure_chat():
    all_t = [_tool("file_write")]
    d = resolve_tools(
        message="thanks",
        all_tools=all_t,
        turn_tier=1,
    )
    assert d.tools is None
    assert d.reason == "l1_pure_chat"


def test_resolve_tools_greeting_disarms_on_any_tier():
    """Hi/thanks stay chat-only regardless of turn tier."""
    all_t = [_tool("file_write"), _tool("bash_exec")]
    for tier in (0, 1):
        d = resolve_tools(message="thanks", all_tools=all_t, turn_tier=tier)
        assert d.tools is None
        assert d.reason == "l1_pure_chat"


def test_resolve_tools_greeting_disarms_leftover_build():
    """Bare Hi must not inherit a previous coding turn's tools."""
    all_t = [_tool("file_write"), _tool("bash_exec")]
    d = resolve_tools(
        message="Hi",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
        open_tasks=["finish the installer"],
        history=[{"role": "user", "content": "implement the installer"}],
    )
    assert d.tools is None
    assert d.reason == "l1_pure_chat"
    keep = resolve_tools(
        message="Hi keep going",
        all_tools=all_t,
        turn_tier=1,
        build_active=True,
    )
    assert keep.tools
    assert keep.reason != "l1_pure_chat"


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


def test_mid_turn_resolve_cannot_downgrade_full_pack_to_peek():
    """A driven work turn must not become ask_first peek on a later step."""
    all_t = [_tool("file_write"), _tool("file_read"), _tool("list_dir")]
    turn = TurnState(all_tools=all_t, run_until_done=True, arm_reason="task")
    turn.rearm(reason="rearm_agency")
    assert turn.run_until_done is True

    class _Rt:
        _turn_tier = 1

    tools, run = resolve_and_apply_tools(
        runtime=_Rt(),
        turn=turn,
        message="Good deal",
        plan_mode=False,
        history=[
            {"role": "assistant", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "ok"},
        ],
        pure_action_kick=False,
        clear_goals_only=False,
        browse_pre_url=None,
        page_interaction=False,
        open_only_browse=False,
        build_state=None,
        open_tasks_for_wall=["finish the review"],
        step_index=4,
    )
    assert tools is not None
    assert run is True
    assert turn.arm_reason == "keep_armed"
    names = {((t.get("function") or {}).get("name") or "") for t in (tools or [])}
    assert "file_write" in names


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


def test_rearm_stays_plan_pack():
    st = TurnState(
        message="plan a calculator",
        plan_mode=True,
        all_tools=[_tool("file_write"), _tool("plan_list")],
        tools=[_tool("plan_list")],
    )
    st.rearm(reason="keep_armed")
    names = {
        ((t.get("function") or {}).get("name") or "")
        for t in (st.tools or [])
    }
    assert "file_write" not in names
    assert st.run_until_done is False


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


def test_cap_tools_keeps_operate_core_over_help_goal():
    tools = [
        _tool("help_list"),
        _tool("goal_add"),
        _tool("goal_list"),
        _tool("companion_context"),
        _tool("memory_search"),
        _tool("web_search"),
        _tool("web_fetch"),
        _tool("skill_search"),
        _tool("file_read"),
        _tool("host_run"),
        _tool("bash_exec"),
        _tool("file_write"),
    ]
    capped = cap_tools_for_step(tools, local=True, max_tools=8)
    names = {
        str((t.get("function") or {}).get("name"))
        for t in (capped or [])
    }
    assert "file_read" in names
    assert "file_write" in names
    assert "host_run" in names
    assert "bash_exec" in names
    assert "help_list" not in names
    assert "goal_add" not in names


def test_write_first_keeps_host_run_for_launch_site():
    from remedy.core.local_agent_optimize import filter_tools_write_first

    tools = [
        _tool("file_write"),
        _tool("file_read"),
        _tool("host_run"),
        _tool("bash_exec"),
        _tool("mission_start"),
    ]
    kept = filter_tools_write_first(
        tools,
        user_message="launch the site locally so I can see the page",
        step_index=0,
    )
    names = {
        str((t.get("function") or {}).get("name"))
        for t in (kept or [])
    }
    assert "host_run" in names
    assert "file_write" in names
    assert "mission_start" not in names


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
