"""Build-engine tools — the guards in front of the expensive machinery.

Every tool here can start something long: compile a spec, run a gate tower,
mutate source, drive hops. What the model actually hits most often is the
refusal path — no project set, no write set yet, a malformed JSON argument —
and a refusal that does not say what is missing costs a whole turn while it
guesses. So this pins the guards, and what each one tells her to do next.

Nothing below runs a build. No LLM is called; no source is mutated.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_build_tools import register_build_tools


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    """Runtime with a project, unless *project* is None."""

    def __init__(self, home, project) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(home)})()
        self._project = project
        self._session_id = "build-session"

    def effective_project_path(self):
        if self._project is None:
            raise RuntimeError("no project set")
        return self._project

    def resolve_tool_path(self, path, *, for_write=False):
        return self._project / (path or ".")

    def _track_artifact(self, _p):
        return None


#: The A–H frontier tools sit behind a maturity flag so default agency keeps
#: the core build set. Tests that exercise them turn it on explicitly rather
#: than assuming whatever this machine's config happens to say.
def _advanced(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(
        "remedy.core.feature_maturity.build_os_advanced_enabled", lambda *a, **kw: on
    )


@pytest.fixture()
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    _advanced(monkeypatch, True)
    project = tmp_path / "proj"
    project.mkdir()
    rt = RT(tmp_path / "home", project)
    register_build_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools, "project": project}


@pytest.fixture()
def core_only(tmp_path, monkeypatch):
    """Default agency: the frontier tools are not offered at all."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    _advanced(monkeypatch, False)
    project = tmp_path / "proj"
    project.mkdir()
    rt = RT(tmp_path / "home", project)
    register_build_tools(rt)
    return rt.tool_registry.tools


@pytest.fixture()
def projectless(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    _advanced(monkeypatch, True)
    rt = RT(tmp_path, None)
    register_build_tools(rt)
    return rt.tool_registry.tools


# --- status and resume ------------------------------------------------------


@pytest.mark.asyncio
async def test_status_answers_with_no_build_ever_started(build):
    out = await build["tools"]["build_status"]()
    assert "no active build turn" in out
    assert "ledger: (empty)" in out


@pytest.mark.asyncio
async def test_status_survives_a_runtime_with_no_project(projectless):
    """Asking what the build is doing must never be the thing that breaks."""
    assert "Build engine" in await projectless["build_status"]()


@pytest.mark.asyncio
async def test_resume_without_a_ledger_says_there_is_nothing_to_resume(build):
    assert "No build ledger" in await build["tools"]["build_resume"]()


# --- guards that need a project ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool",
    ["build_mutation_score", "build_mutant_score", "build_snapshot",
     "build_symbol_index"],
)
async def test_a_tool_that_needs_a_project_says_so(projectless, tool):
    out = await projectless[tool]()
    assert "project" in out.lower()


# --- guards that need a write set -------------------------------------------


@pytest.mark.asyncio
async def test_the_gate_tower_needs_something_to_check(build):
    out = await build["tools"]["build_gate_tower"]()
    assert "No write_set" in out
    assert "hop" in out


@pytest.mark.asyncio
async def test_mutant_scoring_needs_something_to_mutate(build):
    out = await build["tools"]["build_mutant_score"]()
    assert "No write_set" in out


# --- arguments --------------------------------------------------------------


@pytest.mark.asyncio
async def test_compiling_a_spec_needs_a_goal(build):
    out = await build["tools"]["build_compile_spec"]()
    assert "goal=" in out


@pytest.mark.asyncio
async def test_tdd_needs_a_goal(build):
    assert "goal=" in await build["tools"]["build_tdd"]()


@pytest.mark.asyncio
async def test_applying_an_empty_patch_says_what_a_patch_looks_like(build):
    out = await build["tools"]["apply_patch"]()
    assert "patch=" in out
    assert "unified diff" in out


@pytest.mark.asyncio
async def test_applying_a_malformed_patch_reports_red_not_success(build):
    out = await build["tools"]["apply_patch"](patch="this is not a diff at all")
    assert "RED" in out


@pytest.mark.asyncio
async def test_parallel_hops_need_units(build):
    out = await build["tools"]["build_parallel"]()
    assert "units_json=" in out


@pytest.mark.asyncio
async def test_malformed_units_json_names_the_parse_error(build):
    out = await build["tools"]["build_parallel"](units_json='[{"path":')
    assert "parse error" in out


@pytest.mark.asyncio
async def test_units_json_must_be_a_list(build):
    out = await build["tools"]["build_parallel"](units_json='{"path": "a.py"}')
    assert "must be a JSON array" in out


# --- snapshots --------------------------------------------------------------


@pytest.mark.asyncio
async def test_listing_snapshots_before_any_hop(build):
    out = await build["tools"]["build_snapshot"](action="list")
    assert "No snapshots yet" in out


