"""Plugin & hook system for Remedy extensibility.

Enables third-party modules to hook into the Remedy lifecycle:
- startup / shutdown hooks
- pre/post tool execution hooks
- event filters
- custom channel adapters
- custom tool handlers
"""

from __future__ import annotations

import importlib
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class HookRegistration:
    name: str
    handler: Callable
    priority: int = 0
    source: str = "unknown"


class HookManager:
    """Lightweight pub/sub hook system.

    Lifecycle hooks:
        on_startup, on_shutdown
    Event hooks:
        pre_tool_exec(tool_name, arguments, context) -> bool|None
        post_tool_exec(tool_name, result, context) -> None
        on_event(event) -> None
        on_memory_save(entry) -> None
        on_skill_loaded(skill) -> None
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookRegistration]] = defaultdict(list)

    # -- registration --------------------------------------------------------

    def register(
        self,
        hook_name: str,
        handler: Callable,
        priority: int = 0,
        source: str = "unknown",
    ) -> HookRegistration:
        reg = HookRegistration(name=hook_name, handler=handler, priority=priority, source=source)
        self._hooks[hook_name].append(reg)
        self._hooks[hook_name].sort(key=lambda r: -r.priority)
        return reg

    def unregister(self, hook_name: str, handler: Callable) -> bool:
        before = len(self._hooks.get(hook_name, []))
        self._hooks[hook_name] = [
            r for r in self._hooks.get(hook_name, [])
            if r.handler is not handler
        ]
        return len(self._hooks[hook_name]) < before

    def clear(self, hook_name: Optional[str] = None) -> None:
        if hook_name:
            self._hooks.pop(hook_name, None)
        else:
            self._hooks.clear()

    # -- synchronous invocation ----------------------------------------------

    def fire(self, hook_name: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Fire a hook and collect return values (non-None)."""
        results: list[Any] = []
        for reg in self._hooks.get(hook_name, []):
            try:
                result = reg.handler(*args, **kwargs)
                if result is not None:
                    results.append(result)
            except Exception:
                logger.exception("Hook %s/%s failed", hook_name, reg.name)
        return results

    def fire_chain(self, hook_name: str, *args: Any, **kwargs: Any) -> bool:
        """Fire a hook chain where any handler returning False short-circuits."""
        for reg in self._hooks.get(hook_name, []):
            try:
                result = reg.handler(*args, **kwargs)
                if result is False:
                    return False
            except Exception:
                logger.exception("Hook chain %s/%s failed", hook_name, reg.name)
        return True

    # -- async invocation ----------------------------------------------------

    async def fire_async(self, hook_name: str, *args: Any, **kwargs: Any) -> list[Any]:
        import asyncio
        results: list[Any] = []
        for reg in self._hooks.get(hook_name, []):
            try:
                result = reg.handler(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                if result is not None:
                    results.append(result)
            except Exception:
                logger.exception("Async hook %s/%s failed", hook_name, reg.name)
        return results

    # -- introspection -------------------------------------------------------

    def list_hooks(self) -> dict[str, int]:
        return {name: len(regs) for name, regs in self._hooks.items()}

    def list_handlers(self, hook_name: str) -> list[dict]:
        return [
            {"name": r.name, "priority": r.priority, "source": r.source}
            for r in self._hooks.get(hook_name, [])
        ]


class PluginManager:
    """Discovers and loads plugin modules from configured plugin paths.

    Plugins can be Python packages or single .py files. Each plugin
    module can register hooks, tools, skills, or channel adapters.
    """

    def __init__(self, hooks: HookManager) -> None:
        self.hooks = hooks
        self._loaded: dict[str, Any] = {}

    def discover(self, plugin_paths: list[str]) -> list[str]:
        """Discover plugin modules in given directories. Returns module names."""
        found: list[str] = []
        for pp in plugin_paths:
            p = Path(pp).expanduser().resolve()
            if not p.exists():
                continue
            if p.is_file() and p.suffix == ".py":
                found.append(p.stem)
            elif p.is_dir():
                for entry in sorted(p.iterdir()):
                    if entry.suffix == ".py" and not entry.name.startswith("_"):
                        found.append(entry.stem)
                    elif entry.is_dir() and (entry / "__init__.py").exists():
                        found.append(entry.name)
        return found

    def load(self, plugin_name: str, plugin_path: Optional[str] = None) -> bool:
        """Import a plugin module by name."""
        if plugin_name in self._loaded:
            return True

        try:
            if plugin_path:
                sys.path.insert(0, plugin_path)
            module = importlib.import_module(plugin_name)
            self._loaded[plugin_name] = module

            # Call setup if the plugin defines it
            if hasattr(module, "setup_plugin"):
                module.setup_plugin(self.hooks)
                logger.info("Plugin %s setup complete", plugin_name)
            else:
                logger.info("Plugin %s loaded", plugin_name)

            return True
        except Exception:
            logger.exception("Failed to load plugin %s", plugin_name)
            return False
        finally:
            if plugin_path and plugin_path in sys.path:
                sys.path.remove(plugin_path)

    def unload(self, plugin_name: str) -> bool:
        module = self._loaded.pop(plugin_name, None)
        if module and hasattr(module, "teardown_plugin"):
            try:
                module.teardown_plugin()
            except Exception:
                logger.exception("Plugin %s teardown failed", plugin_name)
        return module is not None

    def reload_all(self) -> int:
        count = 0
        for name in list(self._loaded.keys()):
            if self.unload(name) and self.load(name):
                count += 1
        return count

    @property
    def loaded_plugins(self) -> list[str]:
        return list(self._loaded.keys())
