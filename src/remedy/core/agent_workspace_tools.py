"""Workspace file/shell tool registration (extracted from BasicRuntime).

Phase 6 absolute: optional agent_* product families are retired from this
registrar. Production tools live on the Go Tool ABI; tests that need a
workspace jail still get files/help/search/shell (+ skill/local hooks).
"""

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
    # Still useful for BasicRuntime tests / local dogfood harnesses.
    for meth in (
        "_register_comfyui_tools",
        "_register_vision_tools",
        "_register_local_discover_tools",
        "_register_rmb_tools",
        "_register_skill_tools",
    ):
        fn = getattr(runtime, meth, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                _log.exception("optional tool family failed to register: %s", meth)
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
        from remedy.core.agent_voice_tools import register_voice_tools

        register_voice_tools(runtime)
    except Exception:
        _log.exception("optional tool family failed to register")
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps = []
    runtime._learning_loop = None
