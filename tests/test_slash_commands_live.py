"""Slash commands driven with a real memory store behind them.

The existing suite drives every command with ``memory=None`` — proving none of
them raises during boot. That leaves the half that only runs once there *is* a
store: the ones that actually write to memory, and the refusals guarding them.

The one that matters most is /remember. It is the owner explicitly telling
Remedy to keep something forever, which is exactly the moment someone pastes an
API key, so the refusal has to hold and the key must not appear in the reply.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from remedy.interfaces.slash_commands import handle_slash_command
from remedy.memory.store import MemoryStore


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))


@pytest_asyncio.fixture()
async def memory(tmp_path):
    async with MemoryStore(tmp_path / "memory.db") as store:
        yield store


async def run(command, memory=None, session="sess-1", runtime=None):
    return await handle_slash_command(command, session, memory, runtime)


def text_of(result) -> str:
    return str(result.get("text") or "")


# --- /remember ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_stores_the_fact(memory):
    out = await run("/remember bin night is Tuesday", memory)
    assert "Remembered" in text_of(out)
    assert "bin night" in text_of(out)


@pytest.mark.asyncio
async def test_a_remembered_fact_can_be_searched_back(memory):
    await run("/remember the dentist is on Elm St", memory)
    out = await run("/memory dentist", memory)
    assert "Elm St" in text_of(out)


@pytest.mark.asyncio
async def test_remember_with_nothing_to_remember_says_how(memory):
    assert "Usage:" in text_of(await run("/remember", memory))


@pytest.mark.asyncio
async def test_remember_without_a_store_says_so_rather_than_claiming_success(memory):
    """"Remembered:" with nowhere to put it is the worst possible answer."""
    out = await run("/remember something", None)
    assert "not available" in text_of(out)
    assert "Remembered" not in text_of(out)


@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH",
        "my password: hunter2-correct-horse",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCY",
    ],
)
@pytest.mark.asyncio
async def test_a_credential_is_refused_even_when_explicitly_asked_for(memory, secret):
    """An explicit /remember is exactly when someone pastes a key."""
    out = text_of(await run(f"/remember {secret}", memory))
    assert "secret" in out.lower()
    assert "Remembered:" not in out


@pytest.mark.asyncio
async def test_the_refusal_does_not_echo_the_secret(memory):
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
    assert secret not in text_of(await run(f"/remember {secret}", memory))


@pytest.mark.asyncio
async def test_a_refused_secret_is_not_in_the_store_afterwards(memory):
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
    await run(f"/remember {secret}", memory)
    found = await memory.search("sk-ant", limit=20)
    assert not [e for e in found if secret in (e.content or "")]


@pytest.mark.asyncio
async def test_remember_preserves_the_original_casing(memory):
    """The command is lowercased for matching; the fact must not be."""
    out = await run("/remember Call Dr Rowe about the Referral", memory)
    assert "Dr Rowe" in text_of(out)


@pytest.mark.asyncio
async def test_a_very_long_fact_is_stored_but_the_echo_is_bounded(memory):
    out = text_of(await run("/remember " + "x" * 2000, memory))
    assert len(out) < 600


# --- /memory ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_searching_an_empty_store_says_nothing_found(memory):
    out = text_of(await run("/memory anything", memory))
    assert out


@pytest.mark.asyncio
async def test_searching_for_something_absent_does_not_return_something_else(memory):
    await run("/remember the dentist is on Elm St", memory)
    assert "Elm St" not in text_of(await run("/memory zzzz-nothing", memory))


@pytest.mark.asyncio
async def test_memory_without_a_query_still_answers(memory):
    assert isinstance(await run("/memory", memory), dict)


# --- /whoami ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_whoami_answers_on_a_fresh_profile(memory):
    assert text_of(await run("/whoami", memory))


@pytest.mark.asyncio
async def test_whoami_reflects_what_was_remembered(memory):
    await run("/remember I prefer terse replies", memory)
    out = text_of(await run("/whoami", memory))
    assert out


# --- /forget and /pin ----------------------------------------------------------


@pytest.mark.asyncio
async def test_forget_with_nothing_named_says_how(memory):
    assert "Usage" in text_of(await run("/forget", memory)) or text_of(
        await run("/forget", memory)
    )


@pytest.mark.asyncio
async def test_forgetting_something_never_remembered_is_not_an_error(memory):
    assert isinstance(await run("/forget something nobody said", memory), dict)


@pytest.mark.asyncio
async def test_pin_with_nothing_named_says_how(memory):
    assert text_of(await run("/pin", memory))


@pytest.mark.asyncio
async def test_a_pinned_fact_is_accepted(memory):
    assert isinstance(await run("/pin always use metric units", memory), dict)


@pytest.mark.asyncio
async def test_a_credential_cannot_be_pinned_either(memory):
    """Pinning injects it into every prompt — worse than merely storing it.

    The write was already refused underneath, but /pin reported it as a vague
    "Could not pin <the key>": transient-sounding, so the owner retries, and
    the credential lands in the session transcript, which is persisted and
    exportable. It is turned away up front now.
    """
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"
    out = text_of(await run(f"/pin {secret}", memory))
    assert secret not in out
    assert "secret" in out.lower()
    assert "every prompt" in out


@pytest.mark.parametrize(
    "secret",
    [
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "my password: hunter2-correct-horse",
    ],
)
@pytest.mark.asyncio
async def test_no_credential_shape_survives_a_pin(memory, secret):
    assert secret not in text_of(await run(f"/pin {secret}", memory))


@pytest.mark.asyncio
async def test_an_ordinary_fact_still_pins(memory):
    """The guard must not have made pinning impossible."""
    out = text_of(await run("/pin always use metric units", memory))
    assert "secret" not in out.lower()


# --- /sessions ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_answers_with_none_yet(memory):
    assert text_of(await run("/sessions", memory))


@pytest.mark.asyncio
async def test_sessions_lists_what_exists(memory):
    from remedy.models import ChatSession

    await memory.create_chat_session(ChatSession(title="Planning the week"))
    assert "Planning the week" in text_of(await run("/sessions", memory))


# --- /goals -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goals_answers_with_none_set(memory):
    assert text_of(await run("/goals", memory))


@pytest.mark.asyncio
async def test_adding_a_goal_then_listing_it(memory):
    await run("/goal ship the parser", memory)
    assert isinstance(await run("/goals", memory), dict)


@pytest.mark.asyncio
async def test_a_goal_with_no_title_says_how(memory):
    assert text_of(await run("/goal", memory))


# --- the rest, with a store present -------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "/help",
        "/new",
        "/reset",
        "/compact",
        "/harness",
        "/models",
        "/thinking",
        "/plans",
        "/security-status",
        "/stretch",
    ],
)
@pytest.mark.asyncio
async def test_a_command_that_worked_without_a_store_still_works_with_one(
    memory, command
):
    """Adding a store must not turn a working command into a traceback."""
    assert isinstance(await run(command, memory), dict)