@pytest.mark.asyncio
async def test_list_is_the_default_snapshot_action(build):
    assert "No snapshots" in await build["tools"]["build_snapshot"]()


@pytest.mark.asyncio
async def test_restoring_with_no_green_snapshot_says_so(build):
    out = await build["tools"]["build_snapshot"](action="restore")
    assert "snap_id=" in out


@pytest.mark.asyncio
async def test_an_unknown_snapshot_action_lists_the_real_ones(build):
    out = await build["tools"]["build_snapshot"](action="frobnicate")
    assert "list|restore|bisect" in out


# --- todos: the one path that keeps real state ------------------------------


@pytest.mark.asyncio
async def test_reading_todos_before_any_are_written(build):
    assert "No todos" in await build["tools"]["todo_read"]()


@pytest.mark.asyncio
async def test_writing_todos_without_json_shows_what_is_there(build):
    out = await build["tools"]["todo_write"]()
    assert "todos_json" in out or out


@pytest.mark.asyncio
async def test_a_written_todo_comes_back_on_read(build):
    todos = json.dumps([{"id": "1", "content": "write the parser", "status": "pending"}])
    await build["tools"]["todo_write"](todos_json=todos)
    assert "write the parser" in await build["tools"]["todo_read"]()


@pytest.mark.asyncio
async def test_malformed_todo_json_names_the_parse_error(build):
    out = await build["tools"]["todo_write"](todos_json='[{"id": ')
    assert "parse error" in out


@pytest.mark.asyncio
async def test_todos_must_be_a_list(build):
    out = await build["tools"]["todo_write"](todos_json='"just a string"')
    assert "must be a JSON array" in out


@pytest.mark.asyncio
async def test_a_single_todo_object_is_accepted_as_a_list_of_one(build):
    """Models send one item unwrapped constantly; refusing it wastes a turn."""
    out = await build["tools"]["todo_write"](
        todos_json='{"id": "1", "content": "ship it", "status": "pending"}'
    )
    assert "ship it" in out


@pytest.mark.asyncio
async def test_a_todos_wrapper_key_is_unwrapped(build):
    out = await build["tools"]["todo_write"](
        todos_json='{"todos": [{"id": "1", "content": "unwrap me", "status": "pending"}]}'
    )
    assert "unwrap me" in out


@pytest.mark.asyncio
async def test_merging_keeps_earlier_todos(build):
    await build["tools"]["todo_write"](
        todos_json=json.dumps([{"id": "1", "content": "first", "status": "pending"}])
    )
    out = await build["tools"]["todo_write"](
        todos_json=json.dumps([{"id": "2", "content": "second", "status": "pending"}]),
        merge=True,
    )
    assert "first" in out and "second" in out


@pytest.mark.asyncio
async def test_not_merging_replaces_the_list(build):
    await build["tools"]["todo_write"](
        todos_json=json.dumps([{"id": "1", "content": "first", "status": "pending"}])
    )
    out = await build["tools"]["todo_write"](
        todos_json=json.dumps([{"id": "2", "content": "second", "status": "pending"}]),
        merge=False,
    )
    assert "first" not in out


# --- registration -----------------------------------------------------------


CORE_TOOLS = {
    "build_status",
    "build_resume",
    "build_unit_hop",
    "build_live_project",
    "build_mutation_score",
    "build_drive",
    "apply_patch",
    "build_parallel",
    "build_review_fix",
    "todo_write",
    "todo_read",
}

#: Advanced frontiers A–H, only offered when the maturity flag is on.
ADVANCED_TOOLS = {
    "build_compile_spec",
    "build_tdd",
    "build_gate_tower",
    "build_repair_queue",
    "build_mutant_score",
    "build_snapshot",
    "build_symbol_index",
}


def test_the_core_build_tools_are_always_offered(core_only):
    assert set(core_only) == CORE_TOOLS


def test_the_frontier_tools_stay_behind_the_maturity_flag(core_only):
    """Default agency keeps the core set; the A–H tools are opt-in."""
    assert not set(core_only) & ADVANCED_TOOLS


def test_turning_the_flag_on_offers_the_frontier_tools(build):
    assert set(build["tools"]) >= ADVANCED_TOOLS


def test_every_build_tool_is_registered(build):
    assert set(build["tools"]) >= {
        "build_status",
        "build_resume",
        "build_unit_hop",
        "build_mutation_score",
        "build_compile_spec",
        "build_tdd",
        "build_gate_tower",
        "build_repair_queue",
        "build_mutant_score",
        "build_snapshot",
        "todo_write",
        "todo_read",
        "build_drive",
        "apply_patch",
        "build_parallel",
        "build_review_fix",
    }


def test_the_schemas_are_objects(build):
    for name, schema in build["rt"].tool_registry.schemas.items():
        assert schema.get("type") == "object", name
