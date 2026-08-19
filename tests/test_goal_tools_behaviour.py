"""Goals and plans, driven the way the model drives them.

324 statements at 23% covered — and Grove, the default surface, is built
entirely on these: its plots *are* `/goals`. A break here empties the owner's
home screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from remedy.core.agent_goals import register_goal_and_plan_tools


@dataclass
class _Task:
    """Just enough of a task for the goal tools to work with."""

    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "open"
    id: str = field(default_factory=lambda: uuid4().hex[:12])


class _Registry:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.handlers[name] = handler

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _Config:
    def __init__(self, home: str) -> None:
        self.home_dir = home

    def __getattr__(self, _name):
        return None


class _Runtime:
    """A runtime with the five members the goal tools actually touch."""

    def __init__(self, home: str) -> None:
        self.tool_registry = _Registry()
        self.config = _Config(home)
        self.memory = None
        self._session_brief = None
        self._tasks: list[_Task] = []

    def create_task(self, title, description="", tags=None):
        task = _Task(title=title, description=description, tags=list(tags or []))
        self._tasks.append(task)
        return task

    def list_tasks(self, *_a, **_kw):
        return list(self._tasks)

    def __getattr__(self, _name):
        return None


@pytest.fixture
def rt(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    runtime = _Runtime(str(tmp_path))
    register_goal_and_plan_tools(runtime)
    return runtime


@pytest.fixture
def tools(rt):
    return rt.tool_registry.handlers


def test_the_expected_tools_are_registered(tools):
    for name in (
        "goal_add", "goal_list", "goal_clear_all", "goal_complete",
        "goal_set_next", "goal_verify", "goal_drive",
        "plan_save", "plan_show", "plan_list", "plan_step_status",
    ):
        assert name in tools, f"{name} is no longer registered"


@pytest.mark.asyncio
async def test_a_goal_added_is_a_goal_listed(tools, rt):
    await tools["goal_add"](title="Ship the thing", description="by friday")
    assert [t.title for t in rt._tasks] == ["Ship the thing"]
    assert "goal" in rt._tasks[0].tags

    listed = await tools["goal_list"]()
    assert "Ship the thing" in listed


@pytest.mark.asyncio
async def test_listing_with_no_goals_is_not_an_error(tools):
    """Grove asks for this on first launch, before anything exists."""
    out = await tools["goal_list"]()
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_clearing_closes_goals_rather_than_deleting_them(tools, rt):
    """"Clear" means close, not erase — the history stays, marked "cleared by
    user", so a goal you gave up on is still something you did."""
    for title in ("one", "two", "three"):
        await tools["goal_add"](title=title)
    await tools["goal_clear_all"]()

    listed = await tools["goal_list"]()
    for title in ("one", "two", "three"):
        assert title in listed, "the goal was erased rather than closed"
    assert listed.count("[completed]") == 3
    assert "open" not in listed.lower().split("goals:")[-1].replace("cleared", "")


@pytest.mark.asyncio
async def test_a_plan_round_trips(tools):
    saved = await tools["plan_save"](
        title="Ship it", goal="get the thing out", steps="write it\ntest it\nship it"
    )
    assert isinstance(saved, str) and saved.strip()

    shown = await tools["plan_show"]()
    assert "ship it" in shown.lower() or "Ship it" in shown

    listed = await tools["plan_list"]()
    assert isinstance(listed, str) and listed.strip()


@pytest.mark.asyncio
async def test_showing_a_plan_that_is_not_there_says_so(tools):
    out = await tools["plan_show"](plan_id="nope-does-not-exist")
    assert isinstance(out, str) and out.strip()


@pytest.mark.asyncio
async def test_the_next_action_is_recorded(tools):
    await tools["goal_add"](title="Learn Spanish")
    out = await tools["goal_set_next"](
        title="Learn Spanish", action="Twenty minutes of vocabulary", next_by="friday"
    )
    assert isinstance(out, str) and out.strip()


@pytest.mark.asyncio
async def test_completing_a_goal_takes_evidence(tools):
    await tools["goal_add"](title="Fix the sink")
    out = await tools["goal_complete"](title="Fix the sink", evidence="photo of dry floor")
    assert isinstance(out, str) and out.strip()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("goal_add", {"title": ""}),
        ("goal_complete", {"title": "never existed"}),
        ("goal_verify", {"title": "never existed", "evidence": ""}),
        ("goal_set_next", {"title": "never existed", "action": ""}),
        ("plan_save", {"title": "", "goal": "", "steps": ""}),
    ],
)
async def test_empty_or_missing_input_never_raises(tools, tool, kwargs):
    """The model passes these; a crash here is a dead turn."""
    out = await tools[tool](**kwargs)
    assert isinstance(out, str)


@pytest.mark.asyncio
async def test_goal_list_output_is_readable_not_raw_json(tools, rt):
    """Grove renders this for the owner, so it must not be a tool dump."""
    await tools["goal_add"](title="Book the dentist")
    listed = await tools["goal_list"]()
    with pytest.raises(json.JSONDecodeError):
        json.loads(listed)
