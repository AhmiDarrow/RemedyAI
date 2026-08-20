"""How a turn survives a bad ending: continuation, host blips, and giving up well.

``remedy.core.react_loop.loop.call_llm_stream`` does not only stream a happy
answer. It also has to carry a turn across the ugly parts: an answer chopped in
half by the output-token limit, a model that keeps calling the same tool, a
provider that drops the socket after real work already landed on disk, a local
model host that has to be waited for, and a stop pressed mid-sentence.

Those are the paths where a mistake is expensive and invisible:

* an answer cut at ``max_tokens`` that gets *restarted* instead of continued
  (the person sees the first half twice and never the end);
* a repetitive monologue that gets "continued" forever;
* a host blip after twenty tool calls that ends the turn with "please resend",
  throwing away work that already happened;
* a recovery attempt that hammers a local model host that is still loading;
* a stop that loses the tool history it just built;
* a mission whose retries are exhausted but which stays "active" forever and
  keeps blocking every later turn.

So these tests are about what must be *kept*: kept history, kept tool evidence,
bounded retries, and an honest last word when nothing else worked.

Everything talks to :mod:`tests.harness.fake_llm`; nothing here opens a socket,
drives the desktop, waits on a real model host, or touches ``~/.remedy``.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.providers import clear_provider_quarantine
from remedy.core.react_loop.loop import call_llm_stream
from remedy.models import AgentConfig
from tests.harness.fake_llm import (
    FakeLLM,
    FakeSession,
    FakeToolRegistry,
    RecordedToolCall,
    ToolFailure,
    Turn,
    empty_turn,
    exception_turn,
    text_turn,
    tool_turn,
)

#: The tools-on/tools-off gate; pin it off for turns that must stay chat-only.
NO_TOOLS = "remedy.core.agent._message_wants_tools"
#: Where the loop asks the RMB supervisor whether the local model is up. Every
#: test that can reach a local binding pins this — a real call would block the
#: suite for 90s and poke the owner's model host.
WAIT_RMB = "remedy.runtime.rmb.service.wait_rmb_ready"
IS_LOCAL = "remedy.core.local_agent_optimize.is_local_binding"
NEEDS_HARNESS = "remedy.core.local_agent_optimize.needs_agent_harness"
BUILD_BODY = "remedy.core.react_loop.loop.build_step_request_body"

#: Text the loop puts in the post-blip recovery round, used to recognise it.
RECOVERY_MARKER = "Tools already ran"
#: Text the loop puts in the very last synthesis round after the step ceiling.
FINAL_SYNTHESIS_MARKER = "Using all tool results and context above"


@pytest.fixture(autouse=True)
def _clean_provider_breaker():
    """The provider circuit breaker is process-global."""
    clear_provider_quarantine()
    yield
    clear_provider_quarantine()


@pytest.fixture(autouse=True)
def _never_wait_on_a_real_model_host():
    """Fail loudly instead of blocking if a test forgets to pin the RMB wait."""
    with patch(WAIT_RMB, return_value={"ok": False, "ready": False}) as m:
        yield m


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


async def drain(runtime: BasicRuntime, message: str, **kwargs: Any) -> list[str]:
    """Run one whole turn and collect every chunk it yielded."""
    kwargs.setdefault("session_id", str(runtime.config.home_dir))
    return [chunk async for chunk in call_llm_stream(runtime, message, **kwargs)]


def answer(chunks: list[str]) -> str:
    """Only what the person sees — '@@' chunks are UI lifecycle events."""
    return "".join(c for c in chunks if not c.startswith("@@"))


def events(chunks: list[str]) -> list[str]:
    return [c for c in chunks if c.startswith("@@")]


class MarkedSession(FakeSession):
    """A :class:`FakeSession` that answers *specific* rounds by their content.

    The recovery and final-synthesis rounds are built deep inside the loop and
    their position in the request sequence depends on how many retries happened
    first. Scripting them positionally would encode today's retry counts into
    the test; matching on the nudge text the loop itself writes does not.
    """

    def __init__(
        self,
        turns: Sequence[Turn] = (),
        *,
        when_exhausted: Turn | None = None,
        marked: Sequence[tuple[str, Turn]] = (),
    ) -> None:
        super().__init__(turns, when_exhausted=when_exhausted)
        self.marked = list(marked)

    def post(  # noqa: A002 - ``json`` is aiohttp's own keyword
        self,
        url: str = "",
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        blob = jsonlib.dumps(json or {}, default=str)
        for marker, turn in self.marked:
            if marker in blob:
                self.turns.insert(0, turn)
                break
        return super().post(url, headers=headers, json=json, **kwargs)


def marked_llm(
    turns: Sequence[Turn] = (),
    *,
    when_exhausted: Turn | None = None,
    marked: Sequence[tuple[str, Turn]] = (),
) -> FakeLLM:
    fake = FakeLLM()
    fake.session = MarkedSession(
        turns, when_exhausted=when_exhausted, marked=marked
    )
    return fake


def raise_on_step(exc: BaseException, *, at_step: int):
    """Blow up inside the step loop from *at_step* on, real work done before."""
    from remedy.core.react_loop.build_request import (
        build_step_request_body as real_builder,
    )

    def _side_effect(*args: Any, **kwargs: Any):
        if int(kwargs.get("step", 0)) >= at_step:
            raise exc
        return real_builder(*args, **kwargs)

    return _side_effect


# --------------------------------------------------------------------------
# an answer chopped by max_tokens is continued, not restarted
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_answer_cut_by_max_tokens_on_a_tool_armed_round_is_continued(tmp_path):
    """Tool-armed rounds are buffered, not live-streamed, so they need their own
    continuation. Restarting here would show the person the first half twice."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn("part one", finish_reason="length"),
            text_turn(" part two"),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(NEEDS_HARNESS, return_value=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count == 3
    # The round that got cut was a buffered, tool-armed one.
    assert fake.requests[1].tools != []
    assert answer(chunks).endswith("part one part two")
    assert "part one" in fake.requests[2].texts_for_role("assistant")
    assert any(
        "Continue exactly where you stopped" in t
        for t in fake.requests[2].texts_for_role("user")
    )


@pytest.mark.asyncio
async def test_a_stuttering_answer_is_not_length_continued_into_a_longer_stutter(
    tmp_path,
):
    """A blob that already repeats itself will keep repeating if we ask for more."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    sentence = "The capital of France is Paris and it has been for centuries. "
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            text_turn(sentence * 2, finish_reason="length"),
        ],
        when_exhausted=text_turn("must not be needed"),
    )

    with fake.patch(force_tools=True), patch(NEEDS_HARNESS, return_value=True):
        chunks = await drain(runtime, "tell me about france")

    # The stutter is still delivered — it is the only answer there is — but the
    # loop does not spend another round growing it.
    assert fake.request_count == 2
    assert sentence.strip() in answer(chunks)


# --------------------------------------------------------------------------
# reasoning as the answer when the model spends its round on tool calls
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_the_last_step_tool_calls_are_dropped_and_reasoning_becomes_the_answer(
    tmp_path,
):
    """The step ceiling is a hard stop: a model that answers it with more tool
    calls must not get them run, and must not leave the person with silence."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            Turn(
                text="",
                reasoning="Two plus two is four.",
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call_0",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a": 2}'},
                    }
                ],
                finish_reason="stop",
            )
        ],
        when_exhausted=text_turn("must not be needed"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "what is 2+2")

    assert fake.request_count == 1
    assert registry.calls == []
    assert "@@thinking:Two plus two is four." in chunks
    assert answer(chunks) == "Two plus two is four."


