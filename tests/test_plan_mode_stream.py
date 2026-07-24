"""Plan mode enforcement + API stream flag."""

from __future__ import annotations

import pytest

from remedy.core.agent import BasicRuntime
from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES
from remedy.interfaces.api_models import SendMessageRequest
from remedy.models import AgentConfig, ToolCall


@pytest.mark.asyncio
async def test_plan_mode_allows_plan_save_blocks_bash():
    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x", home_dir="~/.remedy-test-plan"))
    rt._plan_mode = True
    # plan_save is allowed (may fail without full registry setup — just not PLAN_MODE_BLOCKED)
    # bash is blocked
    blocked = await rt.call_tool(
        ToolCall(tool_name="bash_exec", arguments={"command": "echo hi"})
    )
    assert blocked.success is False
    assert "PLAN_MODE" in (blocked.error or "") or "Plan mode" in (blocked.error or "")

    # allowlist includes plan tools
    assert "plan_save" in PLAN_MODE_TOOL_NAMES
    assert "checkpoint_save" not in PLAN_MODE_TOOL_NAMES  # build-only


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
