"""Missions — the checklist Remedy keeps when she works alone.

The behaviour worth pinning is the *gate*: a mission with a verify command is
not done because every box is ticked. Ticking boxes is what a model does when
it wants to be finished; running the verify is what tells anyone it worked. So
both mission_status and mission_update must keep saying so until verify passes.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_mission_tools import register_mission_tools
from remedy.core.mission import MissionStore


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    def __init__(self, home, project) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(home)})()
        self._session_id = "mission-session"
        self._project = project

    def effective_project_path(self):
        return self._project

    def resolve_tool_path(self, path, *, for_write=False):
        return self._project / (path or ".")


@pytest.fixture()
def missions(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    rt = RT(home, project)
    register_mission_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools, "home": home, "project": project}


def store_for(missions) -> MissionStore:
    return MissionStore(str(missions["home"]))


# --- starting ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_mission_needs_a_goal(missions):
    out = await missions["tools"]["mission_start"]()
    assert "MISSING_GOAL" in out
    assert "mission_start(goal=" in out


@pytest.mark.asyncio
async def test_a_goal_alone_is_enough(missions):
    out = await missions["tools"]["mission_start"](goal="Ship the parser")
    assert "Mission started" in out
    assert "Ship the parser" in out


@pytest.mark.asyncio
async def test_steps_can_be_given_as_plain_lines(missions):
    await missions["tools"]["mission_start"](
        goal="Build", steps="- write the test\n- make it pass", verify_command="true"
    )
    m = store_for(missions).latest("mission-session")
    assert [s.title for s in m.steps] == ["write the test", "make it pass"]


@pytest.mark.asyncio
async def test_steps_can_be_given_as_json(missions):
    await missions["tools"]["mission_start"](
        goal="Build", steps=json.dumps(["one", "two"]), verify_command="true"
    )
    assert [s.title for s in store_for(missions).latest("mission-session").steps] == [
        "one",
        "two",
    ]


@pytest.mark.asyncio
async def test_malformed_json_steps_fall_back_to_lines(missions):
    """A half-written JSON list should not lose the checklist."""
    await missions["tools"]["mission_start"](
        goal="Build", steps='["one", "two"', verify_command="true"
    )
    assert store_for(missions).latest("mission-session").steps


@pytest.mark.asyncio
async def test_numbered_and_bulleted_steps_are_cleaned(missions):
    await missions["tools"]["mission_start"](
        goal="Build", steps="* alpha\n\t- beta\n", verify_command="true"
    )
    texts = [s.title for s in store_for(missions).latest("mission-session").steps]
    assert texts == ["alpha", "beta"]


@pytest.mark.asyncio
async def test_a_missing_verify_command_is_inferred_from_the_stack(
    missions, monkeypatch
):
    """Remedy should not need to be told `pytest -q` in a Python repo."""
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "pytest -q"})(),
    )
    out = await missions["tools"]["mission_start"](goal="Build")
    assert "auto verify_command" in out
    assert "pytest -q" in out
    assert store_for(missions).latest("mission-session").verify_command == "pytest -q"


@pytest.mark.asyncio
async def test_an_unrecognisable_project_still_starts_a_mission(missions, monkeypatch):
    def boom(_root):
        raise RuntimeError("no fingerprint")

    monkeypatch.setattr("remedy.core.project_fingerprint.fingerprint_path", boom)
    out = await missions["tools"]["mission_start"](goal="Build")
    assert "Mission started" in out
    assert "auto verify_command" not in out


@pytest.mark.asyncio
async def test_an_explicit_verify_command_is_not_overridden(missions, monkeypatch):
    monkeypatch.setattr(
        "remedy.core.project_fingerprint.fingerprint_path",
        lambda root: type("F", (), {"suggest_verify": "pytest -q"})(),
    )
    await missions["tools"]["mission_start"](goal="Build", verify_command="make check")
    assert store_for(missions).latest("mission-session").verify_command == "make check"


@pytest.mark.asyncio
async def test_verify_command_as_a_list_is_a_real_shell_command(missions):
    """Grok sends arrays; .strip() on a list crashed, JSON text never ran tests."""
    await missions["tools"]["mission_start"](
        goal="Build", verify_command=["pytest -q"]
    )
    assert store_for(missions).latest("mission-session").verify_command == "pytest -q"


@pytest.mark.asyncio
async def test_verify_command_json_array_string_is_a_real_shell_command(missions):
    from remedy.skills.tool_registry import _coerce_handler_arguments
    handler = missions["tools"]["mission_start"]
    args = _coerce_handler_arguments(
        handler, {"goal": "Build", "verify_command": ["pytest -q"]}
    )
    await handler(**args)
    assert store_for(missions).latest("mission-session").verify_command == "pytest -q"


@pytest.mark.asyncio
async def test_verify_command_argv_list_joins_with_spaces(missions):
    await missions["tools"]["mission_start"](
        goal="Build", verify_command=["pytest", "-q"]
    )
    assert store_for(missions).latest("mission-session").verify_command == "pytest -q"


@pytest.mark.asyncio
async def test_goal_and_steps_as_lists_are_plain_text(missions):
    """Grok wraps string fields in arrays; .strip() on a list crashed Aug 21."""
    out = await missions["tools"]["mission_start"](
        goal=["Ship the parser"],
        steps=["write the test", "make it pass"],
        verify_command="true",
    )
    assert "Mission started" in out
    m = store_for(missions).latest("mission-session")
    assert m.goal == "Ship the parser"
    assert [s.title for s in m.steps] == ["write the test", "make it pass"]


@pytest.mark.asyncio
async def test_goal_tuple_joins_into_one_string(missions):
    await missions["tools"]["mission_start"](goal=("Ship", "the parser"))
    assert store_for(missions).latest("mission-session").goal == "Ship the parser"


def test_create_mission_does_not_strip_a_list_goal(missions):
    from remedy.core.mission import create_mission

    m = create_mission(
        ["Ship it"],
        steps=["one", ["nested", "step"]],
        home=str(missions["home"]),
    )
    assert m.goal == "Ship it"
    assert [s.title for s in m.steps] == ["one", "nested step"]


# --- status -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_without_a_mission_points_at_mission_start(missions):
    assert "mission_start" in await missions["tools"]["mission_status"]()


@pytest.mark.asyncio
async def test_status_finds_the_latest_mission_for_this_session(missions):
    await missions["tools"]["mission_start"](goal="First", verify_command="true")
    await missions["tools"]["mission_start"](goal="Second", verify_command="true")
    assert "Second" in await missions["tools"]["mission_status"]()


@pytest.mark.asyncio
async def test_a_mission_can_be_addressed_by_id(missions):
    await missions["tools"]["mission_start"](goal="First", verify_command="true")
    first = store_for(missions).latest("mission-session")
    await missions["tools"]["mission_start"](goal="Second", verify_command="true")
    assert "First" in await missions["tools"]["mission_status"](mission_id=first.id)


@pytest.mark.asyncio
async def test_an_unknown_id_is_reported_rather_than_silently_ignored(missions):
    await missions["tools"]["mission_start"](goal="First", verify_command="true")
    out = await missions["tools"]["mission_status"](mission_id="no-such-mission")
    assert "No active mission" in out


# --- the verify gate --------------------------------------------------------


@pytest.mark.asyncio
async def test_all_steps_done_is_not_done_while_verify_is_unproven(missions):
    """The whole point of a verify command."""
    await missions["tools"]["mission_start"](
        goal="Build", steps="one", verify_command="pytest -q"
    )
    out = await missions["tools"]["mission_update"](status="done")
    assert "Do not claim done yet" in out
    assert "pytest -q" in out


@pytest.mark.asyncio
async def test_status_repeats_the_warning(missions):
    await missions["tools"]["mission_start"](
        goal="Build", steps="one", verify_command="pytest -q"
    )
    await missions["tools"]["mission_update"](status="done")
    assert "run mission_verify" in await missions["tools"]["mission_status"]()


@pytest.mark.asyncio
async def test_a_mission_without_a_verify_command_has_nothing_to_gate(missions):
    await missions["tools"]["mission_start"](goal="Tidy the desk", steps="one")
    out = await missions["tools"]["mission_update"](status="done")
    assert "Do not claim done yet" not in out


@pytest.mark.asyncio
async def test_a_half_finished_checklist_is_not_gated_yet(missions):
    await missions["tools"]["mission_start"](
        goal="Build", steps="one\ntwo", verify_command="pytest -q"
    )
    out = await missions["tools"]["mission_update"](status="done")
    assert "Do not claim done yet" not in out


# --- updating ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_updating_without_a_mission_says_what_to_do(missions):
    assert "mission_start" in await missions["tools"]["mission_update"]()


@pytest.mark.asyncio
async def test_an_unknown_status_is_treated_as_done_not_rejected(missions):
    """A wrong word should not cost her the progress she just made."""
    await missions["tools"]["mission_start"](goal="Build", steps="one")
    await missions["tools"]["mission_update"](status="finito")
    assert store_for(missions).latest("mission-session").steps[0].status == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["done", "failed", "skipped", "active", "pending"])
async def test_the_real_statuses_are_all_accepted(missions, status):
    await missions["tools"]["mission_start"](goal="Build", steps="one")
    await missions["tools"]["mission_update"](status=status)
    assert store_for(missions).latest("mission-session").steps[0].status == status


@pytest.mark.asyncio
async def test_a_note_is_kept_with_the_step(missions):
    await missions["tools"]["mission_start"](goal="Build", steps="one")
    out = await missions["tools"]["mission_update"](status="failed", note="flaky DNS")
    assert "Mission updated" in out


# --- registration -----------------------------------------------------------


def test_every_mission_tool_is_registered(missions):
    assert set(missions["tools"]) >= {
        "mission_start",
        "mission_status",
        "mission_update",
        "mission_verify",
        "job_run",
    }


def test_the_schemas_are_objects(missions):
    for name, schema in missions["rt"].tool_registry.schemas.items():
        assert schema.get("type") == "object", name