@pytest.mark.asyncio
async def test_a_reasoning_only_answer_cut_by_max_tokens_is_continued_not_restarted(
    tmp_path,
):
    """A reasoner that spends the round thinking, hits the token cap, and emits
    a stray tool call still owes the person the rest of its answer."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    stray_call = {
        "index": 0,
        "id": "call_0",
        "type": "function",
        "function": {"name": "add", "arguments": "{}"},
    }
    fake = FakeLLM(
        [
            empty_turn(),
            Turn(
                text="",
                reasoning="Half the answer.",
                tool_calls=[stray_call],
                finish_reason="length",
            ),
            text_turn(" The rest."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "explain it")

    assert fake.request_count == 3
    assert answer(chunks) == "Half the answer. The rest."
    # The half answer is replayed so the model can resume rather than restart.
    assert "Half the answer." in fake.requests[2].texts_for_role("assistant")
    assert any(
        "Do not restart or summarize" in t
        for t in fake.requests[2].texts_for_role("user")
    )


# --------------------------------------------------------------------------
# a host blip *after* real tool work: finish the turn, never ask for a resend
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_host_blip_after_tools_is_finished_by_a_short_recovery_round(tmp_path):
    """Tools already changed the world, so ending the turn with "please resend"
    throws that away. The loop buys itself one small round to wrap up instead."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("- add ran, result 5\n- next: nothing"))],
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]
    assert any("Host blip after tools" in e for e in events(chunks))
    reply = answer(chunks)
    assert "- add ran, result 5" in reply
    # It really recovered — the give-up banner must not also fire.
    assert "Host recovered tool progress above" not in "".join(chunks)
    # The recovery round is deliberately cheap: no tools, no streaming, capped.
    recovery = fake.requests[-1]
    assert recovery.stream is False
    assert recovery.tools == []
    assert int(recovery.body["max_tokens"]) <= 768


