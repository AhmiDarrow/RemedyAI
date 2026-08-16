"""Plan mode enforcement + API stream flag."""

from __future__ import annotations

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.computer.types import COMPUTER_PLAN_MODE_TOOLS, COMPUTER_TOOL_NAMES
from remedy.core.plan_store import PLAN_MODE_SYSTEM_ADDENDUM, PLAN_MODE_TOOL_NAMES
from remedy.interfaces.api_models import SendMessageRequest
from remedy.models import AgentConfig, ToolCall


def _is_plan_blocked(res) -> bool:
    err = res.error or ""
    return (not res.success) and ("PLAN_MODE" in err or "Plan mode" in err)


@pytest.mark.asyncio
async def test_plan_mode_allows_plan_save_blocks_bash():
    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x", home_dir="~/.remedy-test-plan"))
    rt._plan_mode = True
    # plan_save is allowed (may fail without full registry setup — just not PLAN_MODE_BLOCKED)
    # bash is blocked
    blocked = await rt.call_tool(
        ToolCall(tool_name="bash_exec", arguments={"command": "echo hi"})
    )
    assert _is_plan_blocked(blocked)

    # Multi-step computer_act is Build-only (click/type side effects)
    act_blocked = await rt.call_tool(
        ToolCall(
            tool_name="computer_act",
            arguments={"url": "https://example.com", "click": "OK", "type": "x"},
        )
    )
    assert _is_plan_blocked(act_blocked)
    assert "Build" in (act_blocked.error or "") or "click" in (act_blocked.error or "").lower()

    # allowlist includes plan tools
    assert "plan_save" in PLAN_MODE_TOOL_NAMES
    assert "checkpoint_save" not in PLAN_MODE_TOOL_NAMES  # build-only
    assert "computer_act" not in PLAN_MODE_TOOL_NAMES


@pytest.mark.asyncio
async def test_plan_mode_computer_matrix_and_help():
    """Soak matrix: observe/navigate allowed; click/type blocked; F1 help allowed."""
    rt = BasicRuntime(
        AgentConfig(name="t", llm_api_key="x", home_dir="~/.remedy-test-plan-matrix")
    )
    rt._plan_mode = True

    assert "help_list" in PLAN_MODE_TOOL_NAMES
    assert "help_read" in PLAN_MODE_TOOL_NAMES
    assert COMPUTER_PLAN_MODE_TOOLS <= PLAN_MODE_TOOL_NAMES

    for name in sorted(COMPUTER_TOOL_NAMES - COMPUTER_PLAN_MODE_TOOLS):
        res = await rt.call_tool(ToolCall(tool_name=name, arguments={}))
        assert _is_plan_blocked(res), f"{name} must be PLAN_MODE_BLOCKED"
        assert "Build" in (res.error or "") or "Ctrl+B" in (res.error or "")

    for name in sorted(COMPUTER_PLAN_MODE_TOOLS):
        res = await rt.call_tool(ToolCall(tool_name=name, arguments={}))
        assert not _is_plan_blocked(res), (
            f"{name} must not be PLAN_MODE_BLOCKED (got {res.error!r})"
        )

    # F1 help never out of scope in Plan
    help_blocked = await rt.call_tool(ToolCall(tool_name="help_list", arguments={}))
    assert not _is_plan_blocked(help_blocked)

    addendum = PLAN_MODE_SYSTEM_ADDENDUM.lower()
    assert "computer_click" in addendum or "click" in addendum
    assert "snapshot" in addendum or "observe" in addendum
    assert "help_list" in addendum or "help_read" in addendum or "f1" in addendum


def test_send_message_request_has_plan_mode():
    m = SendMessageRequest(message="hi", plan_mode=True)
    assert m.plan_mode is True
    m2 = SendMessageRequest(message="hi")
    assert m2.plan_mode is False


def test_plan_mode_field_roundtrip_json():
    """API model accepts plan_mode from desktop stream body."""
    raw = {"message": "plan this", "plan_mode": True, "model": None}
    m = SendMessageRequest.model_validate(raw)
    assert m.plan_mode is True
    assert m.message == "plan this"
