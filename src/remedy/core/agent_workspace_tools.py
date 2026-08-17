"""Workspace file/shell tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

from typing import Any

from remedy.core.workspace_tools.files import register_files_tools
from remedy.core.workspace_tools.help_tools import register_help_tools
from remedy.core.workspace_tools.search import register_search_tools
from remedy.core.workspace_tools.shell import register_shell_tools


def register_workspace_tools(runtime: Any) -> None:
    """Register file/shell tools jailed to the project workspace."""
    register_help_tools(runtime)
    register_files_tools(runtime)
    register_search_tools(runtime)
    register_shell_tools(runtime)
    runtime._register_comfyui_tools()
    runtime._register_vision_tools()
    runtime._register_local_discover_tools()
    runtime._register_skill_tools()
    try:
        from remedy.core.agent_mission_tools import register_mission_tools

        register_mission_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_spread_tools import register_spread_tools

        register_spread_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_web_tools import register_web_tools

        register_web_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_computer_tools import register_computer_tools

        register_computer_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_companion_tools import register_companion_tools

        register_companion_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_self_inject_tools import register_self_inject_tools

        register_self_inject_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_assistant_tools import register_assistant_tools

        register_assistant_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_reminder_tools import register_reminder_tools

        register_reminder_tools(runtime)
    except Exception:
        pass
    try:
        from remedy.core.agent_settings_tools import register_settings_tools


        register_settings_tools(runtime)
    except Exception:
        pass
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps = []
    runtime._learning_loop = None