@pytest.mark.asyncio
async def test_a_recovery_round_that_keeps_5xxing_is_tried_three_times_then_synthesised(
    tmp_path,
):
    """The retry budget is a budget — and the turn still ends with the work."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, Turn(status=503, error_body="host still down"))],
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    recovery_rounds = [r for r in fake.requests if r.mentions(RECOVERY_MARKER)]
    assert len(recovery_rounds) == 3
    whole = "".join(chunks)
    assert "Host recovered tool progress above" in whole
    # The dead host error body is never presented as if it were an answer.
    assert "host still down" not in whole


@pytest.mark.asyncio
async def test_a_stop_pressed_during_the_blip_cancels_recovery_instead_of_retrying(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    blown = {"yes": False}

    def _blow(*args: Any, **kwargs: Any):
        from remedy.core.react_loop.build_request import (
            build_step_request_body as real_builder,
        )

        if int(kwargs.get("step", 0)) >= 1:
            blown["yes"] = True  # the person hit stop as the socket died
            raise OSError("Server disconnected")
        return real_builder(*args, **kwargs)

    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("must never be asked for"))],
    )

    with fake.patch(force_tools=True), patch(BUILD_BODY, side_effect=_blow), patch(
        "remedy.core.turn_context.is_turn_aborted",
        side_effect=lambda *a, **k: blown["yes"],
    ):
        chunks = await drain(runtime, "add one")

    assert not any(r.mentions(RECOVERY_MARKER) for r in fake.requests)
    assert "Host recovered tool progress above" in "".join(chunks)


@pytest.mark.asyncio
async def test_a_local_host_is_waited_for_before_each_recovery_round(tmp_path):
    """Re-POSTing at a model host that is still loading just burns the budget."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("must never be asked for"))],
    )
    waits = MagicMock(return_value={"ok": False, "ready": False})

    with fake.patch(force_tools=True), patch(IS_LOCAL, return_value=True), patch(
        WAIT_RMB, waits
    ), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    # Host never came back: no round was posted at it, and the budget is spent.
    assert waits.call_count == 3
    assert not any(r.mentions(RECOVERY_MARKER) for r in fake.requests)
    assert "Host recovered tool progress above" in "".join(chunks)


@pytest.mark.asyncio
async def test_a_local_host_that_comes_back_gets_the_recovery_round(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("- add ran; nothing left to do"))],
    )
    waits = MagicMock(return_value={"ok": True, "ready": True})

    with fake.patch(force_tools=True), patch(IS_LOCAL, return_value=True), patch(
        WAIT_RMB, waits
    ), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    assert waits.call_count == 1
    assert "- add ran; nothing left to do" in answer(chunks)


