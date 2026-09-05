"""Phase 6 tests: Interfaces & Integration."""

import os
from pathlib import Path
from unittest import mock

from remedy.interfaces.config import (
    _coerce,
    config_to_agent_config,
    create_default_config,
    generate_default_config,
    load_config,
    load_env_overrides,
    resolve_config,
)
from remedy.models import (
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
# Plugin system retired (Phase 6 — Go owns product hooks)
# ============================================================================


def test_interfaces_plugin_module_is_gone() -> None:
    import importlib.util
    from pathlib import Path

    assert importlib.util.find_spec("remedy.interfaces.plugin") is None
    assert not Path("src/remedy/interfaces/plugin.py").exists()


# ============================================================================
