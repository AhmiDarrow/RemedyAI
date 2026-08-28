"""Everything the ReAct turn decides *before* the model says a word.

``call_llm_stream`` does a lot of work up front: it resolves this turn's
provider binding, decides whether the question can be answered without any
provider at all, builds the timeout, injects the session brief / build
protocol / todo checklist, pre-navigates the browser rail, and arms (or
refuses to arm) tools. All of that happens before the first HTTP byte.

When this half is wrong the damage is quiet and expensive: a question that
could have been answered locally burns frontier tokens; a dead Sleev gateway
hangs the UI for a full minute per attempt; a local 7B model gets a
frontier-sized retry budget and dumps thirty thousand characters of hidden
thinking; a plan-mode turn writes a file anyway.

So these tests lean on the negatives:

* the fast paths must finish the turn with **zero** provider requests;
* plan mode must not let any of them write;
* a local binding must get the *small* caps, not the frontier ones;
* the session brief's open tasks must reach the tool resolver, and a pure
  action kick must clear them;
* a navigate that succeeded but has follow-up work must keep tools armed;
* the epoch wall must nudge, not silently stop.

Everything talks to :mod:`tests.harness.fake_llm`; nothing here opens a
socket, drives the desktop, or touches ``~/.remedy``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.providers import clear_provider_quarantine
from remedy.core.react_loop.loop import call_llm_stream
from remedy.models import AgentConfig
from tests.harness.fake_llm import (
    FakeLLM,
    FakeToolRegistry,
    RecordedToolCall,
    empty_turn,
    text_turn,
    tool_turn,
)

#: The tools-on/tools-off gate; pin it off for turns that must stay chat-only.
NO_TOOLS = "remedy.core.agent._message_wants_tools"


@pytest.fixture(autouse=True)
def _clean_provider_breaker():
    """The provider circuit breaker is process-global."""
    clear_provider_quarantine()
    yield
    clear_provider_quarantine()


@pytest.fixture(autouse=True)
def _no_ripgrep_download():
    """Building a runtime kicks off a background ripgrep fetch. Tests do not
    get to reach the network — stub the scheduler, not the download."""
    with patch("remedy.core.rg_binary.schedule_ensure_rg"):
        yield


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


# --------------------------------------------------------------------------
# answers that never need a provider at all
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_question_about_the_model_is_answered_without_any_provider_call(
    tmp_path,
):
    """"which model" is knowable locally — spending a frontier round on it is waste."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch():
        chunks = await drain(runtime, "what model are you using")

    assert fake.request_count == 0
    reply = answer(chunks)
    assert "fake-model" in reply


@pytest.mark.asyncio
async def test_the_local_create_bootstrap_finishes_the_turn_without_a_model_round(
    tmp_path,
):
    """A 7B model monologues instead of calling file_write — bootstrap beats it."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True), patch(
        "remedy.core.local_agent_optimize.maybe_bootstrap_local_create",
        return_value="Wrote `calculator.py`.",
    ):
        chunks = await drain(runtime, "write me a calculator in python")

    assert fake.request_count == 0
    assert answer(chunks) == "Wrote `calculator.py`."


@pytest.mark.asyncio
async def test_plan_mode_never_lets_the_create_bootstrap_write_anything(tmp_path):
    """Plan mode skips call_tool's PLAN_MODE_BLOCKED, so the loop must skip it."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("Here is the plan.")])
    called: list[str] = []

    async def _boot(_runtime, msg):
        called.append(msg)
        return "Wrote `calculator.py`."

    with fake.patch(force_tools=True), patch(
        "remedy.core.local_agent_optimize.maybe_bootstrap_local_create", _boot
    ):
        chunks = await drain(
            runtime, "write me a calculator in python", plan_mode=True
        )

    assert called == []
    assert "Here is the plan." in answer(chunks)


