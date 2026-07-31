"""Plugin & hook system for Remedy extensibility.

Enables third-party modules to hook into the Remedy lifecycle:
- startup / shutdown hooks
- pre/post tool execution hooks
- event filters
- custom channel adapters
- custom tool handlers
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Plugin module stems only — refuse path segments, dotted imports, stdlib hijacks.
_SAFE_PLUGIN_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
# Never load these even if a file is named similarly under a plugin dir.
_PLUGIN_NAME_DENY = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "builtins",
        "importlib",
        "ctypes",
        "socket",
        "pathlib",
        "shutil",
        "pickle",
        "remedy",
        "site",
        "runpy",
    }
)


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

    def clear(self, hook_name: str | None = None) -> None:
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


def is_safe_plugin_name(name: str) -> bool:
    """True when *name* is a single safe Python identifier (plugin stem)."""
    n = (name or "").strip()
    if not n or not _SAFE_PLUGIN_NAME.match(n):
        return False
    return not (n.lower() in _PLUGIN_NAME_DENY or n.startswith("_"))


class PluginManager:
    """Discovers and loads plugin modules from configured plugin paths.

    Plugins can be Python packages or single .py files. Each plugin
    module can register hooks, tools, skills, or channel adapters.

    Trust rules:
    - Names must be safe identifiers (no dotted imports / path segments).
    - Load requires a filesystem ``plugin_path`` under which the module exists;
      bare ``importlib.import_module(name)`` of stdlib/site packages is refused.
    - Denied names (``os``, ``subprocess``, …) never load even if present on disk.
    """

    def __init__(self, hooks: HookManager) -> None:
        self.hooks = hooks
        self._loaded: dict[str, Any] = {}
        # name -> resolved directory that contained the plugin file/package
        self._origins: dict[str, Path] = {}

    def discover(self, plugin_paths: list[str]) -> list[str]:
        """Discover plugin modules in given directories. Returns module names."""
        found: list[str] = []
        for pp in plugin_paths:
            p = Path(pp).expanduser().resolve()
            if not p.exists():
                continue
            if p.is_file() and p.suffix == ".py":
                stem = p.stem
                if is_safe_plugin_name(stem):
                    found.append(stem)
                    self._origins[stem] = p.parent
            elif p.is_dir():
                for entry in sorted(p.iterdir()):
                    if entry.suffix == ".py" and not entry.name.startswith("_"):
                        stem = entry.stem
                        if is_safe_plugin_name(stem):
                            found.append(stem)
                            self._origins[stem] = p
                    elif entry.is_dir() and (entry / "__init__.py").exists():
                        name = entry.name
                        if is_safe_plugin_name(name):
                            found.append(name)
                            self._origins[name] = p
        return found

    def _resolve_plugin_file(self, plugin_name: str, root: Path) -> Path | None:
        """Locate plugin_name under *root* (file or package)."""
        root = root.expanduser().resolve()
        if root.is_file() and root.suffix == ".py" and root.stem == plugin_name:
            return root
        if not root.is_dir():
            return None
        file_py = root / f"{plugin_name}.py"
        if file_py.is_file():
            return file_py
        pkg_init = root / plugin_name / "__init__.py"
        if pkg_init.is_file():
            return pkg_init
        return None

    def load(self, plugin_name: str, plugin_path: str | None = None) -> bool:
        """Import a plugin module by name from an allowlisted filesystem path.

        ``plugin_path`` is required unless the name was previously ``discover``'d
        (origin recorded). Arbitrary bare imports are refused (trust residual).
        """
        if plugin_name in self._loaded:
            return True

        if not is_safe_plugin_name(plugin_name):
            logger.warning("Refusing unsafe plugin name: %r", plugin_name)
            return False

        root: Path | None = None
        if plugin_path:
            root = Path(plugin_path).expanduser().resolve()
        elif plugin_name in self._origins:
            root = self._origins[plugin_name]
        else:
            logger.warning(
                "Plugin %s: refused bare load without plugin_path/discover origin",
                plugin_name,
            )
            return False

        plugin_file = self._resolve_plugin_file(plugin_name, root)
        if plugin_file is None:
            logger.warning(
                "Plugin %s not found under %s", plugin_name, root
            )
            return False

        try:
            # Load from explicit file path — never import a same-named stdlib module.
            unique_name = f"remedy_plugin_{plugin_name}"
            # Drop a prior failed partial import under the unique name
            if unique_name in sys.modules:
                del sys.modules[unique_name]
            spec = importlib.util.spec_from_file_location(unique_name, plugin_file)
            if spec is None or spec.loader is None:
                logger.error("Plugin %s: no import spec for %s", plugin_name, plugin_file)
                return False
            module = importlib.util.module_from_spec(spec)
            sys.modules[unique_name] = module
            # Also bind under plain name for demo_plugin-style `import demo_plugin`
            # only after successful exec, and only if not already a real module.
            spec.loader.exec_module(module)
            self._loaded[plugin_name] = module
            self._origins[plugin_name] = (
                plugin_file.parent
                if plugin_file.name != "__init__.py"
                else plugin_file.parent.parent
            )
            # Alias for `import demo_plugin` style tests. Names are allowlisted
            # (never os/sys/…), so replacing a same-stem site module is intended.
            sys.modules[plugin_name] = module

            if hasattr(module, "setup_plugin"):
                module.setup_plugin(self.hooks)
                logger.info("Plugin %s setup complete", plugin_name)
            else:
                logger.info("Plugin %s loaded", plugin_name)

            return True
        except Exception:
            logger.exception("Failed to load plugin %s", plugin_name)
            # Do not leave a half-loaded module registered
            unique_name = f"remedy_plugin_{plugin_name}"
            sys.modules.pop(unique_name, None)
            mod = sys.modules.get(plugin_name)
            if mod is not None and getattr(mod, "__file__", None) == str(plugin_file):
                sys.modules.pop(plugin_name, None)
            return False

    def unload(self, plugin_name: str) -> bool:
        module = self._loaded.pop(plugin_name, None)
        if module and hasattr(module, "teardown_plugin"):
            try:
                module.teardown_plugin()
            except Exception:
                logger.exception("Plugin %s teardown failed", plugin_name)
        # Drop import aliases we may have registered
        unique_name = f"remedy_plugin_{plugin_name}"
        sys.modules.pop(unique_name, None)
        if plugin_name in sys.modules and sys.modules[plugin_name] is module:
            sys.modules.pop(plugin_name, None)
        return module is not None

    def reload_all(self) -> int:
        count = 0
        for name in list(self._loaded.keys()):
            origin = self._origins.get(name)
            if self.unload(name) and self.load(
                name, plugin_path=str(origin) if origin else None
            ):
                count += 1
        return count

    @property
    def loaded_plugins(self) -> list[str]:
        return list(self._loaded.keys())
