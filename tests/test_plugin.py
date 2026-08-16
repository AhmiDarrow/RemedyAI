"""Plugin system discovery, load, and hook lifecycle tests."""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces.plugin import (
    HookManager,
    PluginManager,
    is_safe_plugin_name,
)


def test_hook_manager_fire_and_priority() -> None:
    hooks = HookManager()
    order: list[int] = []

    hooks.register("on_startup", lambda: order.append(1) or "low", priority=1, source="a")
    hooks.register("on_startup", lambda: order.append(10) or "high", priority=10, source="b")

    results = hooks.fire("on_startup")
    # Higher priority first
    assert order == [10, 1]
    assert results == ["high", "low"]


def test_hook_chain_short_circuit() -> None:
    hooks = HookManager()
    hooks.register("pre_tool_exec", lambda *a, **k: False, priority=5, source="deny")
    hooks.register("pre_tool_exec", lambda *a, **k: True, priority=1, source="allow")
    assert hooks.fire_chain("pre_tool_exec", "bash_exec", {}) is False


def test_plugin_manager_discover_and_load_demo() -> None:
    examples = Path(__file__).resolve().parents[1] / "examples"
    hooks = HookManager()
    pm = PluginManager(hooks)

    found = pm.discover([str(examples)])
    assert "demo_plugin" in found

    ok = pm.load("demo_plugin", plugin_path=str(examples))
    assert ok is True

    import demo_plugin as demo  # type: ignore  # path-bound load aliases module

    assert "setup" in demo.SETUP_CALLS

    results = hooks.fire("on_startup")
    assert "demo-started" in results
    assert "startup" in demo.SETUP_CALLS

    assert hooks.fire_chain("pre_tool_exec", "file_read", {"path": "x"}) is True
    hooks.fire("post_tool_exec", "file_read", "ok")
    assert ("pre", "file_read") in demo.TOOL_EVENTS
    assert ("post", "file_read") in demo.TOOL_EVENTS


def test_plugin_load_missing_returns_false() -> None:
    hooks = HookManager()
    pm = PluginManager(hooks)
    assert pm.load("definitely_not_a_real_plugin_xyz") is False


def test_plugin_refuses_bare_stdlib_and_unsafe_names() -> None:
    """Trust residual: no bare importlib of os/subprocess; no path-like names."""
    hooks = HookManager()
    pm = PluginManager(hooks)
    assert is_safe_plugin_name("os") is False
    assert is_safe_plugin_name("subprocess") is False
    assert is_safe_plugin_name("../evil") is False
    assert is_safe_plugin_name("pkg.sub") is False
    assert is_safe_plugin_name("demo_plugin") is True
    # Bare load without discover origin / path
    assert pm.load("os") is False
    assert pm.load("subprocess") is False
    assert pm.load("json") is False  # stdlib without path


def test_plugin_denylist_even_if_file_present(tmp_path: Path) -> None:
    """A file named os.py under plugins/ must not load."""
    (tmp_path / "os.py").write_text(
        "def setup_plugin(hooks):\n    raise RuntimeError('should not run')\n",
        encoding="utf-8",
    )
    hooks = HookManager()
    pm = PluginManager(hooks)
    found = pm.discover([str(tmp_path)])
    assert "os" not in found
    assert pm.load("os", plugin_path=str(tmp_path)) is False