@pytest.mark.asyncio
async def test_a_preamble_status_event_reaches_the_caller_before_the_first_request(
    tmp_path,
):
    """An attachment the decoder cannot read must say so, not vanish."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("I can only see the file path.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.vision.service.decode_for_turn",
        return_value={"mode": "unavailable", "hint": "no local decoder installed"},
    ):
        chunks = await drain(
            runtime,
            "what is in this picture",
            attachments=[{"path": str(tmp_path / "shot.png"), "kind": "image"}],
        )

    status = [e for e in events(chunks) if e.startswith("@@status:")]
    assert any("Visual decoder unavailable" in e for e in status)
    # Yielded during the preamble — before anything was posted.
    assert chunks.index(status[0]) < chunks.index("I can only see the file path.")


# --------------------------------------------------------------------------
# the timeout the turn is given
# --------------------------------------------------------------------------


def _timeout_spy():
    """Record every ClientTimeout the loop builds, still returning a real one."""
    seen: list[dict[str, Any]] = []
    real = aiohttp.ClientTimeout

    def _make(**kw: Any) -> Any:
        seen.append(dict(kw))
        return real(**kw)

    return seen, patch("aiohttp.ClientTimeout", side_effect=_make)


@pytest.mark.asyncio
async def test_a_direct_provider_gets_the_long_connect_timeout(tmp_path):
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("hi")])
    seen, spy = _timeout_spy()

    with fake.patch(), patch(NO_TOOLS, return_value=False), spy:
        await drain(runtime, "say hello")

    assert seen[0]["connect"] == 60


@pytest.mark.asyncio
async def test_a_sleev_gateway_gets_a_short_connect_so_a_dead_proxy_fails_fast(
    tmp_path,
):
    """A minute per attempt against a dead gateway reads as a frozen UI."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("hi")])
    seen, spy = _timeout_spy()

    with fake.patch(), patch(NO_TOOLS, return_value=False), spy, patch(
        "remedy.core.sleev.is_sleev_endpoint", return_value=True
    ):
        await drain(runtime, "say hello")

    assert seen[0]["connect"] == 12
    # The long read budget is untouched — slow thinking is not a dead gateway.
    assert seen[0]["sock_read"] == 900


# --------------------------------------------------------------------------
# a local binding gets the small budgets, not the frontier ones
# --------------------------------------------------------------------------


def make_local_runtime(tmp_path: Path, **overrides: Any) -> BasicRuntime:
    overrides.setdefault("llm_provider", "ollama")
    overrides.setdefault("llm_model", "qwen3:8b")
    overrides.setdefault("llm_base_url", "http://127.0.0.1:11434/v1")
    overrides.setdefault("llm_api_key", "")
    return make_runtime(tmp_path, **overrides)


@pytest.mark.asyncio
async def test_a_local_answer_cut_by_the_token_limit_is_not_auto_continued(tmp_path):
    """Extra length rounds on a 7B dump hidden thinking, not more answer."""
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn("part one", finish_reason="length")],
        when_exhausted=text_turn(" part two"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "write me a long essay about cats")

    assert fake.request_count == 1
    assert "part two" not in answer(chunks)


@pytest.mark.asyncio
async def test_a_local_model_gets_one_empty_answer_retry_not_the_frontier_budget(
    tmp_path,
):
    runtime = make_local_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=empty_turn())

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    # One original round plus a single retry — the frontier budget is 8.
    assert fake.request_count <= 3
    assert answer(chunks).strip()


# --------------------------------------------------------------------------
# short circuits that answer from a tool and skip the model
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_personal_assistant_ask_is_answered_from_one_tool_call(tmp_path):
    """A debt list is a lookup, not a reasoning problem — no provider round."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "debt_list",
        description="list debts",
        results=['{"ok": true, "debts": [{"name": "Card", "balance": 120.5, '
                 '"apr_pct": 19}]}'],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list my debts")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("debt_list", {})]
    reply = answer(chunks)
    assert "Card" in reply and "120.50" in reply
    assert "@@tool_calls" in chunks


@pytest.mark.asyncio
async def test_rmb_status_is_answered_in_app_without_a_provider_round(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "rmb",
        description="local llama",
        results=["RMB ready model=Qwen running=true"],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "rmb status")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("rmb", {"action": "status"})]
    assert "ready" in answer(chunks).lower()


@pytest.mark.asyncio
async def test_whats_on_screen_is_answered_in_app_without_a_provider_round(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "companion_context",
        description="screen snapshot",
        results=["**Foreground:** Notepad\nclipboard: empty"],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "what's on my screen")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("companion_context", {})]
    assert "Notepad" in answer(chunks)


@pytest.mark.asyncio
async def test_run_the_tests_is_answered_in_app_without_a_provider_round(tmp_path):
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "job_run",
        description="verify",
        results=["[job verify ok]\npytest: 12 passed"],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "run the tests")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("job_run", {"kind": "verify"})]
    assert "12 passed" in answer(chunks)


@pytest.mark.asyncio
async def test_git_status_is_answered_in_app_without_a_provider_round(tmp_path):
    """git status is a local lookup — Claude / GPT / anyone should not be billed."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "git_status",
        description="git status",
        results=["**git_status** exit=0\n## master"],
    )
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "git status")

    assert fake.request_count == 0
    assert registry.calls == [RecordedToolCall("git_status", {})]
    assert "master" in answer(chunks)


