"""LLM tool args arrive JSON-typed; the registry coerces them onto the handler signature.

Yesterday's live session: ``todo_write(todos_json=[...])`` and
``mission_start(steps=[...])`` crashed with ``'list' object has no attribute
'strip'`` on every call because the tools declare ``str`` and grok sent arrays.
"""

from __future__ import annotations

import json

import pytest

from remedy.skills.tool_registry import ToolRegistry, _coerce_handler_arguments


async def _todo_like(todos_json: str = "", merge: bool = True) -> str:
    return f"{type(todos_json).__name__}:{todos_json}|merge={merge!r}"


def _sync_tool(goal="", steps: str = "", count: int = 1):
    return (type(goal).__name__, type(steps).__name__, type(count).__name__)


def test_list_for_str_param_becomes_json_text():
    out = _coerce_handler_arguments(_todo_like, {"todos_json": [{"id": "1"}]})
    assert isinstance(out["todos_json"], str)
    assert json.loads(out["todos_json"]) == [{"id": "1"}]


def test_dict_for_str_param_becomes_json_text():
    out = _coerce_handler_arguments(_todo_like, {"todos_json": {"todos": []}})
    assert json.loads(out["todos_json"]) == {"todos": []}


def test_string_for_bool_param_is_parsed():
    out = _coerce_handler_arguments(_todo_like, {"merge": "false"})
    assert out["merge"] is False
    out = _coerce_handler_arguments(_todo_like, {"merge": "True"})
    assert out["merge"] is True


def test_unannotated_param_uses_default_type():
    out = _coerce_handler_arguments(_sync_tool, {"goal": ["a", "b"], "steps": ["1", "2"], "count": "3"})
    assert json.loads(out["goal"]) == ["a", "b"]
    assert json.loads(out["steps"]) == ["1", "2"]
    assert out["count"] == 3


def test_none_and_already_typed_values_pass_through():
    out = _coerce_handler_arguments(_todo_like, {"todos_json": None, "merge": True})
    assert out == {"todos_json": None, "merge": True}


@pytest.mark.asyncio
async def test_registry_execute_coerces_before_calling_handler():
    reg = ToolRegistry()
    reg.register_handler("todo_write", _todo_like)
    res = await reg.execute("todo_write", todos_json=[{"id": "1", "content": "x"}], merge="yes")
    assert res.startswith("str:")
    assert res.endswith("merge=True")
