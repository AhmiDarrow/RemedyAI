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

    When ``home_dir`` is set and no explicit ``config_path`` is given, prefer
    ``{home_dir}/config.toml`` so desktop ``--home`` and CLI share one file.
    """
    if config_path is None and home_dir:
        home_cfg = Path(home_dir).expanduser() / "config.toml"
        if home_cfg.exists():
            config_path = home_cfg
    config = load_config(config_path)
    if env_overrides:
        config = load_env_overrides(config)
    if home_dir:
        config["home_dir"] = home_dir
    return config


# -- Provider catalog (defaults + model ownership) ---------------------------

PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "models": [
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini"},
            {"id": "o4-mini", "name": "o4-mini"},
        ],
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "models": [
            {"id": "claude-3-5-sonnet-latest", "name": "Claude 3.5 Sonnet"},
            {"id": "claude-3-5-haiku-latest", "name": "Claude 3.5 Haiku"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku"},
            {"id": "claude-3.5-sonnet", "name": "Claude 3.5 Sonnet (alias)"},
            {"id": "claude-3-haiku", "name": "Claude 3 Haiku (alias)"},
        ],
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": [
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
            {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro"},
        ],
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner"},
        ],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "models": [
            {"id": "openrouter/auto", "name": "OpenRouter Auto"},
            {"id": "openai/gpt-4o-mini", "name": "GPT-4o Mini (via OpenRouter)"},
            {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet (via OpenRouter)"},
            {"id": "google/gemini-2.0-flash-001", "name": "Gemini 2.0 Flash (via OpenRouter)"},
        ],
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "models": [
            {"id": "llama3.2", "name": "Llama 3.2"},
            {"id": "qwen2.5", "name": "Qwen 2.5"},
            {"id": "codellama", "name": "Code Llama"},
        ],
    },
    "custom": {
        "base_url": "http://127.0.0.1:5001/api/v1",
        "models": [
            {"id": "default", "name": "Default (custom endpoint)"},
        ],
    },
}


def infer_provider_from_model(model_id: str) -> str | None:
    """Guess which *native* provider owns a model id (not OpenRouter-prefixed)."""
    mid = (model_id or "").strip().lower()
    if not mid:
        return None
    # OpenRouter-style vendor/model is multi-provider; treat as openrouter only
    # when the user already chose openrouter — do not force it here.
    if mid.startswith("claude") or mid.startswith("anthropic/"):
        return "anthropic"
    if mid.startswith("gpt-") or mid.startswith("o1") or mid.startswith("o3") or mid.startswith("o4"):
        return "openai"
    if mid.startswith("gemini") or mid.startswith("models/gemini"):
        return "google"
    if mid.startswith("deepseek"):
        return "deepseek"
    # Common local / Ollama model family prefixes (not exhaustive).
    if mid.startswith(
        (
            "llama",
            "qwen",
            "codellama",
            "mistral",
            "mixtral",
            "phi",
            "gemma",
            "codegemma",
            "tinyllama",
            "wizard",
            "nous",
            "yi-",
            "solar",
            "orca",
            "starcoder",
            "deepseek-coder",
            "deepseek-r1",
        )
    ):
        return "ollama"
    return None


def infer_provider_from_base_url(base_url: str) -> str | None:
    """Map a known host to a provider id."""
    u = (base_url or "").lower()
    if not u:
        return None
    if "anthropic.com" in u:
        return "anthropic"
    if "openai.com" in u:
        return "openai"
    if "deepseek.com" in u:
        return "deepseek"
    if "openrouter.ai" in u:
        return "openrouter"
    if "generativelanguage.googleapis.com" in u or "googleapis.com" in u:
        return "google"
    if "11434" in u or "ollama" in u:
        return "ollama"
    return None


def normalize_llm_settings(
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> tuple[str, str, str]:
    """Align provider, model, and base_url so they don't cross-wire.

    Examples of bad states we fix:
    - provider=deepseek, model=claude-3-haiku  → model=deepseek-chat
    - provider=deepseek, base_url=api.openai.com → base_url=api.deepseek.com
    """
    prov = (provider or "openai").strip().lower() or "openai"
    if prov not in PROVIDER_CATALOG:
        # Unknown label → treat as custom OpenAI-compatible
        if prov not in ("custom",):
            # keep name but use custom defaults for missing pieces
            pass
    catalog = PROVIDER_CATALOG.get(prov) or PROVIDER_CATALOG["custom"]
    default_url = str(catalog.get("base_url") or "")
    default_models = list(catalog.get("models") or [])
    default_model = str(default_models[0]["id"]) if default_models else "gpt-4o-mini"

    url = (base_url or "").strip() or default_url
    mid = (model or "").strip() or default_model

    # If URL clearly belongs to another known provider, snap to this provider's URL.
    url_owner = infer_provider_from_base_url(url)
    if url_owner and url_owner != prov and prov in PROVIDER_CATALOG:
        # OpenRouter intentionally hosts many vendors — only snap when *this*
        # provider is not openrouter/custom.
        if prov not in ("openrouter", "custom", "ollama"):
            url = default_url

    # Flexible providers can host any model id (Ollama pulls deepseek-*, etc.).
    _FLEXIBLE = frozenset({"openrouter", "custom", "ollama"})

    model_owner = infer_provider_from_model(mid)
    if model_owner and model_owner != prov and prov not in _FLEXIBLE:
        mid = default_model
    elif prov in PROVIDER_CATALOG and default_models and prov not in _FLEXIBLE:
        known = {m["id"] for m in default_models}
        if not mid:
            mid = default_model
        # Closed catalogs: reject foreign model ids.
        if prov == "deepseek" and mid not in known and model_owner not in (None, "deepseek"):
            mid = default_model
        if prov in ("openai", "anthropic", "google", "deepseek") and model_owner and model_owner != prov:
            mid = default_model

    if not url:
        url = default_url
    if not mid:
        mid = default_model

    return prov, mid, url


# Canonical desktop personas (aligned with SetupWizard).
PERSONA_PROMPTS: dict[str, str] = {
    "default": "",
    "balanced": (
        "Communication style: balanced — helpful and adaptable to the task. "
        "Match the user's depth; prefer clarity over verbosity."
    ),
    "efficient": (
        "Communication style: efficient — concise, code-first, minimal explanation. "
        "Prefer short answers and actionable output."
    ),
    "detailed": (
        "Communication style: detailed — thorough explanations with context, "
        "trade-offs, and clear structure."
    ),
    "playful": (
        "Communication style: playful — casual tone with light humor while remaining accurate."
    ),
    # CLI wizard aliases
    "concise": (
        "Communication style: concise — short answers, minimal fluff."
    ),
    "verbose": (
        "Communication style: verbose — thorough explanations with examples."
    ),
    "sarcastic": (
        "Communication style: dry humor is allowed; stay helpful and accurate."
    ),
    "minimal": (
        "Communication style: minimal — answer only what was asked."
    ),
}


def persona_system_addendum(persona: str | None) -> str:
    """Return system-prompt text for a persona id, or empty string."""
    if not persona:
        return ""
    key = persona.strip().lower()
    return PERSONA_PROMPTS.get(key, "")


def catalog_models_for_provider(provider: str) -> list[dict[str, Any]]:
    """Return built-in model entries tagged with provider."""
    prov = (provider or "openai").lower()
    cat = PROVIDER_CATALOG.get(prov) or PROVIDER_CATALOG.get("custom") or {}
    out: list[dict[str, Any]] = []
    for m in cat.get("models") or []:
        out.append(
            {
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "provider": prov,
                "default": False,
            }
        )
    return out


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
        project_path=config.get("project_path") or os.environ.get("REMEDY_PROJECT_PATH") or None,
    )


def generate_default_config(home_dir: Path) -> str:
    """Generate a default TOML config file content."""
    return f"""# Remedy AI Configuration