@pytest.mark.asyncio
async def test_the_fast_path_is_skipped_when_the_tool_is_not_registered(tmp_path):
    """No debt_list tool means the model has to answer — not a silent 'Done.'."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([], when_exhausted=text_turn("I have no debt records."))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "list my debts")

    assert fake.request_count >= 1
    assert "I have no debt records." in answer(chunks)


@pytest.mark.asyncio
async def test_clearing_goals_with_no_goal_tool_wipes_the_session_brief_instead(
    tmp_path,
):
    """Without goal_clear_all the open tasks still have to go — silently kept
    open tasks resurrect old work on the next epoch wall."""
    from remedy.memory.harness.brief import SessionBrief

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    brief = SessionBrief(session_id="s", open_tasks=["ship the installer"])
    runtime._session_brief = brief
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "clear goals")

    assert fake.request_count == 0
    assert brief.open_tasks == []
    assert "Session open tasks cleared" in answer(chunks)


@pytest.mark.asyncio
async def test_an_l0_turn_is_answered_without_spending_a_provider_round(tmp_path):
    """The guard read ``int(_turn_tier_of(runtime) or 1) == 0``.

    ``0 or 1`` is 1, so the comparison was False for every possible value and
    the L0 safety net beneath it was dead code. The same idiom flattened the
    tier where it was stored and where it was read, so L0_INSTANT — the tier
    whose entire purpose is answering without a frontier call — could not be
    observed by anyone. An L0 turn paid for a provider round every time.
    """
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("A long story.")])
    l0_calls: list[str] = []

    def _l0(_runtime, msg, **_kw):
        l0_calls.append(msg)
        return "Remedy **9.9.9**."

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop._turn_tier_of", return_value=0
    ), patch("remedy.core.metabolism.l0.try_l0_system_reply", _l0):
        chunks = await drain(runtime, "what version are you")

    assert l0_calls, "the L0 net was still never consulted"
    assert fake.request_count == 0, "a provider round was spent on an L0 turn"
    assert "9.9.9" in answer(chunks)


@pytest.mark.asyncio
async def test_an_ordinary_turn_is_not_diverted_to_the_l0_net(tmp_path):
    """The net must only catch tier 0 — not shortcut real questions."""
    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    fake = FakeLLM([text_turn("A long story.")])
    l0_calls: list[str] = []

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop._turn_tier_of", return_value=1
    ), patch(
        "remedy.core.metabolism.l0.try_l0_system_reply",
        lambda *a, **kw: l0_calls.append(1) or "wrong",
    ):
        chunks = await drain(runtime, "tell me the story of the loop")

    assert l0_calls == []
    assert answer(chunks) == "A long story."


# --------------------------------------------------------------------------
# what gets injected into the first request body
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_existing_checklist_is_streamed_before_the_model_is_asked(tmp_path):
    """The desktop checklist must show the todos already on disk, not wait for
    the model to re-announce them."""
    from remedy.core.build_todos import TodoItem, save_todos

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    save_todos([TodoItem(id="t1", content="wire the installer")], runtime)
    fake = FakeLLM([text_turn("Working on it.")])

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "say hello")

    todos = [e for e in events(chunks) if e.startswith("@@todos:")]
    assert todos, events(chunks)
    assert "wire the installer" in todos[0]


@pytest.mark.asyncio
async def test_a_frontier_continue_carries_the_brief_into_the_first_request(tmp_path):
    """"continue" with no context is how a build silently restarts from zero."""
    from remedy.memory.harness.brief import SessionBrief

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    runtime._session_brief = SessionBrief(
        session_id="s",
        intent="ship the installer",
        open_tasks=["sign the exe"],
    )
    fake = FakeLLM([], when_exhausted=text_turn("Carrying on."))

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        await drain(runtime, "continue")

    assert fake.requests[0].mentions("[Frontier continue")
    assert fake.requests[0].mentions("ship the installer")


@pytest.mark.asyncio
async def test_the_session_briefs_open_tasks_reach_the_tool_resolver(tmp_path):
    from remedy.core.react_loop import binding as _binding_mod
    from remedy.memory.harness.brief import SessionBrief

    runtime = make_runtime(tmp_path)
    FakeToolRegistry().install(runtime)
    runtime._session_brief = SessionBrief(session_id="s", open_tasks=["sign the exe"])
    fake = FakeLLM([text_turn("hi")])
    seen: list[list[str]] = []

    def _spy(**kwargs: Any):
        seen.append(list(kwargs.get("open_tasks_for_wall") or []))
        return _binding_mod.resolve_and_apply_tools(**kwargs)

    with fake.patch(), patch(NO_TOOLS, return_value=False), patch(
        "remedy.core.react_loop.loop._resolve_and_apply_tools_fn", _spy
    ):
        await drain(runtime, "say hello")

    assert seen and seen[0] == ["sign the exe"]


@pytest.mark.asyncio
async def test_a_pure_action_kick_does_not_resume_older_open_tasks(tmp_path):
    """"go to example.com" must not drag last hour's build back into the turn."""
    from remedy.core.react_loop import binding as _binding_mod
    from remedy.memory.harness.brief import SessionBrief

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "computer_navigate",
        description="navigate the browser rail",
        results=['{"ok": true, "url": "https://example.com"}'],
    )
    runtime._session_brief = SessionBrief(session_id="s", open_tasks=["sign the exe"])
    fake = FakeLLM([], when_exhausted=text_turn("must never be reached"))
    seen: list[list[str]] = []

    def _spy(**kwargs: Any):
        seen.append(list(kwargs.get("open_tasks_for_wall") or []))
        return _binding_mod.resolve_and_apply_tools(**kwargs)

    with fake.patch(force_tools=True), patch(
        "remedy.core.react_loop.loop._resolve_and_apply_tools_fn", _spy
    ):
        await drain(runtime, "go to example.com")

    assert seen and seen[0] == []


