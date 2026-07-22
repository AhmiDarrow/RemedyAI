"""Phase 6 tests: Interfaces & Integration."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.interfaces.config import (
    _coerce,
    config_to_agent_config,
    create_default_config,
    generate_default_config,
    load_config,
    load_env_overrides,
    resolve_config,
)
from remedy.interfaces.plugin import HookManager, PluginManager
from remedy.models import (
    AgentConfig,
    ChannelKind,
)

# ============================================================================
# Test Configuration System
# ============================================================================

class TestConfigLoading:
    def test_load_toml(self, tmp_path):
        """Load a TOML config file."""
        p = tmp_path / "config.toml"
        p.write_text(
            'name = "test-agent"\n'
            'log_level = "DEBUG"\n'
            "enabled_channels = [\"cli\", \"web\"]\n",
            encoding="utf-8",
        )
        config = load_config(p)
        assert config["name"] == "test-agent"
        assert config["log_level"] == "DEBUG"
        assert config["enabled_channels"] == ["cli", "web"]

    def test_load_yaml(self, tmp_path):
        """Load a YAML config file."""
        p = tmp_path / "config.yaml"
        p.write_text(
            "name: test-agent\n"
            "log_level: DEBUG\n"
            "enabled_channels:\n"
            "  - cli\n"
            "  - web\n",
            encoding="utf-8",
        )
        config = load_config(p)
        assert config["name"] == "test-agent"
        assert config["log_level"] == "DEBUG"

    def test_load_nonexistent_returns_empty(self):
        """Loading a nonexistent file returns empty dict."""
        config = load_config(Path("/nonexistent/path/never.toml"))
        assert config == {}

    def test_auto_detect_toml_by_name(self, tmp_path):
        """Auto-detects TOML when no path given."""
        p = tmp_path / "remedy.toml"
        p.write_text('name = "auto"\n', encoding="utf-8")
        with mock.patch.object(
            Path, "expanduser", return_value=p
        ):
            config = load_config(p)
            assert config.get("name") == "auto"

    def test_load_yaml_by_name(self, tmp_path):
        """Auto-detect YAML by name."""
        p = tmp_path / "remedy.yaml"
        p.write_text("name: yaml-config\n", encoding="utf-8")
        with mock.patch.object(
            Path, "expanduser", return_value=p
        ):
            config = load_config(p)
            assert config.get("name") == "yaml-config"

    def test_default_config_generation(self):
        """Default config TOML has expected keys."""
        content = generate_default_config(Path("~/.remedy"))
        assert "name = \"Remedy\"" in content
        assert "log_level" in content
        assert "[gateway]" in content
        assert "[execution]" in content
        assert "[telegram]" in content
        assert "[discord]" in content
        assert "[slack]" in content

    def test_create_default_config(self, tmp_path):
        """Creates config.toml in home dir."""
        cfg_path = create_default_config(tmp_path)
        assert cfg_path.exists()
        assert cfg_path.name == "config.toml"
        content = cfg_path.read_text(encoding="utf-8")
        assert "Remedy AI Configuration" in content


class TestEnvOverrides:
    def test_simple_override(self):
        """Simple env var overrides config key."""
        with mock.patch.dict(os.environ, {"REMEDY_NAME": "env-agent"}):
            config = load_env_overrides({"name": "default"})
            assert config["name"] == "env-agent"

    def test_nested_override(self):
        """Double-underscore creates nested keys."""
        with mock.patch.dict(os.environ, {"REMEDY_EXECUTION__MAX_RETRIES": "5"}):
            config = load_env_overrides({"execution": {"max_retries": 3}})
            assert config["execution"]["max_retries"] == 5

    def test_coerce_bool_true(self):
        assert _coerce("true") is True
        assert _coerce("yes") is True
        assert _coerce("1") is True

    def test_coerce_bool_false(self):
        assert _coerce("false") is False
        assert _coerce("no") is False
        assert _coerce("0") is False

    def test_coerce_int(self):
        assert _coerce("42") == 42

    def test_coerce_float(self):
        assert _coerce("3.14") == 3.14

    def test_coerce_string(self):
        assert _coerce("hello") == "hello"

    def test_no_prefix_ignored(self):
        with mock.patch.dict(os.environ, {"NORMAL_ENV": "value"}):
            config = load_env_overrides({"name": "default"})
            assert "normal_env" not in config

    def test_resolve_config_integration(self, tmp_path):
        """Full resolve with file + env + overrides."""
        p = tmp_path / "config.toml"
        p.write_text(
            'name = "file-agent"\nlog_level = "INFO"\n',
            encoding="utf-8",
        )
        with mock.patch.dict(os.environ, {"REMEDY_LOG_LEVEL": "DEBUG"}):
            resolved = resolve_config(
                config_path=p,
                home_dir=str(tmp_path),
            )
        assert resolved["name"] == "file-agent"
        assert resolved["log_level"] == "DEBUG"
        assert resolved["home_dir"] == str(tmp_path)


class TestConfigToAgentConfig:
    def test_minimal_config(self):
        agent_config = config_to_agent_config({"name": "minimal"})
        assert agent_config.name == "minimal"
        assert agent_config.persona == "default"
        assert agent_config.home_dir == "~/.remedy"

    def test_full_config(self):
        agent_config = config_to_agent_config({
            "name": "full",
            "persona": "sarcastic",
            "home_dir": "/tmp/remedy",
            "skills_dir": ["./skills"],
            "memory_db_path": "/tmp/memory.db",
            "enabled_channels": ["cli", "web"],
            "mcp_servers": ["test:python"],
            "allow_skill_creation": False,
            "auto_approve_threshold": 0.9,
            "log_level": "DEBUG",
            "sarcasm_mode": True,
        })
        assert agent_config.name == "full"
        assert agent_config.persona == "sarcastic"
        assert ChannelKind.CLI in agent_config.enabled_channels
        assert ChannelKind.WEB in agent_config.enabled_channels
        assert agent_config.allow_skill_creation is False
        assert agent_config.sarcasm_mode is True


# ============================================================================
# Test Hook & Plugin System
# ============================================================================

class TestHookManager:
    def test_register_and_fire(self):
        hm = HookManager()
        results = []

        def handler(x):
            results.append(x)
            return x

        hm.register("test", handler)
        r = hm.fire("test", 42)
        assert results == [42]
        assert r == [42]

    def test_fire_no_handlers_returns_empty(self):
        hm = HookManager()
        assert hm.fire("nonexistent", 1) == []

    def test_fire_chain_continues_on_true(self):
        hm = HookManager()
        calls = []

        def a(): calls.append("a"); return True
        def b(): calls.append("b"); return True

        hm.register("chain", a, priority=10)
        hm.register("chain", b, priority=5)
        result = hm.fire_chain("chain")
        assert result is True
        assert calls == ["a", "b"]

    def test_fire_chain_short_circuits_on_false(self):
        hm = HookManager()
        calls = []

        def a(): calls.append("a"); return False
        def b(): calls.append("b"); return True

        hm.register("chain", a, priority=10)
        hm.register("chain", b, priority=5)
        result = hm.fire_chain("chain")
        assert result is False
        assert calls == ["a"]

    def test_priority_ordering(self):
        hm = HookManager()
        order = []

        hm.register("test", lambda: order.append("low"), priority=0)
        hm.register("test", lambda: order.append("high"), priority=10)
        hm.register("test", lambda: order.append("mid"), priority=5)
        hm.fire("test")
        assert order == ["high", "mid", "low"]

    def test_unregister_handler(self):
        hm = HookManager()
        calls = []

        def handler():
            calls.append(1)

        hm.register("test", handler)
        hm.unregister("test", handler)
        hm.fire("test")
        assert calls == []

    def test_clear_specific_hook(self):
        hm = HookManager()
        hm.register("a", lambda: None)
        hm.register("b", lambda: None)
        hm.clear("a")
        assert hm.list_hooks().get("a", 0) == 0
        assert hm.list_hooks()["b"] == 1

    def test_clear_all_hooks(self):
        hm = HookManager()
        hm.register("a", lambda: None)
        hm.register("b", lambda: None)
        hm.clear()
        assert hm.list_hooks() == {}

    def test_list_hooks(self):
        hm = HookManager()
        hm.register("a", lambda: None)
        hm.register("a", lambda: None)
        hm.register("b", lambda: None)
        hooks = hm.list_hooks()
        assert hooks == {"a": 2, "b": 1}

    def test_list_handlers(self):
        hm = HookManager()
        hm.register("test", lambda: None, priority=5, source="plugin-x")
        handlers = hm.list_handlers("test")
        assert len(handlers) == 1
        assert handlers[0]["priority"] == 5
        assert handlers[0]["source"] == "plugin-x"

    def test_fire_async(self):
        import asyncio
        hm = HookManager()
        results = []

        async def handler(x):
            results.append(x)
            return x

        hm.register("test", handler)
        r = asyncio.run(hm.fire_async("test", 99))
        assert results == [99]
        assert r == [99]

    def test_handler_exception_does_not_crash(self):
        hm = HookManager()
        results = []

        def bad(): raise ValueError("ouch")
        def good(): results.append("ok"); return "ok"

        hm.register("test", bad, priority=10)
        hm.register("test", good, priority=5)
        r = hm.fire("test")
        assert results == ["ok"]
        assert r == ["ok"]


class TestPluginManager:
    def test_discover_py_files(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        (tmp_path / "plugin_a.py").write_text("def setup_plugin(h): pass\n")
        (tmp_path / "plugin_b.py").write_text("def setup_plugin(h): pass\n")
        (tmp_path / "_internal.py").write_text("pass\n")
        found = pm.discover([str(tmp_path)])
        assert len(found) == 2
        assert "plugin_a" in found
        assert "plugin_b" in found

    def test_discover_packages(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        pkg = tmp_path / "my_plugin"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("def setup_plugin(h): pass\n")
        found = pm.discover([str(tmp_path)])
        assert "my_plugin" in found

    def test_discover_single_file(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        p = tmp_path / "single.py"
        p.write_text("def setup_plugin(h): pass\n")
        found = pm.discover([str(p)])
        assert "single" in found

    def test_discover_missing_dir_returns_empty(self):
        hm = HookManager()
        pm = PluginManager(hm)
        assert pm.discover(["/nonexistent"]) == []

    def test_load_with_setup(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        p = tmp_path / "test_load.py"
        p.write_text(
            "def setup_plugin(h): h.register('on_start', lambda: 'loaded')\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            pm.load("test_load")
            assert "test_load" in pm.loaded_plugins
            assert hm.fire("on_start") == ["loaded"]
        finally:
            sys.path.remove(str(tmp_path))

    def test_load_without_setup(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        p = tmp_path / "plain.py"
        p.write_text("x = 1\n")
        sys.path.insert(0, str(tmp_path))
        try:
            pm.load("plain")
            assert "plain" in pm.loaded_plugins
        finally:
            sys.path.remove(str(tmp_path))

    def test_load_nonexistent_returns_false(self):
        hm = HookManager()
        pm = PluginManager(hm)
        assert pm.load("nonexistent_module_xyz") is False

    def test_unload(self, tmp_path):
        hm = HookManager()
        pm = PluginManager(hm)
        p = tmp_path / "to_unload.py"
        p.write_text(
            "teardowns = []\ndef setup_plugin(h): pass\ndef teardown_plugin(): teardowns.append(1)\n"
        )
        sys.path.insert(0, str(tmp_path))
        try:
            pm.load("to_unload")
            assert pm.unload("to_unload") is True
            assert "to_unload" not in pm.loaded_plugins
        finally:
            sys.path.remove(str(tmp_path))


# ============================================================================
# Test API Endpoints
# ============================================================================

@pytest.fixture
def test_client():
    from unittest import mock

    runtime = mock.MagicMock()
    runtime.config = AgentConfig(name="test", home_dir="~/.remedy")
    runtime.memory = None
    runtime.skills = None

    gateway = mock.MagicMock()
    gateway.emit = mock.AsyncMock(return_value=["echo: hello from test"])
    gateway.stats.return_value = {"running": True, "uptime": "0s"}

    app = create_app(gateway=gateway, title="Test Remedy", version="0.1.0-test")
    return TestClient(app)


class TestAPIStatus:
    def test_status_returns_ok(self, test_client):
        r = test_client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "version" in data

    def test_chat_requires_message(self, test_client):
        r = test_client.post("/api/chat", json={})
        assert r.status_code == 422

    def test_chat_echoes(self, test_client):
        r = test_client.post("/api/chat", json={"message": "hello"})
        assert r.status_code == 200
        data = r.json()
        assert "response" in data
        assert "hello" in data["response"].lower()

    def test_openapi_json(self, test_client):
        r = test_client.get("/api/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["info"]["title"] == "Test Remedy"

    def test_openapi_yaml(self, test_client):
        r = test_client.get("/api/openapi.yaml")
        assert r.status_code == 200
        assert "title: Test Remedy" in r.text

    def test_dashboard_html(self, test_client):
        r = test_client.get("/dashboard")
        assert r.status_code == 200
        assert "Remedy AI" in r.text
        assert "Dashboard" in r.text

    def test_swagger_docs(self, test_client):
        r = test_client.get("/docs")
        assert r.status_code == 200

    def test_redoc(self, test_client):
        r = test_client.get("/redoc")
        assert r.status_code == 200


# ============================================================================
# Test SSE Streaming
# ============================================================================

class TestSSEStreaming:
    def test_chat_stream_returns_sse(self, test_client):
        with test_client.stream("POST", "/api/chat/stream", json={"message": "test stream"}) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            data_lines = [line for line in response.iter_lines() if line]
            assert len(data_lines) > 0
            found_start = False
            found_done = False
            for line in data_lines:
                if line.startswith("data: "):
                    payload = json.loads(line[6:])
                    if payload.get("type") == "start":
                        found_start = True
                    elif payload.get("type") == "done":
                        found_done = True
            assert found_start
            assert found_done
