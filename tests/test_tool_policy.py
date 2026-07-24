"""Simple-query tool policy + agent budget (OpenCode-smooth)."""

from __future__ import annotations

from remedy.core.agent import (
    _MAX_PARALLEL_TOOLS,
    _MAX_REACT_STEPS,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
)


def test_max_tool_steps_has_headroom():
    # Real coding turns need headroom; simple turns never spend it.
    assert _MAX_REACT_STEPS >= 16
    assert _MAX_PARALLEL_TOOLS >= 4


def test_simple_questions_skip_tools():
    assert _message_wants_tools("hi") is False
    assert _message_wants_tools("what provider are we connected to") is False
    assert _message_wants_tools("what is max tool calls?") is False
    assert _message_wants_tools("what time is it in paris") is False
    assert _message_wants_tools("who are you?") is False


def test_project_tasks_enable_tools():
    assert _message_wants_tools("read config.toml") is True
    assert _message_wants_tools("list files in src/") is True
    assert _message_wants_tools("implement login in the project") is True
    assert _message_wants_tools("run the tests") is True
    assert _message_wants_tools("fix the bug in agent.py") is True
    assert _message_wants_tools("review project") is True
    assert _message_wants_tools("analyze the architecture") is True


def test_pseudo_tool_detection_and_parse():
    fake = (
        'I\'ll start by reading key files.\n\n'
        'file_read("pyproject.toml") && file_read("README.md") && list_dir("src/")'
    )
    assert _looks_like_pseudo_tools(fake) is True
    parsed = _parse_pseudo_tool_calls(fake)
    names = [((p.get("function") or {}).get("name")) for p in parsed]
    assert "file_read" in names
    assert "list_dir" in names
    assert _looks_like_pseudo_tools("Just a normal answer about tools.") is False


def test_tool_fingerprint_stable():
    a = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    b = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    c = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "b.py"}',
        }
    }
    assert _tool_call_fingerprint(a) == _tool_call_fingerprint(b)
    assert _tool_call_fingerprint(a) != _tool_call_fingerprint(c)