# --------------------------------------------------------------------------
# the pre-navigate rail, when there is follow-up work on the page
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_navigate_with_follow_up_work_keeps_tools_armed_and_hands_over(
    tmp_path,
):
    """Open-and-sign-in is two jobs. Stopping after the open is a half-done turn."""
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add(
        "computer_navigate",
        description="navigate the browser rail",
        results=['{"ok": true, "url": "https://example.com"}'],
    )
    registry.add("computer_act", description="click and type", results=["clicked"])
    fake = FakeLLM([], when_exhausted=text_turn("Signed in."))

    with fake.patch(force_tools=True):
        chunks = await drain(
            runtime, "open https://example.com then sign in with foo@bar.com"
        )

    assert registry.calls_to("computer_navigate")
    assert fake.request_count >= 1
    first = fake.requests[0]
    # The model is told the open already happened, and is handed the username
    # the person typed rather than being left to invent one.
    assert first.mentions("Browser rail already opened https://example.com (SUCCESS)")
    assert first.mentions("foo@bar.com")
    # Tools stay armed — the sign-in still needs them.
    assert first.tools
    assert "Opened **example.com**" not in answer(chunks)


# --------------------------------------------------------------------------
# tools are never re-armed for a question that never wanted them
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_trivia_question_is_never_re_armed_into_tools_by_a_promise(tmp_path):
    """A model that promises to go and look something up normally gets tools
    handed back. For greetings and verbal trivia that re-arm is refused —
    otherwise every "what is 2+2" becomes a web search nobody asked for.
    """
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("web_search", description="search the web", results=["4"])
    fake = FakeLLM([], when_exhausted=text_turn("I will search the web for that now."))

    with fake.patch(force_tools=True):
        chunks = await drain(runtime, "what is 2+2")

    assert registry.calls == []
    assert all(r.tools == [] for r in fake.requests)
    assert "I will search the web for that now." in answer(chunks)