@pytest.mark.asyncio
async def test_a_5xx_after_tools_also_earns_a_recovery_round_not_only_a_disconnect(
    tmp_path,
):
    """The blip arrives as a status code as often as it arrives as a reset."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("- add ran, result 5"))],
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY,
        side_effect=raise_on_step(RuntimeError("upstream HTTP 503"), at_step=1),
    ):
        chunks = await drain(runtime, "add one")

    assert "- add ran, result 5" in answer(chunks)


@pytest.mark.asyncio
async def test_a_plain_crash_after_tools_is_not_retried_it_is_synthesised(tmp_path):
    """Only blips are worth a retry — a bug in our own code is not."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("must never be asked for"))],
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY, side_effect=raise_on_step(RuntimeError("kaboom"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    assert not any(r.mentions(RECOVERY_MARKER) for r in fake.requests)
    whole = "".join(chunks)
    assert "Host recovered tool progress above" in whole
    assert "kaboom" not in whole


# --------------------------------------------------------------------------
# a model that keeps calling the same tool
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_model_stuck_on_one_tool_call_is_nudged_a_bounded_number_of_times(
    tmp_path,
):
    """Feeding cached results plus a "try something else" nudge is the recovery.
    It is capped: past the cap the loop stops nudging and drives an answer, so a
    stuck model cannot spin the turn until the step ceiling."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1})] * 12,
        when_exhausted=text_turn("It is 5."),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    # The tool itself ran exactly once, however many times it was asked for.
    assert len(registry.calls_to("add")) == 1
    # Nudges accumulate in the history, so the last request carries them all.
    nudges = [
        t
        for t in fake.requests[-1].texts_for_role("user")
        if "Do not repeat them" in t
    ]
    assert len(nudges) == 7
    # The repeat is still answered from cache — an unpaired tool call is an
    # HTTP 400 on the next request.
    assert fake.requests[-1].tool_result_texts.count("5") >= 2
    assert "It is 5." in answer(chunks)


# --------------------------------------------------------------------------
# what gets attached to the next step after a tool batch
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_native_screenshot_is_attached_to_the_next_step_and_announced(tmp_path):
    """A vision model that never receives the screenshot is flying blind on
    exactly the turns (pygame, custom-drawn UI) that need eyes."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    shot = {"role": "user", "content": "[screenshot of the running app]"}
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("I can see it.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.computer.vision_observe.flush_native_screenshots",
        return_value=shot,
    ):
        chunks = await drain(runtime, "add one")

    assert "[screenshot of the running app]" in fake.requests[1].texts_for_role("user")
    assert any("screenshot attached" in e for e in events(chunks))


@pytest.mark.asyncio
async def test_three_one_at_a_time_reads_earn_a_single_batch_up_nudge(tmp_path):
    """Serial exploring is the slow failure mode. It is nudged once — nudging
    every step would bury the real instructions."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            tool_turn("list_dir", {"path": "one"}),
            tool_turn("list_dir", {"path": "two"}),
            tool_turn("list_dir", {"path": "three"}),
            tool_turn("list_dir", {"path": "four"}),
            text_turn("Explored."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.logging.hot_debug_enabled", return_value=True
    ):
        await drain(runtime, "look around the project")

    nudged = [
        i
        for i, r in enumerate(fake.requests)
        if any("You are calling tools one-at-a-time" in t for t in r.texts_for_role("user"))
    ]
    # Fires only after the third serial batch, and only once.
    assert nudged and nudged[0] == 3
    assert len(nudged) == len(fake.requests) - 3


@pytest.mark.asyncio
async def test_a_blocked_empty_file_write_rearms_write_tools_instead_of_giving_up(
    tmp_path,
):
    """An empty/spam write is refused by the tool layer. If the loop then let
    the turn end, the person would be told a file was written that is not."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "file_write",
        description="write a file",
        results=[ToolFailure("EMPTY_SOURCE_WRITE: blank content refused")],
    )
    fake = FakeLLM(
        [
            tool_turn("file_write", {"path": "notes.txt", "content": ""}),
            text_turn("Wrote it properly this time."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.logging.hot_debug_enabled", return_value=True
    ):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    nudges = fake.requests[1].texts_for_role("user")
    assert any("EMPTY/SPAM file_write blocked" in t for t in nudges)
    assert any("was **kept** (not wiped)" in t for t in nudges)
    # Tools must still be armed — the model has to be able to write for real.
    assert fake.requests[1].tools != []
    assert "Wrote it properly this time." in answer(chunks)


# --------------------------------------------------------------------------
# missions: a turn must not leave one wedged
# --------------------------------------------------------------------------


def _save_mission(home: str, **fields: Any):
    from remedy.core.mission import Mission, MissionStore

    mission = Mission(
        id="22222222-2222-4222-8222-222222222222",
        goal="build the thing",
        session_id=home,
        status="active",
        **fields,
    )
    MissionStore(home).save(mission)
    return mission


@pytest.mark.asyncio
async def test_a_mission_past_its_retry_limit_is_failed_not_left_active_forever(
    tmp_path,
):
    """An 'active' mission blocks every later turn from finishing. Once its
    retries are spent it has to be closed out, not carried."""
    from remedy.core.mission import MissionStep, MissionStore

    runtime = make_runtime(tmp_path)
    home = str(tmp_path / "home")
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    _save_mission(
        home,
        retries=5,
        max_retries=5,
        steps=[MissionStep(id="s1", title="step one", status="done")],
    )
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("Done.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, "add one")

    assert MissionStore(home).latest(home).status == "failed"


@pytest.mark.asyncio
async def test_a_mission_whose_steps_are_done_but_unverified_is_sent_back_to_verify(
    tmp_path,
):
    """Every step ticked is not evidence the thing works — verify is."""
    from remedy.core.mission import MissionStep

    runtime = make_runtime(tmp_path)
    home = str(tmp_path / "home")
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    _save_mission(
        home,
        verify_command="pytest -q",
        verify_status=None,
        steps=[
            MissionStep(id="s1", title="step one", status="done"),
            MissionStep(id="s2", title="step two", status="skipped"),
        ],
    )
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("Verified.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, "add one")

    gate = [
        t
        for r in fake.requests
        for t in r.texts_for_role("user")
        if "[Mission gate]" in t
    ]
    assert gate and "pytest -q" in gate[0]
    # Injected once, not once per step.
    assert len(gate) == len(fake.requests) - 1


@pytest.mark.asyncio
async def test_a_buffered_tool_armed_round_that_finishes_normally_ends_the_turn(
    tmp_path,
):
    """The mirror of the continuation case: no length cap, so no extra round."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("It is 5.")],
        when_exhausted=text_turn("must not be needed"),
    )

    with fake.patch(force_tools=True), patch(NEEDS_HARNESS, return_value=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count == 2
    assert answer(chunks).endswith("It is 5.")


@pytest.mark.asyncio
async def test_an_empty_round_on_a_work_turn_is_re_driven_with_tools_still_armed(
    tmp_path,
):
    """A round that produced nothing at all is not an answer to a work request.
    Disarming tools here would guarantee the work never happens."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=empty_turn())

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    assert fake.request_count > 1
    assert all(r.tools for r in fake.requests)
    reply = answer(chunks)
    assert reply.strip()
    # It never claims the file exists.
    assert "notes.txt" not in reply or "no tools ran" in reply


# --------------------------------------------------------------------------
# the step ceiling and the last-ditch synthesis round
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_step_wall_checkpoint_fires_when_the_ceiling_is_reached(tmp_path):
    """The checkpoint that protects work at the riskiest moment of a turn.

    It used to sit at the tail of the tool-batch section, guarded by
    ``is_final_step``. A tool batch is only reached when ``force_answer`` is
    False, and at the ceiling ``force_answer`` is always True — while the one
    path that clears it clears ``is_final_step`` in the same statement. So the
    branch was unreachable and the ceiling checkpoint was never written. It
    fires on arrival at the ceiling now, once per turn.
    """
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    checkpoints: list[dict[str, Any]] = []

    def _checkpoint(**kwargs: Any):
        checkpoints.append(kwargs)
        return {"id": "ckpt-1"}

    runtime._maybe_auto_checkpoint = _checkpoint  # type: ignore[method-assign]
    fake = FakeLLM([tool_turn("add", {"a": 1})], when_exhausted=empty_turn())

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]
    reasons = [c.get("reason") for c in checkpoints]
    assert "step_wall" in reasons, "the ceiling checkpoint was still never written"
    assert reasons.count("step_wall") == 1, "the ceiling checkpoint fired twice"
    assert "@@checkpoint" in chunks


@pytest.mark.asyncio
async def test_a_turn_that_never_reaches_the_ceiling_is_not_checkpointed_for_it(
    tmp_path,
):
    """The wall checkpoint is forced, so firing it on an ordinary short turn
    would write a snapshot on every exchange."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 8
    FakeToolRegistry().install(runtime)
    checkpoints: list[dict[str, Any]] = []
    runtime._maybe_auto_checkpoint = lambda **kw: checkpoints.append(kw) or None

    fake = FakeLLM([text_turn("A short answer.")])
    with fake.patch():
        chunks = await drain(runtime, "hello")

    assert "step_wall" not in [c.get("reason") for c in checkpoints]
    assert "@@checkpoint" not in chunks


@pytest.mark.asyncio
async def test_the_last_ditch_round_hands_over_its_thinking_when_content_is_empty(
    tmp_path,
):
    """A reasoner that thinks its whole answer must not be reduced to silence."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=empty_turn(),
        marked=[
            (
                FINAL_SYNTHESIS_MARKER,
                Turn(text="", reasoning="I ran add and it returned 5."),
            )
        ],
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert "I ran add and it returned 5." in answer(chunks)
    # The deterministic fallback summary is not also appended.
    assert "Tool results" not in answer(chunks)


@pytest.mark.asyncio
async def test_a_last_ditch_round_that_also_dies_still_leaves_the_tool_summary(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=empty_turn(),
        marked=[
            (
                FINAL_SYNTHESIS_MARKER,
                exception_turn(aiohttp.ClientConnectionError("Server disconnected")),
            )
        ],
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    reply = answer(chunks)
    assert reply.strip()
    assert "Tool results" in reply
    assert "Server disconnected" not in reply


@pytest.mark.asyncio
async def test_a_stop_during_the_last_ditch_round_keeps_the_tool_history(tmp_path):
    from remedy.core.react_loop import loop as loop_mod

    real_consume = loop_mod.consume_llm_http_response

    def _consume(*args: Any, **kwargs: Any):
        blob = jsonlib.dumps(kwargs.get("body") or {}, default=str)
        if FINAL_SYNTHESIS_MARKER in blob:
            raise asyncio.CancelledError()
        return real_consume(*args, **kwargs)

    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM([tool_turn("add", {"a": 1})], when_exhausted=empty_turn())

    with fake.patch(force_tools=True), patch.object(
        loop_mod, "consume_llm_http_response", _consume
    ):
        chunks = await drain(runtime, "add one")

    assert "@@aborted\n" in chunks
    assert "kept" in answer(chunks)


@pytest.mark.asyncio
async def test_a_body_that_fails_sanitization_is_never_posted_to_the_provider(
    tmp_path,
):
    """Fail closed: an unsanitizable body must abort the call, not go out."""
    from remedy.core.react_loop import loop as loop_mod

    real_sanitize = loop_mod.sanitize_chat_body

    def _sanitize(body: dict[str, Any], **kwargs: Any):
        if FINAL_SYNTHESIS_MARKER in jsonlib.dumps(body, default=str):
            raise ValueError("unknown provider field")
        return real_sanitize(body, **kwargs)

    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM([tool_turn("add", {"a": 1})], when_exhausted=empty_turn())

    with fake.patch(force_tools=True), patch.object(
        loop_mod, "sanitize_chat_body", _sanitize
    ):
        chunks = await drain(runtime, "add one")

    assert not any(r.mentions(FINAL_SYNTHESIS_MARKER) for r in fake.requests)
    whole = "".join(chunks)
    # The person gets the tool progress, not the sanitizer failure.
    assert "unknown provider field" not in whole
    assert "sanitization failed" not in whole
    assert answer(chunks).strip()


# --------------------------------------------------------------------------
# a stop pressed mid-turn
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stop_after_tools_ran_promises_the_tool_history_is_kept(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1})], when_exhausted=text_turn("must not be needed")
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY, side_effect=raise_on_step(asyncio.CancelledError(), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    assert chunks[-1] == "@@aborted\n"
    note = answer(chunks)
    assert "kept" in note and "continue" in note


@pytest.mark.asyncio
@pytest.mark.parametrize("checks_before_stop", [1, 2])
async def test_a_stop_is_honoured_at_every_gate_of_the_recovery_round(
    tmp_path, checks_before_stop
):
    """The recovery round has abort gates before the POST and before its answer
    is used. Either way the answer must not be delivered as if nothing happened."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    blown = {"yes": False}
    seen = {"n": 0}

    def _blow(*args: Any, **kwargs: Any):
        from remedy.core.react_loop.build_request import (
            build_step_request_body as real_builder,
        )

        if int(kwargs.get("step", 0)) >= 1:
            blown["yes"] = True
            raise OSError("Server disconnected")
        return real_builder(*args, **kwargs)

    def _aborted(*args: Any, **kwargs: Any) -> bool:
        if not blown["yes"]:
            return False
        seen["n"] += 1
        return seen["n"] > checks_before_stop

    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("- add ran, result 5"))],
    )

    with fake.patch(force_tools=True), patch(BUILD_BODY, side_effect=_blow), patch(
        "remedy.core.turn_context.is_turn_aborted", side_effect=_aborted
    ):
        chunks = await drain(runtime, "add one")

    assert "- add ran, result 5" not in answer(chunks)
    assert "Host recovered tool progress above" in "".join(chunks)


@pytest.mark.asyncio
async def test_a_stop_landing_while_the_local_host_is_waited_for_cancels_recovery(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    blown = {"yes": False}
    stopped = {"yes": False}

    def _blow(*args: Any, **kwargs: Any):
        from remedy.core.react_loop.build_request import (
            build_step_request_body as real_builder,
        )

        if int(kwargs.get("step", 0)) >= 1:
            blown["yes"] = True
            raise OSError("Server disconnected")
        return real_builder(*args, **kwargs)

    def _wait(*args: Any, **kwargs: Any):
        stopped["yes"] = True  # the person gave up while the host was starting
        return {"ok": True, "ready": True}

    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, text_turn("must never be asked for"))],
    )

    with fake.patch(force_tools=True), patch(IS_LOCAL, return_value=True), patch(
        WAIT_RMB, side_effect=_wait
    ), patch(BUILD_BODY, side_effect=_blow), patch(
        "remedy.core.turn_context.is_turn_aborted",
        side_effect=lambda *a, **k: blown["yes"] and stopped["yes"],
    ):
        chunks = await drain(runtime, "add one")

    assert not any(r.mentions(RECOVERY_MARKER) for r in fake.requests)
    assert "Host recovered tool progress above" in "".join(chunks)


@pytest.mark.asyncio
async def test_a_recovery_round_that_itself_explodes_does_not_escape_the_turn(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[
            (
                RECOVERY_MARKER,
                exception_turn(aiohttp.ClientConnectionError("still down")),
            )
        ],
    )

    with fake.patch(force_tools=True), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    assert len([r for r in fake.requests if r.mentions(RECOVERY_MARKER)]) == 3
    whole = "".join(chunks)
    assert "Host recovered tool progress above" in whole
    assert "still down" not in whole


@pytest.mark.asyncio
async def test_even_a_broken_fallback_synthesis_still_explains_the_lost_connection(
    tmp_path,
):
    """Belt and braces: if the last-resort summary raises, the turn still ends
    with words rather than propagating the exception to the caller."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = marked_llm(
        [tool_turn("add", {"a": 1})],
        when_exhausted=text_turn("no other round should happen"),
        marked=[(RECOVERY_MARKER, Turn(status=503, error_body="host still down"))],
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.react_turn.synthesize_from_tools",
        side_effect=RuntimeError("summary builder broke"),
    ), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=1)
    ):
        chunks = await drain(runtime, "add one")

    reply = answer(chunks)
    assert "Connection to the model was lost mid-turn" in reply
    assert "summary builder broke" not in reply


# --------------------------------------------------------------------------
# a lost connection with no tool work behind it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lost_local_model_is_waited_for_before_the_turn_gives_up(tmp_path):
    """On a local binding the host is usually just restarting, so it is worth
    one wait before telling the person the turn is dead."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    waits = MagicMock(return_value={"ok": False, "ready": False})
    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        IS_LOCAL, return_value=True
    ), patch(WAIT_RMB, waits), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=0)
    ):
        chunks = await drain(runtime, "say hello")

    assert waits.call_count == 1
    assert any("checking local host" in e for e in events(chunks))
    reply = answer(chunks)
    assert "Connection to the model was lost mid-turn" in reply
    assert "History is intact" in reply


@pytest.mark.asyncio
async def test_a_stop_while_waiting_for_the_local_host_ends_the_turn_as_aborted(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    blown = {"yes": False}

    def _blow(*args: Any, **kwargs: Any):
        blown["yes"] = True
        raise OSError("Server disconnected")

    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        IS_LOCAL, return_value=True
    ), patch(BUILD_BODY, side_effect=_blow), patch(
        "remedy.core.turn_context.is_turn_aborted",
        side_effect=lambda *a, **k: blown["yes"],
    ):
        chunks = await drain(runtime, "say hello")

    assert chunks[-1] == "@@aborted\n"
    assert "History is intact" in answer(chunks)


@pytest.mark.asyncio
async def test_a_dead_sleev_gateway_is_named_so_the_person_can_switch_it_off(tmp_path):
    """A refused connection to the local gateway port looks like a dead model
    unless the turn says which knob to turn."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        BUILD_BODY,
        side_effect=raise_on_step(
            OSError("Cannot connect to host 127.0.0.1:17321"), at_step=0
        ),
    ):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "Sleev" in reply
    assert "Settings" in reply
    assert "17321" in reply


