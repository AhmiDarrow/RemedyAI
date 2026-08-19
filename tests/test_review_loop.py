"""Read-only review must not get trapped in the write-and-verify build loop.

Regression coverage for the "review the project" infinite-loop: a read-only
intent (review / analyze / explain) is supervised as RESEARCH → SYNTHESIZE →
DELIVER, never pushed to BUILD, and never blocked from finishing — while any
actual file write upgrades it back to a full build with the green gate intact.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    build_blocks_final_answer,
    build_protocol_block,
    next_machine_nudge,
    observe_tool_batch,
)
from remedy.core.intent_policy import (
    looks_like_readonly_request,
    policy_for_intent,
)

# --- detection -------------------------------------------------------------

READONLY = [
    "review the project",
    "review project",
    "analyze the auth flow",
    "explain how the build loop works",  # "build" is a noun here
    "review the build system",
    "look at the build errors",
    "investigate why the tests fail",
    "summarize the architecture",
    "what does begin_build_turn do",
    "tell me about the memory layer",
    "audit the security posture",
]

NOT_READONLY = [
    "fix the login bug",
    "implement a calculator",
    "review the code and fix the bugs",  # change verb present
    "add logging to the parser",
    "refactor build_engine",
    "audit the security and then patch it",
    "build a todo app",
    "rebuild the index",
    "set up CI",
    "make it responsive",
    "optimize the query",
]


@pytest.mark.parametrize("msg", READONLY)
def test_readonly_detected(msg: str) -> None:
    assert looks_like_readonly_request(msg) is True


@pytest.mark.parametrize("msg", NOT_READONLY)
def test_change_not_readonly(msg: str) -> None:
    assert looks_like_readonly_request(msg) is False


def test_policy_routes_review_pack() -> None:
    assert policy_for_intent("chat", user_text="review the project")["id"] == "review"
    # Router label should not override the read-only text.
    assert policy_for_intent("task", user_text="review the project")["id"] == "review"
    assert policy_for_intent("tool", user_text="analyze the parser")["id"] == "review"
    # Change work keeps the build/task loop.
    assert policy_for_intent("chat", user_text="fix the login bug")["id"] in {
        "task",
        "build",
    }
    assert policy_for_intent("chat", user_text="implement a calculator")["id"] == "build"


def test_review_pack_says_no_verify_needed() -> None:
    sysmsg = policy_for_intent("chat", user_text="review the project")["system"]
    assert "read-only" in sysmsg.lower()
    assert "no file_write" in sysmsg.lower() or "no verify" in sysmsg.lower()


# --- build-engine behaviour ------------------------------------------------


def _rt() -> SimpleNamespace:
    return SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
        config=None,
    )


def test_begin_build_turn_marks_readonly() -> None:
    st = begin_build_turn(_rt(), "review the project", force=True)
    assert st is not None
    assert st.read_only is True
    assert st.require_green_to_finish is False


def test_begin_build_turn_build_is_not_readonly() -> None:
    st = begin_build_turn(_rt(), "implement a calculator", force=True)
    assert st is not None
    assert st.read_only is False
    assert st.require_green_to_finish is True


def test_readonly_never_force_implements() -> None:
    st = BuildTurnState(
        active=True,
        phase="scout",
        read_only=True,
        require_green_to_finish=False,
        goal="review the project",
    )
    # Heavy scouting that would normally trip FORCE IMPLEMENT.
    st.serial_explore_streak = 9
    st.explore_steps = 12
    assert next_machine_nudge(st) is None
    assert build_blocks_final_answer(st) is False


def test_readonly_protocol_block_is_review() -> None:
    st = BuildTurnState(active=True, phase="scout", read_only=True, goal="review x")
    proto = build_protocol_block(st)
    assert "Read-only review" in proto
    assert "no file_write" in proto.lower() or "no verify" in proto.lower()


def test_write_upgrades_readonly_to_build() -> None:
    st = BuildTurnState(
        active=True,
        phase="scout",
        read_only=True,
        require_green_to_finish=False,
    )
    observe_tool_batch(
        st,
        [{"function": {"name": "file_write", "arguments": '{"path": "a.py"}'}, "id": "t1"}],
        [],
    )
    assert st.write_steps == 1
    assert st.read_only is False
    assert st.require_green_to_finish is True
    # Now a written-but-unverified change must NOT be allowed to finish.
    assert build_blocks_final_answer(st) is True


def test_normal_build_still_force_implements() -> None:
    # Capability preserved: a real build with no writes after scouting is nudged.
    st = BuildTurnState(active=True, phase="scout", read_only=False, max_serial_explore=2)
    st.serial_explore_streak = 3
    st.explore_steps = 5
    n = next_machine_nudge(st)
    assert n is not None
    assert "FORCE IMPLEMENT" in n.get("content", "")


# --- loop layer (epoch wall) ----------------------------------------------


@pytest.mark.parametrize(
    "msg",
    ["continue", "keep going", "finish it", "resume", "", "next",
     "pick up where we left off", "carry on", "proceed."],
)
def test_generic_continuation_true(msg: str) -> None:
    from remedy.core.build_engine import _is_generic_continuation

    assert _is_generic_continuation(msg) is True


@pytest.mark.parametrize(
    "msg",
    ["finish the API", "build a calculator", "review the project",
     "create index.html", "continue the auth refactor and add tests"],
)
def test_specific_goal_not_continuation(msg: str) -> None:
    from remedy.core.build_engine import _is_generic_continuation

    assert _is_generic_continuation(msg) is False


def test_epoch_wall_frees_readonly_turn() -> None:
    from remedy.core.react_policy import turn_has_unfinished_work
    from remedy.core.turn_context import begin_turn, end_turn

    rt = _rt()
    t = begin_turn("s-ro", project_raw=None, active_path=".")
    try:
        st = begin_build_turn(rt, "review the whole project", force=True)
        assert st is not None and st.read_only
        # Ran tools this turn, phase still "scout": would loop forever pre-fix.
        assert (
            turn_has_unfinished_work(
                rt,
                session_id="s-ro",
                tools_enabled=True,
                tool_steps_this_turn=6,
            )
            is False
        )
    finally:
        end_turn("s-ro", *t)


def test_progress_score_climbs_with_work() -> None:
    from remedy.core.build_engine import build_progress_score

    st = BuildTurnState(active=True)
    assert build_progress_score(st) == 0
    st.write_steps = 2
    st.verify_steps = 1
    assert build_progress_score(st) == 3
    st.last_verify_ok = True
    st.ship_pushed = True
    assert build_progress_score(st) == 3 + 1 + 3


def test_open_drive_stalls_but_never_caps_progress() -> None:
    from remedy.core.build_engine import open_drive_should_continue

    st = BuildTurnState(active=True, open_todo_count=1)
    # Advancing (small steps_since_progress) → keep overriding caps.
    assert open_drive_should_continue(st, steps_since_progress=5, patience=60) is True
    # Stalled past patience → stop honestly (this is the runaway ceiling).
    assert open_drive_should_continue(st, steps_since_progress=61, patience=60) is False
    # No open drive → never overrides caps at all.
    st2 = BuildTurnState(active=True, open_todo_count=0)
    assert open_drive_should_continue(st2, steps_since_progress=1, patience=60) is False


def test_no_compiler_stops_the_c_retry_loop(monkeypatch) -> None:
    import remedy.core.build_engine as BE

    # Simulate a machine with no C toolchain on PATH.
    monkeypatch.setattr(BE, "_has_c_toolchain", lambda: False)
    st = BuildTurnState(active=True, phase="implement", require_green_to_finish=True)
    st.write_steps = 1
    st.write_set = ["main.c"]
    n = BE.next_machine_nudge(st)
    assert n is not None
    assert "NEEDS A COMPILER" in n["content"]
    # Trap released so the turn can conclude with the source saved + a plain note.
    assert st.require_green_to_finish is False
    assert BE.build_blocks_final_answer(st) is False


def test_compiler_present_keeps_c_green_gate(monkeypatch) -> None:
    import remedy.core.build_engine as BE

    monkeypatch.setattr(BE, "_has_c_toolchain", lambda: True)
    st = BuildTurnState(active=True, phase="implement", require_green_to_finish=True)
    st.write_steps = 1
    st.write_set = ["main.c"]
    n = BE.next_machine_nudge(st)
    # No "needs a compiler" bail-out — the normal C green gate stays in force.
    assert not (n and "NEEDS A COMPILER" in n.get("content", ""))
    assert st.require_green_to_finish is True


def test_epoch_wall_still_holds_real_build() -> None:
    from remedy.core.react_policy import turn_has_unfinished_work
    from remedy.core.turn_context import begin_turn, end_turn

    rt = _rt()
    t = begin_turn("s-build", project_raw=None, active_path=".")
    try:
        st = begin_build_turn(rt, "implement the parser", force=True)
        assert st is not None and not st.read_only
        st.write_steps = 1
        st.last_verify_ok = None  # wrote code, not verified
        assert (
            turn_has_unfinished_work(
                rt,
                session_id="s-build",
                tools_enabled=True,
                tool_steps_this_turn=3,
            )
            is True
        )
    finally:
        end_turn("s-build", *t)