@pytest.mark.asyncio
async def test_a_build_engine_that_raises_does_not_take_the_turn_down_with_it(
    tmp_path,
):
    """The build engine is consulted on every step. It is advisory — an
    exception from it must degrade to "no build", not end the turn in a crash.
    """
    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("I will create the file shortly."))

    with fake.patch(force_tools=True), patch(
        "remedy.core.build_engine.get_build_state",
        side_effect=RuntimeError("build engine exploded"),
    ), patch(
        "remedy.core.build_engine.open_drive_should_continue",
        side_effect=RuntimeError("build engine exploded"),
    ):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    reply = answer(chunks)
    assert "build engine exploded" not in reply
    # The honest ending still happens: prose is not accepted as done work.
    assert "no tools ran" in reply


# --------------------------------------------------------------------------
# the soft epoch wall — compact and keep going, never a silent stop
# --------------------------------------------------------------------------

#: The epoch roll queues a background continuity job; that is not what these
#: tests are about, and a real queue would make them non-deterministic.
NO_CONTINUITY_JOB = "remedy.memory.partner_state.continuity.schedule_continuity_core"


@pytest.mark.asyncio
async def test_a_productive_epoch_rolls_over_and_tells_the_model_to_keep_going(
    tmp_path,
):
    """The soft wall is a compaction point, not a tool budget: work that is
    still calling tools must be told to continue, with tools still armed."""
    runtime = make_runtime(tmp_path)
    runtime._epoch_react_steps = 16
    runtime._max_react_steps = 22
    registry = FakeToolRegistry().install(runtime)
    registry.add("note", description="record a note", results=["noted"])
    fake = FakeLLM(
        [tool_turn("note", {"i": i}) for i in range(17)],
        when_exhausted=text_turn("All done."),
    )

    with fake.patch(force_tools=True), patch(NO_CONTINUITY_JOB, return_value=False):
        chunks = await drain(runtime, "work through the checklist and finish the build")

    roll = [e for e in events(chunks) if "Continuing until task finished" in e]
    assert len(roll) == 1
    assert "epoch 2, step 16" in roll[0]
    after_roll = fake.requests[17]
    assert after_roll.tools, "the epoch wall must not disarm tools"
    assert any("epoch" in t.lower() for t in after_roll.steering_texts())


@pytest.mark.asyncio
async def test_an_epoch_of_pure_talk_is_nudged_back_to_tools_not_wrapped_up(tmp_path):
    """Tools armed, a whole epoch gone, and not one function call — the wall
    must push harder, not accept the monologue as the answer."""
    runtime = make_runtime(tmp_path)
    runtime._epoch_react_steps = 16
    runtime._max_react_steps = 19
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("I will create the file shortly."))

    with fake.patch(force_tools=True), patch(
        NO_CONTINUITY_JOB, return_value=False
    ), patch(
        # An open drive keeps the zero-tool drive going past its own cap, so the
        # turn actually survives long enough to reach the epoch wall.
        "remedy.core.build_engine.open_drive_should_continue",
        return_value=True,
    ), patch(
        # No build supervision this turn — otherwise the wall reads the build as
        # unfinished work and takes the ordinary roll instead of this branch.
        "remedy.core.build_engine.begin_build_turn",
        return_value=None,
    ):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    nudge = [e for e in events(chunks) if "Nudge" in e and "use tools" in e]
    assert len(nudge) == 1
    assert any("Use tools now" in t for t in fake.requests[17].steering_texts())
    assert fake.requests[17].tools


@pytest.mark.asyncio
async def test_a_dead_air_epoch_pauses_and_re_arms_instead_of_stopping(tmp_path):
    """After the stale-epoch budget the partner must not give up — it re-arms
    tools and keeps going, because the person asked for the work to be done."""
    runtime = make_runtime(tmp_path)
    runtime._epoch_react_steps = 16
    runtime._max_react_steps = 19
    runtime._react_max_stale_epochs = 1
    registry = FakeToolRegistry().install(runtime)
    registry.add("file_write", description="write a file", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("I will create the file shortly."))

    with fake.patch(force_tools=True), patch(
        NO_CONTINUITY_JOB, return_value=False
    ), patch(
        "remedy.core.build_engine.open_drive_should_continue", return_value=True
    ), patch("remedy.core.build_engine.begin_build_turn", return_value=None):
        chunks = await drain(runtime, "create a file called notes.txt in the project")

    paused = [e for e in events(chunks) if "Paused after long idle" in e]
    assert len(paused) == 1
    assert "re-arming tools" in paused[0]
    assert fake.requests[17].tools