@pytest.mark.asyncio
async def test_a_promise_hidden_only_in_reasoning_still_rearms_tools(tmp_path):
    """The model dumped junk markup as its answer — so there is no answer text
    at all — and put "I will use tools now" only in its thinking. That promise
    is the sole evidence the turn is unfinished; dropping it would end the turn
    on a blank."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    junk = '<tool_call>{"name": "list_dir", "argum'
    fake = FakeLLM(
        [
            tool_turn("list_dir", {"path": "."}),
            # Two rounds spend the incomplete-markup nudge budget.
            Turn(text=junk, finish_reason="stop"),
            Turn(text=junk, finish_reason="stop"),
            Turn(text=junk, reasoning="I will use tools now.", finish_reason="stop"),
            text_turn("The directory holds a.txt."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list the project directory")

    rearmed = [
        r
        for r in fake.requests
        if any(
            "Do not only *say* you will use tools" in t
            for t in r.texts_for_role("user")
        )
    ]
    assert rearmed, "the promise in reasoning was dropped"
    assert rearmed[0].tools != []
    # The junk itself is never shown to the person.
    assert "<tool_call>" not in answer(chunks)
    assert "The directory holds a.txt." in answer(chunks)


@pytest.mark.asyncio
async def test_build_engine_events_from_a_tool_batch_reach_the_person(tmp_path):
    """Syntax-gate and auto-verify results are the only signal a build is red."""

    async def _engine(**_kwargs: Any):
        yield "@@status:Build gate — syntax check failed\n"
        yield ""

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("Done.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.react_loop.loop.apply_build_engine_after_batch", _engine
    ):
        chunks = await drain(runtime, "add one")

    assert "@@status:Build gate — syntax check failed\n" in chunks
    assert "Done." in answer(chunks)


@pytest.mark.asyncio
async def test_a_broken_debug_logging_switch_never_breaks_the_turn(tmp_path):
    """Diagnostics are best-effort. A turn that dies because hot debug logging
    threw would be a self-inflicted outage on the machine trying to debug."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "list_dir",
        description="list a directory",
        results=["a.txt", "b.txt", ToolFailure("boom while listing")],
    )
    fake = FakeLLM(
        [
            tool_turn("list_dir", {"path": "one"}),
            tool_turn("list_dir", {"path": "two"}),
            tool_turn("list_dir", {"path": "three"}),
            text_turn("Explored."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True), patch(
        "remedy.core.logging.hot_debug_enabled", side_effect=RuntimeError("no logger")
    ):
        chunks = await drain(runtime, "look around the project")

    # Both nudges still landed even though their debug logging blew up.
    last = fake.requests[-1].texts_for_role("user")
    assert any("You are calling tools one-at-a-time" in t for t in last)
    assert any("boom while listing" in t for t in fake.requests[-1].tool_result_texts)
    assert "Explored." in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_host_probe_that_itself_fails_still_explains_the_lost_turn(
    tmp_path,
):
    """The readiness probe is a courtesy. If it explodes, the person must still
    be told the connection died — not get a bare traceback or silence."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        IS_LOCAL, return_value=True
    ), patch(WAIT_RMB, side_effect=RuntimeError("supervisor is not running")), patch(
        BUILD_BODY, side_effect=raise_on_step(OSError("Server disconnected"), at_step=0)
    ):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "Connection to the model was lost mid-turn" in reply
    assert "supervisor is not running" not in reply


@pytest.mark.asyncio
async def test_a_turn_that_produced_nothing_at_all_still_ends_with_words(tmp_path):
    """No tool ran, no text was ever written, and even the last-ditch round came
    back empty. The one thing the turn must not do is end silently — an empty
    assistant bubble reads as a crash and loses the thread."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 2
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=empty_turn())

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    assert registry.calls == []
    reply = answer(chunks)
    assert reply.strip()
    assert "continue" in reply
    # It never pretends the file was written.
    assert "notes.txt" not in reply
