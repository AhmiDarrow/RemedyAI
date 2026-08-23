"""Workspace file/shell tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

import logging
from typing import Any

from remedy.core.workspace_tools.files import register_files_tools
from remedy.core.workspace_tools.help_tools import register_help_tools
from remedy.core.workspace_tools.search import register_search_tools
from remedy.core.workspace_tools.shell import register_shell_tools

_log = logging.getLogger(__name__)


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
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_spread_tools import register_spread_tools

        register_spread_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_hive_tools import register_hive_tools

        register_hive_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_web_tools import register_web_tools

        register_web_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_computer_tools import register_computer_tools

        register_computer_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_companion_tools import register_companion_tools

        register_companion_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_self_inject_tools import register_self_inject_tools

        register_self_inject_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_assistant_tools import register_assistant_tools

        register_assistant_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_reminder_tools import register_reminder_tools

        register_reminder_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_document_tools import register_document_tools

        register_document_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_settings_tools import register_settings_tools

        register_settings_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_telephony_tools import register_telephony_tools

        register_telephony_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_voice_tools import register_voice_tools

        register_voice_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_game_tools import register_game_tools

        register_game_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_analysis_tools import register_analysis_tools

        register_analysis_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_research_tools import register_research_tools

        register_research_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    try:
        from remedy.core.agent_science_tools import register_science_tools

        register_science_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    # Optional MCP client bridge: user-listed servers (config mcp_servers)
    # surface as mcp_<server>_<tool>; nothing spawns until first use.
    try:
        from remedy.core.agent_mcp_bridge import register_mcp_bridge

        register_mcp_bridge(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps = []
    runtime._learning_loop = None

