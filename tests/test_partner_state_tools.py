"""Partner State tools — subgoals, tool recall, the epistemic graph, reminders.

This is the working memory Remedy keeps *about her own work*: what she is doing
right now, which writes she has not checked, what she concluded and why. The
sharpest edge is memory_fact: it writes into long-lived state, so a secret that
gets in there stays in there. That refusal gets the most attention below.
"""

from __future__ import annotations

import pytest

from remedy.core.agent_partner_tools import register_partner_state_tools


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler


class RT:
    def __init__(self, home, session_id: str) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(home), "project_path": ""})()
        self._session_id = session_id
        self._session_brief = None
        self._project_path = ""


@pytest.fixture()
def partner(tmp_path, monkeypatch, request):
    # PartnerState lives in a process registry keyed by session id, so every
    # test needs its own or they share one graph and one subgoal stack.
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = RT(tmp_path, session_id=f"partner-{request.node.name}")
    register_partner_state_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools}


def state(partner):
    from remedy.memory.partner_state import ensure_partner_state

    return ensure_partner_state(partner["rt"])


# --- subgoals ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_subgoal_needs_a_title(partner):
    assert "short subgoal title" in await partner["tools"]["subgoal_open"]()


@pytest.mark.asyncio
async def test_opening_a_subgoal_reports_the_protection_it_buys(partner):
    out = await partner["tools"]["subgoal_open"](title="Fix the parser")
    assert "Fix the parser" in out
    assert "subgoal_close" in out


@pytest.mark.asyncio
async def test_the_first_subgoal_becomes_the_session_intent(partner):
    """Otherwise a resumed session has no idea what it was doing."""
    await partner["tools"]["subgoal_open"](title="Fix the parser")
    assert partner["rt"]._session_brief.intent == "Fix the parser"


@pytest.mark.asyncio
async def test_a_later_subgoal_does_not_rewrite_the_intent(partner):
    await partner["tools"]["subgoal_open"](title="Fix the parser")
    await partner["tools"]["subgoal_close"]()
    await partner["tools"]["subgoal_open"](title="Tidy imports")
    assert partner["rt"]._session_brief.intent == "Fix the parser"


@pytest.mark.asyncio
async def test_closing_nothing_says_there_was_nothing_open(partner):
    assert "No open subgoal" in await partner["tools"]["subgoal_close"]()


@pytest.mark.asyncio
async def test_a_closed_subgoal_reports_its_status(partner):
    await partner["tools"]["subgoal_open"](title="Fix the parser")
    out = await partner["tools"]["subgoal_close"](summary="done", status="closed")
    assert "Closed subgoal" in out
    assert "Status=closed" in out


@pytest.mark.asyncio
async def test_status_answers_on_a_cold_state(partner):
    out = await partner["tools"]["subgoal_status"]()
    assert "Partner State:" in out
    assert "(none)" in out


@pytest.mark.asyncio
async def test_status_names_the_active_subgoal(partner):
    await partner["tools"]["subgoal_open"](title="Fix the parser")
    out = await partner["tools"]["subgoal_status"]()
    assert "Fix the parser" in out


# --- tool recall ------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_with_nothing_recorded_says_so(partner):
    assert "No tool transactions" in await partner["tools"]["tool_recall"]()


# --- the write set ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_write_set_is_reported_as_clean(partner):
    assert "clean" in await partner["tools"]["write_set_verify"]()


@pytest.mark.asyncio
async def test_verifying_a_path_that_was_never_written_is_refused(partner):
    out = await partner["tools"]["write_set_verify"](path="never/written.py")
    assert "not in write-set" in out


# --- the epistemic graph ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_node_needs_text(partner):
    assert "Provide text" in await partner["tools"]["memory_fact"]()


@pytest.mark.asyncio
async def test_a_fact_is_recorded(partner):
    out = await partner["tools"]["memory_fact"](text="The parser is recursive descent")
    assert "Recorded fact" in out
    assert "recursive descent" in out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "fact",
        "decision",
        "artifact",
        "commitment",
        "hypothesis",
        "skill_pattern",
        "affordance",
    ],
)
async def test_every_node_kind_is_accepted(partner, kind):
    out = await partner["tools"]["memory_fact"](text="something", kind=kind)
    assert f"Recorded {kind}" in out


@pytest.mark.asyncio
async def test_an_unknown_kind_degrades_to_a_fact(partner):
    """Being wrong about the label must not lose the knowledge."""
    out = await partner["tools"]["memory_fact"](text="something", kind="nonsense")
    assert "Recorded fact" in out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret",
    [
        "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKKLLLL",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY",
        "password: hunter2-correct-horse-battery",
    ],
)
async def test_a_credential_is_refused_rather_than_stored(partner, secret):
    """Partner state is long-lived; a key that lands here never leaves."""
    out = await partner["tools"]["memory_fact"](text=secret)
    assert "Refused" in out
    assert "secret" in out.lower()


@pytest.mark.asyncio
async def test_the_refusal_does_not_echo_the_secret_back(partner):
    secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKKLLLL"
    out = await partner["tools"]["memory_fact"](text=secret)
    assert secret not in out


@pytest.mark.asyncio
async def test_a_rejected_alternative_is_kept_with_the_decision(partner):
    """Why something was *not* done is the part that gets lost."""
    out = await partner["tools"]["memory_fact"](
        text="Use a hand-written lexer",
        kind="decision",
        why="regex engine backtracks on nested groups",
        rejected="regex-based tokenizer",
    )
    assert "Recorded decision" in out


# --- prospective memory -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_reminder_needs_text(partner):
    assert "Provide the reminder" in await partner["tools"]["remember_later"]()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trigger",
    [
        "session_start",
        "subgoal_close",
        "tool_success",
        "tool_name",
        "project_switch",
        "tests_pass",
        "epoch_roll",
        "manual",
    ],
)
async def test_every_documented_trigger_is_accepted(partner, trigger):
    out = await partner["tools"]["remember_later"](text="check the lock", trigger=trigger)
    assert f"when {trigger}" in out


@pytest.mark.asyncio
async def test_an_unknown_trigger_becomes_a_manual_one(partner):
    """Better armed-but-manual than silently dropped."""
    out = await partner["tools"]["remember_later"](text="check", trigger="on_tuesday")
    assert "when manual" in out


@pytest.mark.asyncio
async def test_a_tool_scoped_reminder_names_its_tool(partner):
    out = await partner["tools"]["remember_later"](
        text="re-read the config", trigger="tool_name", tool_name="file_write"
    )
    assert "tool=file_write" in out


# --- registration -----------------------------------------------------------


def test_every_partner_tool_is_registered(partner):
    assert set(partner["tools"]) >= {
        "subgoal_open",
        "subgoal_close",
        "subgoal_status",
        "tool_recall",
        "write_set_verify",
        "memory_fact",
        "remember_later",
        "partner_state_sync",
    }


@pytest.mark.asyncio
async def test_a_forced_sync_answers(partner):
    out = await partner["tools"]["partner_state_sync"]()
    assert out
