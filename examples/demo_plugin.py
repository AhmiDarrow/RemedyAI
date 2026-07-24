"""Demo Remedy plugin — logs tool use and marks startup.

Load with PluginManager:

    from remedy.interfaces.plugin import HookManager, PluginManager
    hooks = HookManager()
    pm = PluginManager(hooks)
    pm.load("demo_plugin", plugin_path="examples")  # or full path to parent dir

Or drop this file into ``~/.remedy/plugins/`` and discover that directory.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("remedy.plugin.demo")

# Populated by setup_plugin for tests / inspection.
SETUP_CALLS: list[str] = []
TOOL_EVENTS: list[tuple[str, str]] = []


def setup_plugin(hooks: Any) -> None:
    """Register lifecycle hooks (required entrypoint)."""
    SETUP_CALLS.append("setup")

    def on_startup() -> str:
        logger.info("demo_plugin: on_startup")
        SETUP_CALLS.append("startup")
        return "demo-started"

    def pre_tool(tool_name: str, arguments: dict, context: Any = None) -> bool | None:
        TOOL_EVENTS.append(("pre", tool_name))
        logger.debug("demo_plugin pre_tool %s %s", tool_name, arguments)
        return None  # allow

    def post_tool(tool_name: str, result: Any, context: Any = None) -> None:
        TOOL_EVENTS.append(("post", tool_name))
        logger.debug("demo_plugin post_tool %s", tool_name)

    hooks.register("on_startup", on_startup, priority=10, source="demo_plugin")
    hooks.register("pre_tool_exec", pre_tool, priority=0, source="demo_plugin")
    hooks.register("post_tool_exec", post_tool, priority=0, source="demo_plugin")


def teardown_plugin() -> None:
    SETUP_CALLS.append("teardown")