@pytest.mark.asyncio
async def test_an_open_drive_that_stops_advancing_is_asked_for_an_honest_status(
    tmp_path,
):
    """Retrying the same blocked step to the wall looks like a hang. Ask once."""
    runtime = make_runtime(tmp_path)
    runtime._open_drive_patience = 16
    runtime._max_react_steps = 22
    registry = FakeToolRegistry().install(runtime)
    registry.add("note", description="record a note", results=["noted"])
    fake = FakeLLM(
        [tool_turn("note", {"i": i}) for i in range(20)],
        when_exhausted=text_turn("All done."),
    )

    with fake.patch(force_tools=True), patch(
        # One early advance, then a drive that never moves again.
        "remedy.core.build_engine.build_progress_score",
        return_value=1,
    ), patch("remedy.core.build_engine.build_has_open_drive", return_value=True):
        await drain(runtime, "work through the checklist and finish the build")

    # Appended to the running message list once, so it is carried by every
    # later request — but never appended twice. A nag on every step would just
    # be more noise in an already long context window.
    asks = [
        t for t in fake.requests[-1].steering_texts() if "[Progress check]" in t
    ]
    assert len(asks) == 1
    assert "what you need from me" in asks[0]
    assert not any("[Progress check]" in t for t in fake.requests[0].steering_texts())


# --------------------------------------------------------------------------
# machine green verify — tools come off unless something still needs them
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_green_verify_strips_tools_before_the_first_request(tmp_path):
    """Machine verify is green and nothing is left to ship: the summary round
    must go out with no tools at all, or the model keeps poking the project."""
    from remedy.core.turn_context import set_turn_build_verify_green

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("bash_exec", description="run a command", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("It went fine."))
    set_turn_build_verify_green(True, runtime)

    with fake.patch(force_tools=True), patch(
        "remedy.core.build_engine.keep_agency_after_green", return_value=False
    ):
        chunks = await drain(runtime, "how did it go")

    assert fake.request_count == 1
    assert fake.last_request.tools == []
    assert registry.calls == []
    assert "It went fine." in answer(chunks)


@pytest.mark.asyncio
async def test_the_same_turn_without_a_green_verify_keeps_its_tools(tmp_path):
    """The control for the test above: the strip is the green flag's doing,
    not something about this message."""
    from remedy.core.turn_context import set_turn_build_verify_green

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("bash_exec", description="run a command", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("It went fine."))
    set_turn_build_verify_green(False, runtime)

    with fake.patch(force_tools=True):
        await drain(runtime, "how did it go")

    assert fake.requests[0].tools


@pytest.mark.asyncio
async def test_a_green_verify_that_still_owes_a_ship_step_keeps_its_tools(tmp_path):
    """Green does not mean shipped — stripping tools here strands the release."""
    from remedy.core.turn_context import set_turn_build_verify_green

    runtime = make_runtime(tmp_path)
    registry = FakeToolRegistry().install(runtime)
    registry.add("bash_exec", description="run a command", results=["ok"])
    fake = FakeLLM([], when_exhausted=text_turn("Shipped."))
    set_turn_build_verify_green(True, runtime)

    with fake.patch(force_tools=True), patch(
        "remedy.core.build_engine.keep_agency_after_green", return_value=True
    ):
        await drain(runtime, "how did it go")

    assert fake.requests[0].tools, "ship work still needs tools"
    # The green latch is released so the next step is judged on its own.
    assert runtime._build_verify_green is False


@pytest.mark.asyncio
async def test_a_pure_chat_turn_is_wrapped_up_at_the_epoch_wall(tmp_path):
    """Tools were never enabled, so there is no work to protect: once a chat
    turn has burned a whole epoch of length-continuations the wall forces the
    final answer instead of letting the essay run to the safety ceiling.
    """
    runtime = make_runtime(tmp_path)
    runtime._epoch_react_steps = 16
    runtime._max_react_steps = 22
    FakeToolRegistry().install(runtime)
    fake = FakeLLM(
        [text_turn(f"part {i} ", finish_reason="length") for i in range(20)],
        when_exhausted=text_turn("never reached"),
    )

    with fake.patch(), patch(NO_TOOLS, return_value=False):
        chunks = await drain(runtime, "write me a long essay about cats")

    # 16 continuations, then the wall — well short of the 22-step ceiling.
    assert fake.request_count == 17
    reply = answer(chunks)
    assert reply.startswith("part 0 ")
    assert "never reached" not in reply
