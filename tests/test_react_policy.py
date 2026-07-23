"""Unit tests for ReAct policy helpers (tool gating, pseudo-tools, fingerprints)."""

from __future__ import annotations

import json

from remedy.core.react_policy import (
    looks_like_pseudo_tools,
    message_wants_tools,
    parse_pseudo_tool_calls,
    tool_call_fingerprint,
)


def test_message_wants_tools_chat_vs_code() -> None:
    assert message_wants_tools("hello!") is False
    assert message_wants_tools("what skills do you have?") is False
    assert message_wants_tools("list the files in src/") is True
    assert message_wants_tools("please review the codebase architecture") is True


def test_pseudo_tool_parse_and_log(caplog) -> None:
    text = 'file_read("README.md") && list_dir("src")'
    assert looks_like_pseudo_tools(text)
    calls = parse_pseudo_tool_calls(text)
    assert len(calls) == 2
    assert calls[0]["function"]["name"] == "file_read"
    args0 = json.loads(calls[0]["function"]["arguments"])
    assert args0["path"] == "README.md"
    assert calls[1]["function"]["name"] == "list_dir"


def test_tool_call_fingerprint_stable() -> None:
    a = {
        "function": {
            "name": "file_read",
            "arguments": '{"path": "a.py"}',
        }
    }
    b = {
        "function": {
            "name": "file_read",
            "arguments": {"path": "a.py"},
        }
    }
    assert tool_call_fingerprint(a) == tool_call_fingerprint(b)
