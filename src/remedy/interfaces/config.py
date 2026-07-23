"""Remedy configuration system.

Loads config from TOML/YAML files (e.g. ~/.remedy/config.toml),
environment variable overrides, and CLI arguments. Provides a single
merged config for all subsystems.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from remedy.models import AgentConfig, ChannelKind

CONFIG_PATHS = [
    Path("~/.remedy/config.toml"),
    Path("~/.remedy/config.yaml"),
    Path("remedy.toml"),
    Path("remedy.yaml"),
]

ENV_PREFIX = "REMEDY_"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load config from a file, auto-detecting format (TOML or YAML)."""
    if path is None:
        for p in CONFIG_PATHS:
            expanded = p.expanduser().resolve()
            if expanded.exists():
                path = expanded
                break

    if path is None:
        return {}

    path = Path(path).expanduser().resolve()
    if not path.exists():
        return {}

    content = path.read_text(encoding="utf-8")

    try:
        if path.suffix in (".toml", ".tml"):
            return tomllib.loads(content)
        elif path.suffix in (".yaml", ".yml") or "---" in content[:100] or path.suffix == ".yaml":
            return yaml.safe_load(content) or {}
        else:
            return tomllib.loads(content)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("Failed to parse config %s: %s", path, exc)
        return {}


def load_env_overrides(base: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides.

    Maps REMEDY_NAME=foo to config["name"] = "foo"
    Maps REMEDY_LOG_LEVEL=DEBUG to config["log"]["level"] = "DEBUG"
    """
    result = dict(base)
    for key, value in os.environ.items():
        if not key.startswith(ENV_PREFIX):
            continue
        config_key = key[len(ENV_PREFIX):].lower()
        parts = config_key.split("__")
        if len(parts) == 1:
            result[parts[0]] = _coerce(value)
        else:
            current = result
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = _coerce(value)
    return result


def _coerce(value: str) -> Any:
    if value.lower() in ("true", "yes", "1"):
        return True
    if value.lower() in ("false", "no", "0"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def resolve_config(
    config_path: Path | None = None,
    home_dir: str | None = None,
    env_overrides: bool = True,
) -> dict[str, Any]:
    """Load and resolve full configuration.

    Priority: env vars > config file > defaults.
    """
    config = load_config(config_path)
    if env_overrides:
        config = load_env_overrides(config)
    if home_dir:
        config["home_dir"] = home_dir
    return config


def _resolve_str(config_value: str | None, env_var: str, default: str) -> str:
    """Resolve a config value, preferring non-empty config over env var over default."""
    if config_value:
        return config_value
    env_value = os.environ.get(env_var, "").strip()
    if env_value:
        return env_value
    return default


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_local_url(url: str) -> bool:
    """Check if a base URL points to a local/loopback server."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        return host in _LOCAL_HOSTS or host.endswith(".local")
    except Exception:
        return False


def config_to_agent_config(config: dict[str, Any]) -> AgentConfig:
    """Convert a config dict to a validated AgentConfig model.

    API key resolution order:
        non-empty config value > REMEDY_LLM_API_KEY env var > empty (fallback).

    When llm_base_url points to a local server (localhost, 127.0.0.1, etc.)
    and no API key is configured, a dummy key is used automatically since
    local servers (Ollama, Kobold.cpp, LM Studio, etc.) don't require one.
    """
    import logging

    logger = logging.getLogger(__name__)

    channels_raw = config.get("enabled_channels", [])
    if not isinstance(channels_raw, list):
        channels_raw = [channels_raw] if isinstance(channels_raw, str) else []

    channels: list[ChannelKind] = []
    for c in channels_raw:
        try:
            channels.append(ChannelKind(c))
        except ValueError:
            logger.warning("Ignoring unknown channel '%s' in config", c)

    llm_base_url = _resolve_str(
        config.get("llm_base_url"),
        "REMEDY_LLM_BASE_URL",
        "https://api.openai.com/v1",
    )

    llm_api_key = _resolve_str(
        config.get("llm_api_key"),
        "REMEDY_LLM_API_KEY",
        "",
    )

    # Local servers (Ollama, Kobold.cpp, LM Studio, etc.) don't need a real
    # API key.  Supply a dummy value so the agent doesn't fall back to echo
    # mode when the user hasn't set one.
    if not llm_api_key and _is_local_url(llm_base_url):
        llm_api_key = "local"
        logger.info("No API key set for local LLM at %s — using dummy key", llm_base_url)

    return AgentConfig(
        name=config.get("name", "Remedy"),
        persona=config.get("persona", "default"),
        home_dir=config.get("home_dir", "~/.remedy"),
        skills_dir=config.get("skills_dir", []),
        memory_db_path=config.get("memory_db_path"),
        enabled_channels=channels,
        mcp_servers=config.get("mcp_servers", []),
        allow_skill_creation=config.get("allow_skill_creation", True),
        auto_approve_threshold=config.get("auto_approve_threshold", 0.8),
        log_level=config.get("log_level", "INFO"),
        sarcasm_mode=config.get("sarcasm_mode", False),
        llm_provider=_resolve_str(
            config.get("llm_provider"),
            "REMEDY_LLM_PROVIDER",
            "openai",
        ),
        llm_api_key=llm_api_key,
        llm_model=_resolve_str(
            config.get("llm_model"),
            "REMEDY_LLM_MODEL",
            "gpt-4o-mini",
        ),
        llm_base_url=llm_base_url,
    )


def generate_default_config(home_dir: Path) -> str:
    """Generate a default TOML config file content."""
    return f"""# Remedy AI Configuration
# Generated by remedy config init

name = "Remedy"
persona = "default"
home_dir = "{home_dir.as_posix()}"

# --- LLM Provider ---
# Supported providers: openai, anthropic, google, deepseek, openrouter, ollama, custom
llm_provider = "openai"
llm_model = "gpt-4o-mini"
llm_base_url = "https://api.openai.com/v1"
# llm_api_key - set via REMEDY_LLM_API_KEY env var or uncomment below:
# llm_api_key = "sk-..."

# Search paths for bundled + user skills
skills_dir = []

# SQLite memory database
memory_db_path = "{home_dir.joinpath('memory.db').as_posix()}"

# Channels to enable: cli, telegram, discord, slack, web, api
enabled_channels = ["cli"]

# MCP server configurations
mcp_servers = []

# Learning loop
allow_skill_creation = true
auto_approve_threshold = 0.8

# Logging
log_level = "INFO"

[gateway]
heartbeat_interval = 60
rate_limit = 120

[execution]
default_timeout = 30
max_retries = 3
retry_backoff = 1.0

[telegram]
bot_token = ""

[discord]
bot_token = ""
channel_id = ""

[slack]
bot_token = ""
channel_id = ""
"""


def create_default_config(home_dir: Path | None = None) -> Path:
    """Create a default config file in the home directory."""
    hd = (home_dir or Path("~/.remedy")).expanduser()
    hd.mkdir(parents=True, exist_ok=True)
    config_path = hd / "config.toml"
    if not config_path.exists():
        config_path.write_text(generate_default_config(hd), encoding="utf-8")
    return config_path