# Generated by remedy config init

name = "Remedy"
persona = "default"
home_dir = "{home_dir.as_posix()}"

# First-run setup: false until `remedy setup` / desktop wizard / --skip-setup
setup_completed = false

# Default project/workspace folder for the agent (file tools, shell cwd, @file UI)
# project_path = "C:/Users/You/Projects/MyApp"

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


def config_path_for_home(home_dir: str | Path | None = None) -> Path:
    """Return the canonical config.toml path for a home directory."""
    hd = Path(home_dir or Path.home() / ".remedy").expanduser()
    return hd / "config.toml"


def needs_first_run_setup(
    config: dict[str, Any] | None = None,
    *,
    home_dir: str | Path | None = None,
    config_path: Path | None = None,
) -> bool:
    """Return True when first-run setup should run before launch.

    Rules:
    - No config file → need setup
    - ``setup_completed`` present → honor it (True skips, False forces)
    - Legacy config without the flag → treat as already set up (do not re-wizard upgrades)

    Skipping setup (desktop Skip, CLI --skip-setup) writes ``setup_completed = true``
    so subsequent launches ignore the wizard.
    """
    path = config_path
    if path is None and home_dir is not None:
        path = config_path_for_home(home_dir)
    if path is None:
        path = config_path_for_home()

    path = Path(path).expanduser()
    if not path.exists():
        return True

    cfg = config if config is not None else load_config(path)
    if "setup_completed" in cfg:
        return not bool(cfg["setup_completed"])
    # Pre-flag installs: config already exists → do not force wizard again.
    return False


def mark_setup_completed(
    *,
    home_dir: str | Path | None = None,
    config_path: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Persist ``setup_completed = true`` (optionally merging extra keys).

    Creates a minimal config when none exists so first-launch skip is remembered.
    """
    path = config_path or config_path_for_home(home_dir)
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    cfg: dict[str, Any] = load_config(path) if path.exists() else {}
    if extra:
        cfg.update(extra)
    cfg["setup_completed"] = True
    if "home_dir" not in cfg:
        cfg["home_dir"] = path.parent.as_posix()

    # Minimal TOML writer (top-level scalars only + nested dicts as sections).
    lines = ["# Remedy AI Configuration", ""]
    for key, value in cfg.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]")
            for k, v in value.items():
                lines.append(f"{k} = {_toml_scalar(v)}")
            lines.append("")
        else:
            lines.append(f"{key} = {_toml_scalar(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_toml_scalar(v) for v in value)
        return f"[{items}]"
    if value is None:
        return '""'
    # Escape backslashes and quotes for TOML strings
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def provider_credentials_ready(config: dict[str, Any] | None = None) -> bool:
    """True when an LLM key is configured (or local URL which needs none)."""
    cfg = config if config is not None else load_config()
    key = str(cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or "").strip()
    if key:
        return True
    base = str(
        cfg.get("llm_base_url")
        or os.environ.get("REMEDY_LLM_BASE_URL")
        or ""
    ).strip()
    if base and _is_local_url(base):
        return True
    provider = str(cfg.get("llm_provider") or os.environ.get("REMEDY_LLM_PROVIDER") or "").lower()
    return provider in ("ollama",)
