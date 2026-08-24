"""Recovered tool calls must name a tool that exists.

``parse_pseudo_tool_calls`` reconstructs tool calls from text when a model
emits them as prose or markup instead of native ``tool_calls``. Recovery is a
guess, and it was trusted unconditionally — so when a parser branch locked onto
wire-format scaffolding it produced calls named ``auto`` and ``name``, which the
runtime dutifully dispatched:

    Error [TOOL_VALUE_ERROR:auto]: No handler registered for tool: auto
    Error [TOOL_VALUE_ERROR:name]: No handler registered for tool: name

``auto`` is a ``tool_choice`` value and ``name`` is a JSON key; no registry
contains either. Each invented call burned a step and returned an error the
model then had to reason about.

Recovery is now validated twice: structural keywords are never tool names, and
when the caller passes the armed tool set anything outside it is dropped.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.react_policy import (
    is_impossible_tool_name,
    parse_pseudo_tool_calls,
)

ARMED = {"file_read", "file_write", "list_dir", "bash_exec"}


def _names(calls: list[dict]) -> list[str]:
    return [c["function"]["name"] for c in calls]


@pytest.mark.parametrize(
    "name",
    ["auto", "none", "required", "name", "arguments", "tool_choice", "type", "AUTO"],
)
def test_wire_format_keywords_are_never_tool_names(name: str) -> None:
    assert is_impossible_tool_name(name)


@pytest.mark.parametrize("name", ["file_read", "file_write", "comfyui", "host_run"])
def test_real_tool_names_are_allowed(name: str) -> None:
    assert not is_impossible_tool_name(name)


def test_blank_is_impossible() -> None:
    assert is_impossible_tool_name("")
    assert is_impossible_tool_name(None)


def test_genuine_recovery_still_works() -> None:
    text = '```json\n{"name": "file_read", "arguments": {"path": "a.py"}}\n```'
    calls = parse_pseudo_tool_calls(text, ARMED)
    assert _names(calls) == ["file_read"]
    assert json.loads(calls[0]["function"]["arguments"])["path"] == "a.py"


def test_scaffolding_name_is_rejected_even_without_an_allowlist() -> None:
    text = '```json\n{"name": "auto", "arguments": {}}\n```'
    assert parse_pseudo_tool_calls(text) == []


def test_unarmed_tool_is_dropped_when_allowlist_given() -> None:
    text = '```json\n{"name": "send_email", "arguments": {"to": "x"}}\n```'
    assert parse_pseudo_tool_calls(text, ARMED) == []


def test_no_allowlist_keeps_plausible_unknown_names() -> None:
    """Without an armed set we cannot judge — only scaffolding is refused."""
    text = '```json\n{"name": "send_email", "arguments": {"to": "x"}}\n```'
    assert _names(parse_pseudo_tool_calls(text)) == ["send_email"]


def test_mixed_batch_keeps_only_the_valid_call() -> None:
    text = (
        '```json\n{"name": "auto", "arguments": {}}\n```\n'
        '```json\n{"name": "list_dir", "arguments": {"path": "."}}\n```'
    )
    assert _names(parse_pseudo_tool_calls(text, ARMED)) == ["list_dir"]


def test_empty_text_is_safe() -> None:
    assert parse_pseudo_tool_calls("", ARMED) == []
