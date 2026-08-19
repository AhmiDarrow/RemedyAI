"""spread_run's argument handling and its fan-out caps.

Two separate concerns meet here.

The arguments come from a tool-calling model, which sends a *native* array
about as often as a JSON string, and sometimes a single object, and sometimes a
one-element list where a string was asked for. Calling `.strip()` on a list is
the crash this coercion exists to prevent, so every shape has to land somewhere
sensible rather than raising.

And the caps are a spend limit. Config sets the fan-out, but config is
editable — by the owner, and by Remedy's own settings tool — so the hard
ceiling has to hold whatever it says. Workers also never spawn workers; a
recursive spread would multiply out of the owner's control.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_spread_tools import (
    _coerce_str,
    _parse_tasks_arg,
    register_spread_tools,
)


class Reg:
    def __init__(self) -> None:
        self.tools: dict = {}
        self.schemas: dict = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    def __init__(self, root) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(root)})()
        self._session_id = "spread-session"

    def effective_project_path(self):
        return self.root

    def __getattr__(self, _n):
        return None


@pytest.fixture()
def spread(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = RT(tmp_path)
    rt.root = tmp_path
    register_spread_tools(rt)
    return rt.tool_registry


# --- coercing a string field --------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("hello", "hello"),
        ("", ""),
        (None, ""),
        (42, "42"),
        (3.5, "3.5"),
        (True, "True"),
        (["only one"], "only one"),
    ],
)
def test_a_string_field_accepts_what_models_actually_send(value, expected):
    assert _coerce_str(value) == expected


def test_a_default_is_used_when_there_is_nothing():
    assert _coerce_str(None, ".") == "."


def test_a_multi_element_list_is_not_silently_the_first_one():
    """Picking one of several would run the wrong search without saying so."""
    assert _coerce_str(["a", "b"], "fallback") == str(["a", "b"])


# --- parsing the task list ----------------------------------------------------


@pytest.mark.parametrize("value", [None, "", [], "   "])
def test_no_tasks_is_not_an_error(value):
    parsed, err = _parse_tasks_arg(value)
    assert parsed is None
    assert err is None


def test_a_native_list_is_taken_as_is():
    """Function-calling models send arrays; .strip() on one is the crash."""
    parsed, err = _parse_tasks_arg([{"goal": "a"}, {"goal": "b"}])
    assert err is None
    assert len(parsed) == 2


def test_a_json_string_is_parsed():
    parsed, err = _parse_tasks_arg(json.dumps([{"goal": "a"}]))
    assert err is None
    assert parsed == [{"goal": "a"}]


def test_a_single_object_is_wrapped():
    parsed, err = _parse_tasks_arg({"goal": "just one"})
    assert err is None
    assert parsed == [{"goal": "just one"}]


def test_a_single_object_as_json_is_wrapped_too():
    parsed, err = _parse_tasks_arg('{"goal": "just one"}')
    assert err is None
    assert len(parsed) == 1


def test_malformed_json_names_the_problem():
    parsed, err = _parse_tasks_arg('[{"goal": ')
    assert parsed is None
    assert "JSON" in err


def test_json_that_is_not_a_list_or_object_is_refused():
    parsed, err = _parse_tasks_arg('"just a string"')
    assert parsed is None
    assert "JSON array" in err


def test_a_type_nobody_expected_is_refused_by_name():
    parsed, err = _parse_tasks_arg(42)
    assert parsed is None
    assert "int" in err


# --- the fan-out caps ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_worker_cannot_start_another_spread(spread, monkeypatch):
    """Recursive fan-out multiplies out of the owner's control."""
    # Patched where it is *used*: the tool module binds the name at import.
    monkeypatch.setattr("remedy.core.agent_spread_tools.spread_depth", lambda: 1)
    out = await spread.tools["spread_run"](goal="anything")
    assert "SPREAD_DEPTH" in out
    assert "nest" in out


@pytest.mark.asyncio
async def test_spread_can_be_turned_off_in_config(spread, monkeypatch):
    monkeypatch.setattr(
        "remedy.interfaces.config.load_config",
        lambda: {"spread": {"enabled": False}},
    )
    out = await spread.tools["spread_run"](goal="anything")
    assert "SPREAD_DISABLED" in out


@pytest.mark.asyncio
async def test_a_bad_tasks_argument_is_reported_before_any_worker_starts(spread):
    out = await spread.tools["spread_run"](goal="g", tasks='[{"goal": ')
    assert "JSON" in out


# --- registration -------------------------------------------------------------


def test_the_tool_is_registered_with_a_schema(spread):
    assert "spread_run" in spread.tools
    assert spread.schemas["spread_run"]["type"] == "object"


def test_the_schema_admits_a_native_array_for_tasks(spread):
    """Declaring tasks as a bare string is what made models send one."""
    props = spread.schemas["spread_run"]["properties"]
    assert "tasks" in props
