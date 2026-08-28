"""What the ReAct loop does *after* a tool batch, and why it matters.

``call_llm_stream`` does not simply hand the model's next paragraph to the
person. Once tools have run — or once tools were armed and the model refused
to call any — a stack of gates decides whether the turn may end:

* an "answer" that is really the model's private scratchpad is refused;
* faked tool markup is never shipped as the final bubble;
* a prose promise to use tools re-arms the tools instead of ending the turn;
* the build engine refuses "done" until a verify went green, emits a ship
  report when it does, and gives up after a bounded number of re-opens;
* a local model that keeps narrating instead of calling tools is detected by
  fingerprint, fed a live project listing, and finally hard-nudged — with the
  monologue itself never fed back into history as loop fuel;
* every one of those injections is *capped*, so a stuck model ends the turn
  honestly instead of burning tokens forever.

The expensive failures here are the quiet ones: a build that stops after a
prose promise and never touches disk, a "done" claimed over a red verify, an
internal monologue shown to the person as the answer, or a nudge loop with no
ceiling that spins until the connection dies. So the assertions below are
mostly negative: what must be refused, what must be capped, what must never
reach the chat bubble.

Everything talks to :mod:`tests.harness.fake_llm`; nothing here opens a
socket, drives the desktop, or touches ``~/.remedy``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.build_engine import BuildTurnState
from remedy.core.providers import clear_provider_quarantine
from remedy.core.react_loop.loop import call_llm_stream
from remedy.models import AgentConfig
from tests.harness.fake_llm import (
    FakeLLM,
    FakeToolRegistry,
    ToolFailure,
    Turn,
    text_turn,
    tool_call,
    tool_turn,
)

#: The tools-on/tools-off gate; pin it off for turns that must stay chat-only.
NO_TOOLS = "remedy.core.agent._message_wants_tools"

#: A model reply that is obviously the model talking to itself.
SCRATCHPAD = (
    "The user wants a status update. I should not leak tool markup, so I will "
    "give a clean summary from context."
)


@pytest.fixture(autouse=True)
def _clean_provider_breaker():
    """The provider circuit breaker is process-global."""
    clear_provider_quarantine()
    yield
    clear_provider_quarantine()


def make_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    """A runtime pointed at a throwaway home and an unroutable provider."""
    kwargs: dict[str, Any] = {
        "name": "test",
        "home_dir": str(tmp_path / "home"),
        "project_path": str(tmp_path / "proj"),
        "llm_provider": "openai",
        "llm_model": "fake-model",
        "llm_api_key": "sk-test",
        "llm_base_url": "http://llm.invalid/v1",
    }
    kwargs.update(overrides)
    (tmp_path / "proj").mkdir(exist_ok=True)
    runtime = BasicRuntime(AgentConfig(**kwargs), memory=None)
    runtime._max_react_steps = 24
    return runtime


def make_local_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    """A runtime whose binding counts as local muscle (harness rails ON).

    ``ollama`` is in ``token_nanobot._LOCAL_PROVIDERS``, so
    ``needs_agent_harness`` is true and the monologue breakers arm.
    """
    overrides.setdefault("llm_provider", "ollama")
    overrides.setdefault("llm_model", "qwen2.5-coder:7b")
    return make_runtime(tmp_path, **overrides)


def stamp_build_state(runtime: BasicRuntime, **fields: Any) -> BuildTurnState:
    """Put a machine build state on the runtime without running a build turn.

    ``get_build_state`` falls back to ``runtime._build_turn`` when no per-session
    map exists, which is exactly the shape ``begin_build_turn`` leaves behind.
    Stamping it lets these tests pin one build phase instead of driving the whole
    build engine (which would run real verify commands).
    """
    fields.setdefault("active", True)
    fields.setdefault("goal", "build the widget")
    state = BuildTurnState(**fields)
    runtime._build_turn = state
    return state


async def drain(runtime: BasicRuntime, message: str, **kwargs: Any) -> list[str]:
    """Run one whole turn and collect every chunk it yielded."""
    kwargs.setdefault("session_id", str(runtime.config.home_dir))
    return [chunk async for chunk in call_llm_stream(runtime, message, **kwargs)]


def answer(chunks: list[str]) -> str:
    """Only what the person sees — '@@' chunks are UI lifecycle events."""
    return "".join(c for c in chunks if not c.startswith("@@"))


def events(chunks: list[str]) -> list[str]:
    return [c for c in chunks if c.startswith("@@")]


def user_texts(fake: FakeLLM) -> list[str]:
    """Owner + partner injects the loop posted (user and system)."""
    return [t for r in fake.requests for t in r.steering_texts()]


def count_user_texts(fake: FakeLLM, needle: str) -> int:
    """Peak number of matching steering lines on any one request.

    Slim can drop an earlier system inject from the last snapshot; the
    cap still applied when the peak snapshot held it.
    """
    return max(
        (sum(1 for t in r.steering_texts() if needle in t) for r in fake.requests),
        default=0,
    )


# --------------------------------------------------------------------------
# scratchpad: the model's private notes are never the answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_internal_scratchpad_after_tools_is_refused_as_the_final_answer(
    tmp_path,
):
    """122 tools then "The user wants…" is a failed turn, not a finished one."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn(SCRATCHPAD),
            text_turn("I ran add and the result is 5."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool with a=1")

    assert fake.request_count == 3
    assert any("internal scratchpad" in t for t in user_texts(fake))
    # The rejected monologue is replayed as *assistant* text so the model can
    # see what it said — but it must never reach the person.
    assert SCRATCHPAD in fake.requests[2].texts_for_role("assistant")
    reply = answer(chunks)
    assert "The user wants" not in reply
    assert "I ran add and the result is 5." in reply


@pytest.mark.asyncio
async def test_a_frontier_scratchpad_retry_is_asked_for_prose_with_tools_disarmed(
    tmp_path,
):
    """On a hosted model the summary re-ask must not invite another tool batch."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn(SCRATCHPAD), text_turn("Result: 5.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, "run the add tool with a=1")

    assert fake.requests[2].tools == []


# --------------------------------------------------------------------------
# agency: a promise to use tools re-arms them
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_prose_promise_to_use_tools_re_arms_them_instead_of_ending_the_turn(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn("Activating skill now."),
            text_turn("Done — the sum is 5."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool with a=1")

    assert fake.request_count == 3
    assert any("Do not only *say* you will use tools" in t for t in user_texts(fake))
    # Re-arming that dropped the schemas could never be satisfied.
    assert fake.requests[2].tools
    assert "Activating skill now." not in answer(chunks)


# --------------------------------------------------------------------------
# narration after real tool work — nudged once on a hosted model, then let go
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrating_progress_after_tools_is_nudged_back_to_real_tool_calls(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    narration = "Checking the numbers and verifying the totals for you."
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn(narration), text_turn("Final: 5.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, "run the add tool with a=1")

    assert any("Stop narrating intent" in t for t in user_texts(fake))
    assert narration in fake.requests[2].texts_for_role("assistant")


@pytest.mark.asyncio
async def test_a_hosted_model_is_only_nudged_once_for_narration_then_trusted(
    tmp_path,
):
    """Frontier cap is 1: thrashing a model that self-steers just burns tokens."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn("Checking the numbers now."),
            text_turn("Checking the totals again."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool with a=1")

    assert fake.request_count == 3
    assert sum(1 for t in user_texts(fake) if "Stop narrating intent" in t) == 1
    assert "Checking the totals again." in answer(chunks)


# --------------------------------------------------------------------------
# the build engine green gate: no "done" over a red verify
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_build_that_wrote_code_but_never_verified_is_refused_a_final_answer(
    tmp_path,
):
    """Code on disk with no green verify is not a finished build."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    state = stamp_build_state(
        runtime, write_steps=1, last_verify_ok=None, write_set=["app.py"]
    )
    fake = FakeLLM([], when_exhausted=text_turn("All finished!"))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        await drain(runtime, "say hello")

    gate = [t for t in fake.requests[1].steering_texts() if "GREEN GATE" in t]
    assert len(gate) == 1
    # The gate hands the model the evidence it is missing, not just a scolding.
    assert "app.py" in gate[0]
    assert "last_verify_ok=None" in gate[0]
    assert "green_gate" in state.nudges_emitted


@pytest.mark.asyncio
async def test_a_model_claiming_the_tests_pass_does_not_satisfy_the_green_gate(
    tmp_path,
):
    """Only the machine's own verify record opens the gate — never prose."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    state = stamp_build_state(
        runtime, write_steps=1, last_verify_ok=None, write_set=["app.py"]
    )
    fake = FakeLLM([], when_exhausted=text_turn("I ran pytest and it is all green."))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        await drain(runtime, "say hello")

    assert state.last_verify_ok is None
    assert count_user_texts(fake, "GREEN GATE") == 6


@pytest.mark.asyncio
async def test_the_green_gate_gives_up_after_its_reopen_cap_and_ships_a_report(
    tmp_path,
):
    """A verify that never goes green must end the turn, not spin forever."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    stamp_build_state(runtime, write_steps=1, last_verify_ok=False, write_set=["a.py"])
    fake = FakeLLM([], when_exhausted=text_turn("Done, honest."))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    # max_green_gate_reopens is 6: six blocked finals, then the seventh is let
    # through with a ship report rather than a seventh re-ask.
    assert count_user_texts(fake, "GREEN GATE") == 6
    assert fake.request_count == 7
    ship = [e for e in events(chunks) if e.startswith("@@ship_report:")]
    assert len(ship) == 1
    assert '"verify_ok":false' in ship[0]


@pytest.mark.asyncio
async def test_a_build_that_does_not_block_the_final_still_reports_what_it_shipped(
    tmp_path,
):
    """The ship report is observability — it must survive the happy path too."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    stamp_build_state(
        runtime,
        # A read-only review never demands a green verify to finish.
        read_only=True,
        require_green_to_finish=False,
        phase="done",
        paths_touched=["notes.md"],
    )
    fake = FakeLLM([text_turn("Here is the review.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    ship = [e for e in events(chunks) if e.startswith("@@ship_report:")]
    assert len(ship) == 1
    assert '"phase":"done"' in ship[0]
    assert "Here is the review." in answer(chunks)


@pytest.mark.asyncio
async def test_no_ship_report_is_emitted_when_no_build_is_running(tmp_path):
    """A plain chat turn must not fake build telemetry."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Hello there.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert not [e for e in events(chunks) if e.startswith("@@ship_report:")]


# --------------------------------------------------------------------------
# local muscle: the monologue loop breaker
# --------------------------------------------------------------------------


MONOLOGUE = (
    "Let me lay out the architecture first. I will design the module layout, "
    "then decide on the data model, and finally sketch the CLI surface before "
    "writing any code at all."
)


#: A question that arms tools (fail-open) but is not itself a work order, so
#: the zero-tool-work driver stays out of the way and the monologue rails run.
OPINION_ASK = "what do you think of the new plan?"


@pytest.mark.asyncio
async def test_a_local_model_that_narrates_instead_of_calling_tools_is_broken_out_of_it(
    tmp_path,
):
    """A 7B that essays with tools armed will essay forever unless driven."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            text_turn(MONOLOGUE),
            tool_turn("file_write", {"path": "app.py", "content": "x = 1"}),
            text_turn("Wrote app.py."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert fake.request_count == 3
    # The live project tree is injected so the model cannot "re-plan" blindly.
    assert any("AUTO EXPLORE DONE" in t for t in user_texts(fake))
    # The monologue itself is never replayed as assistant history — that is the
    # loop fuel this breaker exists to remove.
    assert MONOLOGUE not in fake.requests[1].texts_for_role("assistant")
    assert registry.calls_to("file_write")
    assert MONOLOGUE not in answer(chunks)


@pytest.mark.asyncio
async def test_a_repeating_monologue_injects_the_project_tree_once_then_hard_nudges(
    tmp_path,
):
    """Re-injecting the same listing every step is just more context to ignore."""
    runtime = make_local_runtime(tmp_path)
    runtime._max_react_steps = 4
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM([], when_exhausted=text_turn(MONOLOGUE))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert count_user_texts(fake, "AUTO EXPLORE DONE") == 1
    assert count_user_texts(fake, "CONTINUE BUILD") >= 1
    # The fingerprint counter noticed the repeat and told the person why the
    # turn went quiet instead of silently streaming the same essay again.
    assert any("Breaking monologue loop" in e for e in events(chunks))
    assert registry.calls == []


@pytest.mark.asyncio
async def test_a_hosted_model_is_never_put_through_the_monologue_breaker(tmp_path):
    """Frontier models self-steer; the local rails would only thrash them."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM([text_turn(MONOLOGUE)], when_exhausted=text_turn("kept going"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert fake.request_count == 1
    assert not any("AUTO EXPLORE DONE" in t for t in user_texts(fake))
    assert MONOLOGUE in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_model_that_narrates_after_real_tool_work_gets_a_write_first_pack(
    tmp_path,
):
    """Local muscle is re-pointed at write tools, not merely told off."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            tool_turn("list_dir", {"path": "."}),
            text_turn("Now creating the module and wiring it up for you."),
            text_turn("Done."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, OPINION_ASK)

    assert any("Stop narrating intent" in t for t in user_texts(fake))
    # Local narration is re-driven; the hosted cap of one does not apply here.
    assert fake.request_count == 3


@pytest.mark.asyncio
async def test_a_local_scratchpad_final_keeps_agency_instead_of_disarming_tools(
    tmp_path,
):
    """Disarming here ended real builds with empty files still on disk."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "app.py", "content": "x = 1"}),
            text_turn(SCRATCHPAD),
            text_turn(SCRATCHPAD),
            text_turn("Wrote app.py with a working module."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "keep going")

    assert fake.request_count == 4
    # Local build skips the polite summary and keeps write tools on.
    assert count_user_texts(fake, "CONTINUE BUILD") >= 1
    assert count_user_texts(fake, "do not stop") >= 1
    assert "The user wants" not in answer(chunks)


# --------------------------------------------------------------------------
# tool calls faked as text, recovered, and then failing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_recovered_text_tool_call_that_fails_still_gets_a_recovery_nudge(
    tmp_path,
):
    """Recovery must not lose the failure — that is how a turn dies silently."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "list_dir",
        description="list a directory",
        results=[ToolFailure("PATH_DENIED: outside the workspace")],
    )
    fake = FakeLLM(
        [
            text_turn('<tool_call>{"name": "list_dir", "arguments": {"path": "/"}}</tool_call>'),
            text_turn("That path is not readable."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list the project directory")

    # The failure reaches the model as a tool result *and* as a recovery nudge.
    assert any("PATH_DENIED" in t for t in fake.requests[1].tool_result_texts)
    assert count_user_texts(fake, "One or more tools failed") == 1
    assert "<tool_call>" not in answer(chunks)


@pytest.mark.asyncio
async def test_a_recovered_text_tool_call_counts_as_tool_evidence(tmp_path):
    """``tools_executed_this_turn`` was bumped only in the native tool-call
    batch, never in the pseudo-recovery batch. So a tool that really ran —
    because the loop rescued it out of text markup — left the turn believing
    nothing had: the zero-tool-work driver spent its whole budget of extra
    round-trips and the person was finally told "no tools ran" about a tool
    that had just run.
    """
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            text_turn('<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'),
        ],
        when_exhausted=text_turn("The directory holds a.txt."),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list the project directory")

    assert registry.calls_to("list_dir")  # the tool really ran
    assert count_user_texts(fake, "no function calls is not done") == 0, (
        "the zero-tool driver spent round-trips on a turn where a tool ran"
    )
    assert "no tools ran" not in answer(chunks)


# --------------------------------------------------------------------------
# an answer cut by the token limit, on a step that still had tools armed
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_armed_answer_cut_by_the_token_limit_is_continued_not_restarted(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn("The first half of the report", finish_reason="length"),
            text_turn(" and the second half."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool with a=1")

    assert fake.request_count == 3
    assert any("do not restart" in t for t in user_texts(fake))
    # The cut answer is replayed as assistant text so the model can resume it.
    assert "The first half of the report" in fake.requests[2].texts_for_role("assistant")
    assert answer(chunks).endswith(" and the second half.")


#: The same sentence twice: a model stuttering inside one reply. The breaker
#: must score this as a repeat straight away, not wait for a second round.
STUTTER = (
    "I will now design the complete module layout for this project. "
    "I will now design the complete module layout for this project."
)


@pytest.mark.asyncio
async def test_a_monologue_that_stutters_inside_one_reply_counts_as_a_repeat(tmp_path):
    runtime = make_local_runtime(tmp_path)
    runtime._max_react_steps = 3
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM([], when_exhausted=text_turn(STUTTER))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert any("Breaking monologue loop" in e for e in events(chunks))


@pytest.mark.asyncio
async def test_three_distinct_monologues_are_not_yet_treated_as_a_loop(tmp_path):
    """The counterpart: without the internal stutter, three hits is not a loop."""
    runtime = make_local_runtime(tmp_path)
    runtime._max_react_steps = 3
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM([], when_exhausted=text_turn(MONOLOGUE))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert not any("Breaking monologue loop" in e for e in events(chunks))


# --------------------------------------------------------------------------
# faked tool syntax must never be the final bubble
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_markup_in_a_forced_final_answer_is_nudged_not_shipped(tmp_path):
    """Live 2026-08-13: a forced final left the chat bubble reading "tool_c"."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            Turn(
                text=(
                    "The answer is in the file.\n\n"
                    '<tool_call>{"name": "add", "arguments": {"a": 1}}</tool_call>'
                ),
                tool_calls=[tool_call("add", {"a": 1})],
            )
        ],
        when_exhausted=text_turn("The sum is 5."),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert any(
        "Do not write tool calls as text or DSML/XML" in t for t in user_texts(fake)
    )
    assert "<tool_call>" not in answer(chunks)


# --------------------------------------------------------------------------
# local build: the turn does not end on a scratchpad "final"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_local_build_refuses_to_end_on_a_scratchpad_final_up_to_its_cap(
    tmp_path,
):
    """Six refusals, then the turn ends anyway — a nudge loop needs a ceiling."""
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [tool_turn("file_write", {"path": "app.py", "content": "x = 1"})],
        when_exhausted=text_turn(SCRATCHPAD),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "keep going")

    # 1 tool round + bounded refusals + 1 round that is finally allowed
    # to end the turn. Local build keeps CONTINUE BUILD on, not a polite
    # summary that disarms tools.
    assert fake.request_count == 8
    assert count_user_texts(fake, "CONTINUE BUILD") >= 1
    assert answer(chunks).strip()


# --------------------------------------------------------------------------
# a local answer cut by the token limit
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_local_answer_cut_by_the_token_limit_is_delivered_not_continued(
    tmp_path,
):
    """Local hosts get ``max_length_continuations = 0`` on purpose.

    Each extra round on a local model dumps more hidden thinking, which is how
    a one-line summary turned into 30k characters of "Done." spam. So the cut
    answer ships as-is; it is never resumed.
    """
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    cut = "The plan has three parts, and the first"
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn(cut, finish_reason="length")],
        when_exhausted=text_turn("must not be needed"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, OPINION_ASK)

    assert fake.request_count == 2
    assert not any("do not restart" in t for t in user_texts(fake))
    assert answer(chunks).endswith(cut)


# --------------------------------------------------------------------------
# the last resort: still no tools, and the soft blocks are used up
# --------------------------------------------------------------------------


REFUSAL_MONOLOGUE = "I cannot start yet. " + MONOLOGUE


@pytest.mark.asyncio
async def test_a_local_build_that_only_ever_refuses_is_re_fed_the_tree_not_dumped_on(
    tmp_path,
):
    """A refusal is not bulldozed into tools — but a *build* still gets driven.

    ``looks_like_safety_refusal`` keeps the zero-tool-work driver out of the
    way, so this is the one route into the last-resort re-inject: the soft
    monologue blocks are spent, the reply is too short to fingerprint, and the
    loop must keep the build alive instead of dumping the refusal on the person.
    """
    runtime = make_local_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [text_turn(REFUSAL_MONOLOGUE)] * 3,
        when_exhausted=text_turn("I can't."),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "keep going")

    assert any("Still no tools from model" in e for e in events(chunks))
    # A refusal is never force-driven with the zero-tool-work nudge.
    assert not any("no function calls is not done" in t for t in user_texts(fake))
    # And the re-injection is bounded — it does not spin to the step ceiling.
    assert fake.request_count < 12
