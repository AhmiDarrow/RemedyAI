"""The ReAct turn loop: what it must refuse, retry, and never swallow.

``remedy.core.react_loop.loop.call_llm_stream`` is the single path every chat
turn takes. It decides whether tools are armed, whether a provider error is
fatal or worth retrying, whether a model reply counts as a finished answer,
and what the person sees when nothing worked. If it is wrong the failures are
the expensive kind: a turn that silently ends with no answer, a build that
stops after a prose promise and never touches disk, an API key echoed back
into the chat bubble, or a 404 that spins forever instead of saying "that
model does not exist".

So these tests lean on the negative properties:

* an unconfigured or aborted turn must not open an HTTP request at all;
* a work request answered with prose and zero tool calls is never "done";
* a genuine refusal must not be bulldozed by the force-a-tool driver;
* a fatal provider error must stop once, and a transient one must retry;
* provider error bodies are redacted before they reach the person;
* every exit path — including the fatal ones — still runs post-turn cleanup.

Everything talks to :mod:`tests.harness.fake_llm`; nothing here opens a
socket, drives the desktop, or touches ``~/.remedy``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.providers import clear_provider_quarantine, record_provider_error
from remedy.core.react_loop.loop import (
    _browse_tool_ok,
    _stopped_note,
    call_llm_stream,
)
from remedy.models import AgentConfig
from tests.harness.fake_llm import (
    FakeLLM,
    FakeToolRegistry,
    RecordedToolCall,
    ToolFailure,
    Turn,
    empty_turn,
    error_turn,
    exception_turn,
    raw_turn,
    text_turn,
    tool_turn,
    tools_turn,
    truncated_turn,
)

#: The tools-on/tools-off gate; pin it off for turns that must stay chat-only.
NO_TOOLS = "remedy.core.agent._message_wants_tools"


@pytest.fixture(autouse=True)
def _clean_provider_breaker():
    """The provider circuit breaker is process-global.

    A 500 left behind by one test would quarantine the provider for the next.
    """
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
    # Safety ceiling well above every path exercised here: a loop that starts
    # running away fails on the exhausted script instead of hanging the suite.
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


# --------------------------------------------------------------------------
# _browse_tool_ok — the pre-navigate verdict parser
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"ok": true, "user_visible": true}', (True, False)),
        ('{"ok": false, "user_visible": true}', (False, True)),
        # rail_failed overrides an optimistic ok flag.
        ('{"ok": true, "detail": "rail_failed"}', (False, True)),
        # Not valid JSON, but the ok flag is still legible.
        ('{"ok": true, "trunc', (True, False)),
        ('{"ok":false', (False, True)),
        ("", (False, False)),
        ("plain prose with no verdict at all", (False, False)),
    ],
)
def test_a_navigate_result_is_read_from_its_ok_flag(body, expected):
    assert _browse_tool_ok(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        '{"success": true, "user_visible": true}',
        "the navigation was unsuccessful",
        '{"status": "user_visible"}',
    ],
)
def test_success_and_user_visible_are_never_mistaken_for_a_successful_navigate(body):
    """``success`` matches ``unsuccessful``; ``user_visible`` is on failures too."""
    assert _browse_tool_ok(body) == (False, False)


@pytest.mark.parametrize(
    "body",
    [
        '{"ok": true}',
        '{"ok": false}',
        '{"ok": true, "x": "rail_failed"}',
        "",
        "no verdict",
    ],
)
def test_a_navigate_verdict_is_never_both_success_and_failure(body):
    ok, failed = _browse_tool_ok(body)

    assert not (ok and failed)


# --------------------------------------------------------------------------
# _stopped_note — what a stopped turn leaves in the transcript
# --------------------------------------------------------------------------


def test_a_stop_after_tools_promises_the_tool_history_is_kept():
    note = _stopped_note(True)

    assert "kept" in note and "history" in note.lower()
    assert "continue" in note


def test_a_stop_before_any_tool_says_no_final_answer_was_written():
    note = _stopped_note(False)

    assert "before a final answer" in note
    assert "History is intact" in note


# --------------------------------------------------------------------------
# guards that must fire before a single byte goes out
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unconfigured_provider_is_explained_instead_of_being_dialled(tmp_path):
    """No key and no proxy cannot chat — say so, do not produce a raw 401."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.llm_binding.binding_looks_unconfigured", return_value=True
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 0
    reply = answer(chunks)
    assert "openai" in reply
    assert "Settings" in reply


