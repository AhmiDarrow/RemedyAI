"""Agency battery — mock tool-plane regression fence (no live provider).

Covers Build-class contracts from scripts/agency_battery and manual chapter 18:
file_edit uniqueness, mission short ids, plan-mode tool filter, tool pairing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.file_edit import apply_search_replace
from remedy.core.mission import MissionStore, advance_step, create_mission
from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
from remedy.core.react_stream import (
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    normalize_tool_calls,
    should_enable_tools,
    tool_call_fingerprint,
)
from remedy.models import AgentConfig


def test_file_edit_unique_replace() -> None:
    r = apply_search_replace(
        "def a():\n    return 1\n\ndef b():\n    return 2\n",
        "return 1",
        "return 42",
    )
    assert r.ok is True
    assert r.new_content is not None
    assert r.new_content.count("return 42") == 1


def test_file_edit_ambiguous_fails() -> None:
    r = apply_search_replace("x = 1\nx = 1\n", "x = 1", "x = 2")
    assert r.ok is False
    assert r.occurrences == 2
    assert "unique" in r.message.lower() or "matched" in r.message.lower()


def test_mission_short_id_prefix(tmp_path: Path) -> None:
    m = create_mission(
        "Ship agency tests",
        steps=["write tests", "run pytest"],
        verify_command="pytest -q",
        home=tmp_path,
    )
    store = MissionStore(tmp_path)
    full_id = m.id
    short = full_id[:8]
    got = store.get(short)
    assert got is not None
    assert got.id == full_id
    updated = advance_step(got, status="done", note="done writing")
    store.save(updated)
    got2 = store.get(full_id)
    assert got2 is not None
    assert any(s.status == "done" for s in got2.steps)


def test_plan_mode_tool_names_exclude_destructive() -> None:
    blocked = {
        "bash_exec",
        "host_run",
        "host_mkdir",
        "host_script",
        "file_write",
        "file_edit",
        "job_run",
        "computer_act",
        "computer_click",
        "computer_type",
        "computer_app",
        "skill_run",
    }
    assert not (blocked & set(PLAN_MODE_TOOL_NAMES))
    assert "plan_save" in PLAN_MODE_TOOL_NAMES
    assert "file_read" in PLAN_MODE_TOOL_NAMES
    assert "web_fetch" in PLAN_MODE_TOOL_NAMES
    assert "file_glob" in PLAN_MODE_TOOL_NAMES
    assert "host_which" in PLAN_MODE_TOOL_NAMES
    assert "todo_write" in PLAN_MODE_TOOL_NAMES
    assert "companion_context" in PLAN_MODE_TOOL_NAMES
    assert "clipboard_read" in PLAN_MODE_TOOL_NAMES
    assert "clipboard_write" not in PLAN_MODE_TOOL_NAMES
    assert "build_drive" not in PLAN_MODE_TOOL_NAMES


def test_should_enable_tools_for_build_intent() -> None:
    tools = [
        {
            "type": "function",
            "function": {"name": "file_edit", "parameters": {"type": "object"}},
        }
    ]
    # Build-class phrasing should keep tools available when schemas exist.
    assert (
        should_enable_tools(
            "implement the fix with file_edit",
            tools,
            has_attachments=False,
        )
        is True
    )


def test_fresh_tool_filter_breaks_loops() -> None:
    tc = {
        "id": "1",
        "type": "function",
        "function": {"name": "list_dir", "arguments": "{}"},
    }
    real_fp = tool_call_fingerprint(tc)
    seen = {real_fp}
    fresh = filter_fresh_tool_calls([tc], seen)
    assert fresh == []


def test_pairing_after_partial_batch() -> None:
    messages = [
        {"role": "user", "content": "build"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": normalize_tool_calls(
                [
                    {
                        "id": "c1",
                        "function": {"name": "repo_search", "arguments": '{"q":"foo"}'},
                    },
                    {
                        "id": "c2",
                        "function": {"name": "file_read", "arguments": '{"path":"a"}'},
                    },
                ]
            ),
        },
        {"role": "tool", "tool_call_id": "c1", "content": "hits"},
    ]
    fixed = ensure_tool_call_pairings(messages)
    ids = [m["tool_call_id"] for m in fixed if m.get("role") == "tool"]
    assert "c1" in ids and "c2" in ids


@pytest.mark.asyncio
async def test_runtime_has_tool_registry() -> None:
    rt = BasicRuntime(AgentConfig(llm_api_key="test-key", project_path="."))
    assert hasattr(rt, "tool_registry")
    # Tools may register lazily; registry itself must exist for agency surface.
    _ = rt._openai_tools()


def test_openai_tools_payload_cached_until_register() -> None:
    """L1/L2 turns share one schema build; register invalidates cache."""
    from remedy.core.agent_llm import openai_tools_payload
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    reg.register_builtin("a", "A tool", {"type": "object", "properties": {}})
    reg.register_builtin("b", "B tool", {"type": "object", "properties": {}})
    p1 = openai_tools_payload(reg)
    p2 = openai_tools_payload(reg)
    assert p1 is p2
    assert len(p1) == 2
    reg.register_builtin("c", "C tool", {"type": "object", "properties": {}})
    p3 = openai_tools_payload(reg)
    assert p3 is not p1
    assert len(p3) == 3