@pytest.mark.asyncio
async def test_a_turn_aborted_before_it_starts_never_calls_the_provider(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.turn_context.is_turn_aborted", return_value=True
    ):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 0
    # The @@aborted event alone never lands in the saved transcript, so a
    # durable note has to be yielded next to it.
    assert "@@aborted\n" in chunks
    assert "History is intact" in answer(chunks)


# --------------------------------------------------------------------------
# the ordinary shapes of a turn
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_plain_answer_takes_exactly_one_round_trip(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Hello there.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert answer(chunks) == "Hello there."
    # Chat-only turns must not arm tools.
    assert fake.last_request.tools == []


@pytest.mark.asyncio
async def test_a_tool_turn_runs_the_tool_then_feeds_its_result_back(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add two numbers", results=[{"sum": 5}])
    fake = FakeLLM([tool_turn("add", {"a": 2, "b": 3}), text_turn("The sum is 5.")])

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the add tool with a=2 b=3")

    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 2, "b": 3})]
    assert fake.request_count == 2
    assert any("sum" in t for t in fake.requests[1].tool_result_texts)
    assert answer(chunks).endswith("The sum is 5.")


@pytest.mark.asyncio
async def test_several_tool_calls_in_one_turn_all_run_and_all_report_back(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("alpha", description="first", results=["A"])
    registry.add("beta", description="second", results=["B"])
    fake = FakeLLM(
        [tools_turn([("alpha", {"x": 1}), ("beta", {"y": 2})]), text_turn("Both ran.")]
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run alpha and beta")

    assert registry.calls == [
        RecordedToolCall("alpha", {"x": 1}),
        RecordedToolCall("beta", {"y": 2}),
    ]
    # One tool message per call id — a dropped pairing is an HTTP 400 later.
    assert sorted(fake.requests[1].tool_result_texts) == ["A", "B"]
    assert "Both ran." in answer(chunks)


@pytest.mark.asyncio
async def test_a_tool_call_repeated_verbatim_is_answered_from_cache_not_rerun(tmp_path):
    """Re-running the identical call is wasted work — and can be destructive."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=[{"sum": 5}])
    fake = FakeLLM(
        [
            tool_turn("add", {"a": 1}),
            tool_turn("add", {"a": 1}),
            text_turn("Done, it is 5."),
        ]
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert len(registry.calls_to("add")) == 1
    assert "Done, it is 5." in answer(chunks)
    # The looped call still gets a tool message back (pairing) and a nudge.
    assert any(
        "already ran" in t for t in fake.requests[2].steering_texts()
    )


@pytest.mark.asyncio
async def test_a_failing_tool_does_not_end_the_turn_it_gets_a_recovery_nudge(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("boom", description="explodes", results=[ToolFailure("disk on fire")])
    fake = FakeLLM([tool_turn("boom", {}), text_turn("The tool failed; here is why.")])

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the boom tool")

    # The error text is handed back to the model rather than swallowed.
    assert any("disk on fire" in t for t in fake.requests[1].tool_result_texts)
    nudges = fake.requests[1].steering_texts()
    assert any("tools failed" in t.lower() for t in nudges)
    assert "The tool failed" in answer(chunks)


@pytest.mark.asyncio
async def test_provider_reported_usage_is_forwarded_as_a_usage_event(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("hi", usage={"prompt_tokens": 10, "completion_tokens": 4})]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    usage = [e for e in events(chunks) if e.startswith("@@usage:")]
    assert len(usage) == 1
    assert '"prompt_tokens":10' in usage[0]
    assert '"completion_tokens":4' in usage[0]


# --------------------------------------------------------------------------
# plan mode and explicit stop — things the loop must leave alone
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_mode_arms_no_tools_on_the_request(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["written"])
    fake = FakeLLM([text_turn("Here is the plan.")])

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "write a file called x", plan_mode=True)

    assert fake.last_request.tools == []
    assert fake.last_request.tool_choice is None
    assert "Here is the plan." in answer(chunks)


@pytest.mark.asyncio
async def test_a_user_who_says_stop_is_not_driven_back_into_tools(tmp_path):
    """A bare stop request looks like work by shape — take the no anyway."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([text_turn("Stopped.")], when_exhausted=text_turn("kept going"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "stop")

    assert fake.request_count == 1
    assert registry.calls == []
    assert answer(chunks) == "Stopped."


@pytest.mark.asyncio
async def test_stop_cancels_the_active_mission_rather_than_leaving_it_running(tmp_path):
    from remedy.core.mission import Mission, MissionStore

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    home = str(tmp_path / "home")
    store = MissionStore(home)
    store.save(
        Mission(
            id="11111111-1111-4111-8111-111111111111",
            goal="build the thing",
            session_id=home,
            status="active",
        )
    )
    assert MissionStore(home).latest(home).status == "active"
    fake = FakeLLM([text_turn("Stopped.")], when_exhausted=text_turn("kept going"))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        await drain(runtime, "stop")

    assert MissionStore(home).latest(home).status == "cancelled"


# --------------------------------------------------------------------------
# work with zero tool evidence is never a finished turn
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_work_request_answered_with_a_promise_is_driven_back_to_tools(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM(
        [text_turn("I will create the file for you shortly.")],
        when_exhausted=text_turn("Still thinking about creating it."),
    )

    with fake.patch(force_tools=True):
        await drain(runtime, "create a file called notes.txt in the project")

    assert fake.request_count > 1
    # Every re-ask keeps the tool schemas armed — a drive that disarms tools
    # can never be satisfied.
    assert all(r.tools for r in fake.requests)
    assert any(
        "no function calls is not done" in t
        for t in fake.last_request.steering_texts()
    )


@pytest.mark.asyncio
async def test_a_work_request_that_never_ran_a_tool_ends_by_saying_so(tmp_path):
    """The turn must not present a prose stub as if the work had been done."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("I will create the file shortly."))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    assert registry.calls == []
    reply = answer(chunks)
    assert "no tools ran" in reply
    assert "I will create the file shortly." not in reply


@pytest.mark.asyncio
async def test_a_refusal_is_delivered_verbatim_and_never_forced_into_tools(tmp_path):
    """A model that said no must not be re-asked until it invents a tool call."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    refusal = "I cannot help with that request."
    fake = FakeLLM([], when_exhausted=text_turn(refusal))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    reply = answer(chunks)
    assert refusal in reply
    assert "no tools ran" not in reply
    assert registry.calls == []
    assert not any(
        "no function calls is not done" in t
        for r in fake.requests
        for t in r.steering_texts()
    )


@pytest.mark.asyncio
async def test_prose_after_a_tool_actually_ran_is_accepted_as_the_answer(tmp_path):
    """Tool evidence exists, so the summary is a real final — not a stub."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("I'll get right on it.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count == 2
    assert "I'll get right on it." in answer(chunks)


# --------------------------------------------------------------------------
# provider errors: fatal once, transient retried, never leaked
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_model_stops_after_one_attempt_and_names_the_model(tmp_path):
    """404 cannot succeed on retry — soft-retry spam just looks like a hang."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [], when_exhausted=error_turn(404, "model_not_found: the model does not exist")
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    reply = answer(chunks)
    assert "fake-model" in reply and "openai" in reply
    assert "not a tool-budget limit" in reply


@pytest.mark.asyncio
async def test_a_billing_400_is_fatal_and_says_it_is_billing_not_a_bad_key(tmp_path):
    """Anthropic returns credit problems as a 400 — a generic 400 would retry."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [],
        when_exhausted=error_turn(400, "your credit balance is too low to access"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    reply = answer(chunks)
    assert "billing" in reply.lower()
    assert "credits" in reply.lower()


@pytest.mark.asyncio
async def test_a_transient_500_is_retried_once_without_tools_then_given_up_on(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(500, "upstream exploded"))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert fake.requests[1].tools == []
    assert any(
        "API returned an error" in t
        for t in fake.requests[1].steering_texts()
    )
    reply = answer(chunks)
    assert "500" in reply
    assert "upstream exploded" in reply


@pytest.mark.asyncio
async def test_a_quarantined_provider_is_reported_and_the_turn_stops(tmp_path):
    """Three consecutive API errors in a session auto-skip the provider."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    record_provider_error("openai", 429)
    record_provider_error("openai", 429)
    fake = FakeLLM([], when_exhausted=error_turn(429, "rate limited"))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert "quarantined" in answer(chunks)


@pytest.mark.asyncio
async def test_a_successful_round_clears_the_provider_failure_streak(tmp_path):
    from remedy.core.providers import provider_quarantined

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    record_provider_error("openai", 500)
    record_provider_error("openai", 500)
    fake = FakeLLM([text_turn("all good")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        await drain(runtime, "say hello")

    assert provider_quarantined("openai") is False
    # Streak really is cleared: two more errors must not trip the breaker.
    assert record_provider_error("openai", 500) is False
    assert record_provider_error("openai", 500) is False


@pytest.mark.asyncio
async def test_an_api_key_echoed_by_the_provider_is_redacted_before_the_chat(tmp_path):
    secret = "sk-supersecretkey12345"
    runtime = make_runtime(tmp_path, llm_api_key=secret)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(401, f"bad key: {secret}"))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    whole = "".join(chunks)
    assert secret not in whole
    assert "[redacted]" in whole


@pytest.mark.asyncio
async def test_an_expired_xai_session_is_refreshed_once_and_the_request_retried(
    tmp_path,
):
    runtime = make_runtime(
        tmp_path,
        llm_provider="xai",
        llm_api_key="old-key",
        llm_base_url="http://xai.invalid/v1",
    )
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([error_turn(401, "expired"), text_turn("worked after refresh")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.interfaces.xai_auth.refresh_if_needed"
    ), patch("remedy.interfaces.xai_auth.resolve_bearer", return_value="new-key"):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert fake.requests[0].headers["Authorization"] == "Bearer old-key"
    assert fake.requests[1].headers["Authorization"] == "Bearer new-key"
    assert "worked after refresh" in answer(chunks)


@pytest.mark.asyncio
async def test_an_xai_refresh_that_yields_no_token_stops_with_a_chat_message(tmp_path):
    """A status banner alone never reaches the transcript — say it in chat."""
    runtime = make_runtime(
        tmp_path,
        llm_provider="xai",
        llm_api_key="old-key",
        llm_base_url="http://xai.invalid/v1",
    )
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=error_turn(401, "expired"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.interfaces.xai_auth.refresh_if_needed"
    ), patch("remedy.interfaces.xai_auth.resolve_bearer", return_value=None):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    reply = answer(chunks)
    assert "xAI session expired" in reply
    assert "resend" in reply


@pytest.mark.asyncio
async def test_a_thinking_vs_tool_choice_400_rebuilds_the_body_instead_of_reposting(
    tmp_path,
):
    """A bare retry would re-POST the exact payload the host just rejected."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            error_turn(400, "tool_choice is not supported with thinking enabled"),
            tool_turn("add", {"a": 1}),
            text_turn("The sum is 5."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count >= 2
    assert any("cannot mix thinking with required tools" in e for e in events(chunks))
    # Recovery drops the *tool_choice*, never the tools — the model still has
    # to be able to call one.
    assert fake.requests[1].tool_choice == "auto"
    assert fake.requests[1].tool_names == ["add"]
    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]
    assert "The sum is 5." in answer(chunks)


@pytest.mark.asyncio
async def test_a_context_overflow_400_shrinks_and_retries_the_same_turn(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            error_turn(400, "exceed_context_size: n_prompt_tokens too large"),
            text_turn("shrunk and answered"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert any("Context full" in e for e in events(chunks))
    assert "shrunk and answered" in answer(chunks)


@pytest.mark.asyncio
async def test_truncated_tool_arguments_are_repaired_rather_than_posted_again(tmp_path):
    """A half-written arguments blob is a permanent 400 until it is repaired."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [
            tool_turn("add", '{"a": 1'),
            error_turn(400, "invalid tool argument: EOF while parsing"),
            text_turn("recovered"),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    # The cut arguments never reach the handler as if they were real.
    assert registry.calls == []
    whole = "".join(chunks)
    assert "TOOL_ARGS_TRUNCATED" in whole
    assert "Repaired incomplete tool arguments" in whole


# --------------------------------------------------------------------------
# stream-level failures
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dropped_connection_is_retried_without_streaming(tmp_path):
    import aiohttp

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            exception_turn(aiohttp.ClientConnectionError("Server disconnected")),
            text_turn("second try worked"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert fake.requests[0].stream is True
    assert fake.requests[1].stream is False
    assert "second try worked" in answer(chunks)


@pytest.mark.asyncio
async def test_a_cancelled_provider_wait_without_stop_keeps_the_turn(tmp_path):
    """WinError 64 / mid-JSON RST often arrives as CancelledError.

    That must retry the same turn, not paint Generation stopped / @@aborted.
    Owner Stop still aborts (see test_a_stop_after_tools_ran).
    """
    import asyncio

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            exception_turn(asyncio.CancelledError()),
            text_turn("kept going after the drop"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert "@@aborted\n" not in chunks
    assert "Generation stopped" not in "".join(chunks)
    assert "kept going after the drop" in answer(chunks)
    assert fake.request_count == 2
    assert fake.requests[1].stream is False


@pytest.mark.asyncio
async def test_a_connection_that_never_comes_back_leaves_a_durable_explanation(
    tmp_path,
):
    import aiohttp

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [],
        when_exhausted=exception_turn(
            aiohttp.ClientConnectionError("Server disconnected")
        ),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count > 1
    reply = answer(chunks)
    assert "Server disconnected" in reply
    assert "History is intact" in reply


@pytest.mark.asyncio
async def test_a_stream_cut_mid_token_delivers_what_arrived_and_does_not_retry(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [truncated_turn("hello world this got cut", keep=11)],
        when_exhausted=text_turn("must not be needed"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 1
    assert answer(chunks) == "hello world"


@pytest.mark.asyncio
async def test_unparseable_sse_is_treated_as_an_empty_round_not_as_an_answer(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [
            raw_turn([b"data: not-json-at-all\n\n", b"data: [DONE]\n\n"]),
            text_turn("second round fine"),
        ]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "not-json-at-all" not in reply
    assert reply == "second round fine"


# --------------------------------------------------------------------------
# empty and reasoning-only completions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_empty_completion_is_retried_rather_than_shipped_as_the_answer(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([empty_turn(), text_turn("recovered answer")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    assert fake.request_count == 2
    assert any(
        "returned no final answer text" in t
        for t in fake.requests[1].steering_texts()
    )
    assert answer(chunks) == "recovered answer"


@pytest.mark.asyncio
async def test_endless_empty_completions_end_with_an_honest_message(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=empty_turn())

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "empty final message after several retries" in reply
    # Bounded: the retry budget really is a budget.
    assert fake.request_count < 24


@pytest.mark.asyncio
async def test_a_reasoning_only_completion_is_promoted_to_the_answer(tmp_path):
    """Reasoner models often leave content blank after a long thinking stream."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([Turn(text="", reasoning="the answer is 4", finish_reason="stop")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "what is 2+2")

    assert fake.request_count == 1
    assert "@@thinking:the answer is 4" in chunks
    assert answer(chunks) == "the answer is 4"


@pytest.mark.asyncio
async def test_an_answer_cut_by_the_token_limit_is_continued_not_restarted(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("part one", finish_reason="length"), text_turn(" part two")]
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "write me a long essay about cats")

    assert fake.request_count == 2
    assert answer(chunks) == "part one part two"
    followup = fake.requests[1].steering_texts()
    assert any("do not restart" in t for t in followup)
    # The partial answer is replayed as assistant text so the model can resume.
    assert "part one" in fake.requests[1].texts_for_role("assistant")


# --------------------------------------------------------------------------
# tool calls written as text
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_markup_written_as_prose_is_executed_for_real_not_shown(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [
            text_turn('<tool_call>{"name": "list_dir", "arguments": {"path": "."}}</tool_call>'),
            tool_turn("list_dir", {"path": "."}),
            text_turn("The directory holds a.txt."),
        ],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list the project directory")

    assert registry.calls_to("list_dir")[0] == RecordedToolCall("list_dir", {"path": "."})
    reply = answer(chunks)
    assert "<tool_call>" not in reply
    assert "@@tool_calls" in chunks


@pytest.mark.asyncio
async def test_incomplete_tool_markup_is_nudged_back_to_real_function_calls(tmp_path):
    """A half-written DSML dump is junk — do not hang on it, and do not ship it."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("list_dir", description="list a directory", results=["a.txt"])
    fake = FakeLLM(
        [text_turn('<tool_call>{"name": "list_dir", "argum')],
        when_exhausted=text_turn("Sorry, I cannot proceed."),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list the project directory")

    assert fake.request_count >= 2
    assert any(
        "leaked incomplete tool markup" in t
        for t in fake.requests[1].steering_texts()
    )
    assert "<tool_call>" not in answer(chunks)


# --------------------------------------------------------------------------
# short circuits that must skip the model entirely
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clearing_goals_runs_the_tool_and_never_asks_the_model(tmp_path):
    """Replaying history through a model here used to re-run old browses."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "goal_clear_all", description="clear all goals", results=["2 goals cleared"]
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "clear goals")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("goal_clear_all", {})]
    assert answer(chunks) == "2 goals cleared"


@pytest.mark.asyncio
async def test_an_open_only_browse_navigates_once_and_stops_without_a_model_call(
    tmp_path,
):
    """``computer_navigate`` here is a scripted handler in a replaced registry.

    ``FakeToolRegistry.install`` swaps out ``runtime.tool_registry`` entirely
    and ``call_tool`` only ever dispatches through it, so nothing reaches the
    real host bridge or the desktop job queue.
    """
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "computer_navigate",
        description="navigate the browser rail",
        results=['{"ok": true, "url": "https://example.com"}'],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "go to example.com")

    assert fake.request_count == 0
    assert registry.calls == [
        RecordedToolCall(
            "computer_navigate", {"url": "https://example.com", "target": "browser"}
        )
    ]
    assert "Opened **example.com**" in answer(chunks)


@pytest.mark.asyncio
async def test_a_navigate_that_reports_ok_false_is_not_read_as_a_successful_open(
    tmp_path,
):
    """The tool *call* succeeded; the navigation did not. Only the body decides."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "computer_navigate",
        description="navigate the browser rail",
        results=['{"ok": false, "error": "rail_failed"}'],
    )
    fake = FakeLLM([], when_exhausted=text_turn("The browser rail did not open."))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "go to example.com")

    assert len(registry.calls_to("computer_navigate")) == 1
    assert "Opened" not in answer(chunks)
    # The real failure body is handed to the model, not hidden behind a retry.
    assert any(
        "did not succeed" in t and "rail_failed" in t
        for t in fake.requests[0].steering_texts()
    )


# --------------------------------------------------------------------------
# the absolute step ceiling still owes the person an answer
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_running_out_of_steps_asks_once_more_without_tools(tmp_path):
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM(
        [tool_turn("add", {"a": 1}), text_turn("Synthesized final.")],
        when_exhausted=text_turn("kept going"),
    )

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    assert fake.request_count == 2
    assert fake.requests[1].tools == []
    assert any(
        "write the complete final answer now" in t
        for t in fake.requests[1].steering_texts()
    )
    assert "Synthesized final." in answer(chunks)


@pytest.mark.asyncio
async def test_a_turn_that_ran_tools_but_produced_no_text_summarises_them_itself(
    tmp_path,
):
    """Silence after real tool work is the worst outcome — synthesize instead."""
    runtime = make_runtime(tmp_path)
    runtime._max_react_steps = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("add", description="add", results=["5"])
    fake = FakeLLM([tool_turn("add", {"a": 1})], when_exhausted=empty_turn())

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "add one")

    reply = answer(chunks)
    assert reply.strip()
    assert "Tool results" in reply
    assert registry.calls_to("add") == [RecordedToolCall("add", {"a": 1})]


# --------------------------------------------------------------------------
# a crash mid-turn is explained, not raised at the caller
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unexpected_error_mid_turn_ends_the_turn_with_an_explanation(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.build_step_request_body",
        side_effect=RuntimeError("kaboom in the builder"),
    ):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "Something went wrong on my side mid-turn" in reply
    assert "history is intact" in reply.lower()
    # The raw exception text is never dumped into the chat bubble.
    assert "kaboom in the builder" not in reply


@pytest.mark.asyncio
async def test_a_disconnect_with_no_tool_work_is_explained_in_chat_not_only_in_status(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("never reached"))

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop.build_step_request_body",
        side_effect=OSError("Server disconnected"),
    ):
        chunks = await drain(runtime, "say hello")

    reply = answer(chunks)
    assert "Connection to the model was lost mid-turn" in reply
    assert "Server disconnected" in reply
    assert "continue" in reply


# --------------------------------------------------------------------------
# cleanup — the finally block runs on every exit
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "turn",
    [
        text_turn("all done"),
        error_turn(404, "model_not_found: does not exist"),
        error_turn(400, "your credit balance is too low to access"),
        empty_turn(),
    ],
    ids=["answered", "fatal_model", "fatal_billing", "never_answered"],
)
async def test_post_turn_prep_runs_however_the_turn_ends(tmp_path, turn):
    """Early returns inside the step loop used to skip soul/memory saving."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=turn)
    seen: list[dict[str, Any]] = []

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.agent_post_turn.schedule_post_turn_prep",
        side_effect=lambda *a, **k: seen.append(k),
    ):
        await drain(runtime, "say hello")

    assert [c["message"] for c in seen] == ["say hello"]


@pytest.mark.asyncio
async def test_a_failing_post_turn_prep_never_breaks_the_already_streamed_reply(
    tmp_path,
):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("the answer")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.agent_post_turn.schedule_post_turn_prep",
        side_effect=RuntimeError("memory save exploded"),
    ):
        chunks = await drain(runtime, "say hello")

    assert answer(chunks) == "the answer"


@pytest.mark.asyncio
async def test_plan_mode_never_re_arms_tools_even_for_a_work_request(tmp_path):
    """The zero-tool drive must not smuggle write tools into a planning turn."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM(
        [], when_exhausted=text_turn("I will create the file for you shortly.")
    )

    with fake.patch(force_tools=True):
        chunks = await drain(
            runtime,
            "create a file called notes.txt in the project",
            plan_mode=True,
        )

    assert registry.calls == []
    assert all(r.tools == [] for r in fake.requests)
    assert answer(chunks).strip()
