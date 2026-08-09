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

# Env vars that can supply a key for a given provider (checked after provider_keys).
_PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY", "REMEDY_LLM_API_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "xai": ("XAI_API_KEY", "REMEDY_XAI_API_KEY"),
    "groq": ("GROQ_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "poe": ("POE_API_KEY", "REMEDY_POE_API_KEY"),
}


def looks_like_xai_credential(key: str | None) -> bool:
    """True if the secret is an xAI console key or OAuth access JWT."""
    k = (key or "").strip()
    if not k:
        return False
    if k.startswith("xai-"):
        return True
    # OAuth access tokens are JWTs (header.payload.sig)
    return bool(k.startswith("eyJ") and k.count(".") >= 2)


def infer_key_provider(key: str | None) -> str | None:
    """Best-effort owner for a raw API key (used only for migration)."""
    k = (key or "").strip()
    if not k:
        return None
    if looks_like_xai_credential(k):
        return "xai"
    if k.startswith("sk-ant"):
        return "anthropic"
    if k.startswith("gsk_"):
        return "groq"
    if k.startswith("AIza"):
        return "google"
    if k.startswith("sk-or-"):
        return "openrouter"
    # DeepSeek console keys are sk-… (same prefix as OpenAI). Prefer deepseek when
    # the key was incorrectly parked on xAI; OpenAI users can re-save once.
    if k.startswith("sk-"):
        return "deepseek"
    return None


def _home_from_cfg(cfg: dict[str, Any] | None, home: Path | str | None = None) -> Path | None:
    if home is not None:
        return Path(home).expanduser()
    if cfg and cfg.get("home_dir"):
        return Path(str(cfg["home_dir"])).expanduser()
    return None


def get_provider_keys(
    cfg: dict[str, Any] | None = None,
    *,
    home: Path | str | None = None,
) -> dict[str, str]:
    """Return {provider: api_key} from the **secure store** (not config.toml).

    Legacy plaintext ``cfg['provider_keys']`` is still read for one-shot
    migration, then ignored for ongoing use.
    """
    from remedy.interfaces.secret_store import load_provider_keys

    home_path = _home_from_cfg(cfg, home)
    stored = load_provider_keys(home_path)
    # Transient legacy table (only until migrate_provider_keys scrubs it)
    raw = (cfg or {}).get("provider_keys") or {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            pk = str(k or "").strip().lower()
            val = str(v or "").strip()
            if pk and val and pk not in stored:
                stored[pk] = val
    return stored


def set_provider_key(
    cfg: dict[str, Any],
    provider: str,
    api_key: str | None,
    *,
    home: Path | str | None = None,
) -> dict[str, Any]:
    """Write/clear a per-provider API key in the secure store. Scrubs cfg secrets."""
    from remedy.interfaces.secret_store import scrub_config_secrets, set_provider_secret

    provider = str(provider or "").strip().lower()
    if not provider:
        return cfg
    home_path = _home_from_cfg(cfg, home)
    set_provider_secret(provider, api_key, home=home_path)
    # Never keep secrets in the config dict destined for disk.
    cfg = scrub_config_secrets(cfg)
    cfg.pop("provider_keys", None)
    cfg["llm_api_key"] = ""
    return cfg


def migrate_provider_keys(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Move secrets into the secure store; scrub config; unstick foreign xAI keys.

    Legacy configs used a single ``llm_api_key`` shared across providers and/or a
    plaintext ``[provider_keys]`` table. Both are migrated into
    ``~/.remedy/auth/provider_keys.json`` (DPAPI-encrypted on Windows) and
    removed from the config representation.
    """
    from remedy.interfaces.secret_store import migrate_secrets_from_config

    cfg = dict(cfg or {})
    home = _home_from_cfg(cfg)
    # Pull plaintext secrets into secure store + scrub cfg
    cfg = migrate_secrets_from_config(cfg, home=home)
    # Ensure global key is never left populated (secrets live in the store / OAuth)
    cfg["llm_api_key"] = ""
    cfg.pop("provider_keys", None)
    return cfg


def resolve_provider_api_key(
    cfg: dict[str, Any] | None = None,
    provider: str | None = None,
    *,
    home: Path | str | None = None,
) -> str:
    """Resolve the API key/bearer for *this* provider only (never another provider's).

    Order:
      1. Secure store (``~/.remedy/auth/provider_keys.json``)
      2. xAI OAuth / auth store via ``resolve_bearer``
      3. Provider-specific env vars
      4. Legacy ``llm_api_key`` / ``REMEDY_LLM_API_KEY`` only if plausible for provider
         (and never written back to disk by this function)
    """
    cfg = dict(cfg or {})
    home_path = _home_from_cfg(cfg, home)
    provider = str(provider or cfg.get("llm_provider") or "").strip().lower()
    if not provider:
        return ""

    # Demo / guest gateways need a non-empty OpenAI-style bearer; no real secret.
    if provider == "demo":
        return DEMO_DUMMY_API_KEY

    # Secure store first (after optional lazy migration of any leftover plaintext)
    if cfg.get("provider_keys") or str(cfg.get("llm_api_key") or "").strip():
        cfg = migrate_provider_keys(cfg)

    from remedy.interfaces.secret_store import get_provider_secret

    mapped = (get_provider_secret(provider, home=home_path) or "").strip()
    if mapped:
        if provider == "xai" and not looks_like_xai_credential(mapped):
            mapped = ""
        else:
            return mapped

    if provider == "xai":
        try:
            from remedy.interfaces.xai_auth import resolve_bearer

            token = resolve_bearer(home_path)
            if token:
                return token
        except Exception:
            pass

    for env_name in _PROVIDER_ENV_KEYS.get(provider, ()):
        val = os.environ.get(env_name, "").strip()
        if val:
            if provider == "xai" and env_name in ("XAI_API_KEY", "REMEDY_XAI_API_KEY"):
                return val
            if provider != "xai":
                return val
            if looks_like_xai_credential(val):
                return val

    # Legacy global / env — only when it matches this provider. Not persisted.
    global_key = str(
        cfg.get("llm_api_key") or os.environ.get("REMEDY_LLM_API_KEY") or ""
    ).strip()
    if not global_key:
        return ""
    if provider == "xai":
        return global_key if looks_like_xai_credential(global_key) else ""
    if looks_like_xai_credential(global_key):
        return ""
    return global_key


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
        # XAI_API_KEY / other env keys preselect provider on clean defaults.
        config = apply_env_provider_bootstrap(config)
    if home_dir:
        config["home_dir"] = home_dir
    return config


# Provider catalog lives in provider_catalog.py (imported for compatibility).
from remedy.interfaces.provider_catalog import (  # noqa: E402
    DEMO_DUMMY_API_KEY,
    PROVIDER_CATALOG,
    free_options_public,
)

# Providers that keep a closed model catalog (foreign model ids are snapped).
_CLOSED_PROVIDERS = frozenset(
    {"openai", "anthropic", "google", "deepseek", "xai", "groq", "mistral"}
)


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
    if mid.startswith("grok") or mid.startswith("xai/"):
        return "xai"
    if mid.startswith("mistral") or mid.startswith("codestral") or mid.startswith("open-mistral"):
        return "mistral"
    # Groq hosts open models; only treat explicit groq/ prefix as owned.
    if mid.startswith("groq/"):
        return "groq"
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
    if "api.x.ai" in u or "x.ai" in u:
        return "xai"
    if "api.groq.com" in u or "groq.com" in u:
        return "groq"
    if "mistral.ai" in u:
        return "mistral"
    if "openrouter.ai" in u:
        return "openrouter"
    if "api.poe.com" in u or "poe.com" in u:
        return "poe"
    if "generativelanguage.googleapis.com" in u or "googleapis.com" in u:
        return "google"
    if "11434" in u or "ollama" in u:
        return "ollama"
    if ":8787" in u or u.rstrip("/").endswith(":8787") or "/rmb" in u:
        return "rmb"
    return None


# Retired / renamed API model ids → current ids (before live discovery or chat).
# Endpoint discovery is preferred; these keep saved config working offline.
_LEGACY_MODEL_ALIASES: dict[str, str] = {
    # DeepSeek: chat/reasoner retired 2026-07-24 (mapped to V4 Flash modes).
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek-reasoner": "deepseek-v4-flash",
    "deepseek-coder": "deepseek-v4-flash",
    # xAI: older Grok slugs retired / redirected; prefer current flagship family.
    "grok-3": "grok-4.3",
    "grok-3-mini": "grok-4.3",
    "grok-3-fast": "grok-4.3",
    "grok-3-mini-fast": "grok-4.3",
    "grok-2": "grok-4.3",
    "grok-2-1212": "grok-4.3",
    "grok-2-vision-1212": "grok-4.5",
    "grok-2-vision": "grok-4.5",
    "grok-beta": "grok-4.5",
    "grok-vision-beta": "grok-4.5",
}


def normalize_llm_settings(
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> tuple[str, str, str]:
    """Align provider, model, and base_url so they don't cross-wire.

    Examples of bad states we fix:
    - provider=deepseek, model=claude-3-haiku  → model=deepseek-v4-flash
    - provider=deepseek, base_url=api.openai.com → base_url=api.deepseek.com
    - provider=deepseek, model=deepseek-chat → deepseek-v4-flash (retired id)
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

    # Map retired provider model ids before ownership checks.
    alias = _LEGACY_MODEL_ALIASES.get(mid.lower())
    if alias:
        mid = alias

    # If URL clearly belongs to another known provider, snap to this provider's URL.
    url_owner = infer_provider_from_base_url(url)
    if url_owner and url_owner != prov and prov in PROVIDER_CATALOG:
        # OpenRouter intentionally hosts many vendors — only snap when *this*
        # provider is not openrouter/custom.
        if prov not in ("openrouter", "custom", "ollama", "poe"):
            url = default_url

    # Flexible providers can host any model id (Ollama pulls deepseek-*, etc.).
    # Poe hosts many labs' bots under Poe bot names (not closed native catalogs).
    # RMB / llama.cpp load arbitrary GGUF stems — must NOT snap to catalog 7B.
    # Demo is *not* flexible — guest gateway junk (image/video/foreign) is clamped
    # to the curated allowlist so free-setup never silently hits blocked models.
    _FLEXIBLE = frozenset({"openrouter", "custom", "ollama", "poe", "rmb", "llamacpp"})

    model_owner = infer_provider_from_model(mid)
    if model_owner and model_owner != prov and prov not in _FLEXIBLE and prov != "demo":
        mid = default_model
    elif prov == "demo" and default_models:
        known = {m["id"] for m in default_models}
        if mid not in known:
            mid = default_model
    elif prov in PROVIDER_CATALOG and default_models and prov not in _FLEXIBLE:
        known = {m["id"] for m in default_models}
        if not mid:
            mid = default_model
        # Closed catalogs: snap foreign / garbage ids to provider default.
        # Allow native-family prefixes (deepseek-*, grok-*) for live /models extras.
        elif mid not in known and not _native_model_id_for_provider(prov, mid):
            mid = default_model
        if prov in _CLOSED_PROVIDERS and model_owner and model_owner != prov:
            mid = default_model

    if not url:
        url = default_url
    if not mid:
        mid = default_model

    return prov, mid, url


def _native_model_id_for_provider(provider: str, model_id: str) -> bool:
    """True when *model_id* looks like a native id for *provider* (not catalog-only)."""
    mid = (model_id or "").strip().lower()
    prov = (provider or "").strip().lower()
    if not mid or not prov:
        return False
    if prov == "deepseek":
        return mid.startswith("deepseek")
    if prov == "xai":
        return mid.startswith("grok") or mid.startswith("xai/")
    if prov == "openai":
        return mid.startswith(("gpt-", "o1", "o3", "o4", "chatgpt-"))
    if prov == "anthropic":
        return mid.startswith("claude")
    if prov == "google":
        return mid.startswith("gemini") or mid.startswith("models/gemini")
    if prov == "mistral":
        return mid.startswith(("mistral", "codestral", "open-mistral"))
    if prov == "groq":
        return mid.startswith("groq/") or mid in {
            m["id"] for m in (PROVIDER_CATALOG.get("groq") or {}).get("models") or []
        }
    return False


def validate_provider_model(provider: str | None, model: str | None) -> str:
    """Return a clean model id or raise ValueError for closed-catalog garbage.

    Flexible providers (ollama/custom/openrouter) accept any non-empty id.
    Closed providers accept catalog ids + native-family prefixes only.
    """
    prov = (provider or "").strip().lower() or "openai"
    mid = (model or "").strip()
    if not mid:
        raise ValueError("Model id is required")
    # Demo uses a hard curated allowlist (same as catalog route filter).
    if prov == "demo":
        catalog = PROVIDER_CATALOG.get("demo") or {}
        known = {m["id"] for m in (catalog.get("models") or [])}
        if mid in known:
            return mid
        sample = ", ".join(sorted(known)[:8]) or "(none)"
        raise ValueError(
            f"Unknown demo model {mid!r}. Guest free chat allows: {sample}."
        )
    _FLEXIBLE = frozenset({"openrouter", "custom", "ollama", "poe", "rmb", "llamacpp"})
    if prov in _FLEXIBLE or prov not in PROVIDER_CATALOG:
        return mid
    # Apply legacy aliases first
    mid = _LEGACY_MODEL_ALIASES.get(mid.lower(), mid)
    catalog = PROVIDER_CATALOG.get(prov) or {}
    known = {m["id"] for m in (catalog.get("models") or [])}
    if mid in known or _native_model_id_for_provider(prov, mid):
        return mid
    sample = ", ".join(sorted(known)[:8]) or "(none)"
    raise ValueError(
        f"Unknown model {mid!r} for provider {prov!r}. "
        f"Pick a listed model (e.g. {sample})."
    )


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

    llm_provider = _resolve_str(
        config.get("llm_provider"),
        "REMEDY_LLM_PROVIDER",
        "openai",
    )

    # Per-provider keys (DeepSeek sk- never rides into xAI, etc.).
    llm_api_key = resolve_provider_api_key(config, llm_provider)

    # Local servers (Ollama, Kobold.cpp, LM Studio, etc.) don't need a real
    # API key.  Supply a dummy value so the agent doesn't fall back to echo
    # mode when the user hasn't set one.
    if not llm_api_key and _is_local_url(llm_base_url):
        llm_api_key = "local"
        logger.info("No API key set for local LLM at %s — using dummy key", llm_base_url)

    am = str(config.get("approval_mode") or "ask").strip().lower()
    if am not in ("ask", "auto"):
        am = "ask"
    scope = str(config.get("access_scope") or "project").strip().lower() or "project"
    hm = str(config.get("harness_mode") or "auto").strip().lower()
    if hm not in ("off", "manual", "auto"):
        hm = "auto"
    tl = str(config.get("thinking_level") or "high").strip().lower()
    if tl not in ("off", "low", "medium", "high"):
        tl = "high"

    _ag = str(config.get("agent_gender") or "female").strip().lower()
    if _ag not in ("female", "male", "neutral"):
        _ag = "female"
    return AgentConfig(
        name=config.get("name", "Remedy") or "Remedy",
        agent_gender=_ag,
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
        llm_provider=llm_provider,
        llm_api_key=llm_api_key,
        llm_model=_resolve_str(
            config.get("llm_model"),
            "REMEDY_LLM_MODEL",
            "gpt-4o-mini",
        ),
        llm_base_url=llm_base_url,
        sleev_enabled=bool(config.get("sleev_enabled", False)),
        sleev_gateway_url=str(config.get("sleev_gateway_url") or "").strip(),
        project_path=config.get("project_path") or os.environ.get("REMEDY_PROJECT_PATH") or None,
        # Partner trust / agency — must come from config.toml (status-bar thumbs).
        # Previously omitted → always defaulted to approval_mode="ask" at startup
        # while Settings UI still showed auto from raw TOML.
        access_scope=scope,
        harness_mode=hm,
        harness_min_context_pct=float(config.get("harness_min_context_pct") or 0.75),
        harness_max_context_pct=float(config.get("harness_max_context_pct") or 0.92),
        thinking_level=tl,
        approval_mode=am,
        launch_at_login=bool(config.get("launch_at_login", False)),
        start_in_tray=bool(config.get("start_in_tray", False)),
        close_to_tray=bool(config.get("close_to_tray", False)),
    )


def generate_default_config(
    home_dir: Path,
    *,
    first_run_project: Path | None = None,
) -> str:
    """Generate a default TOML config file content.

    On first-run only, *first_run_project* (New Project folder) may be written so
    Settings has a safe starter path. New chat sessions still start with no
    project (root) unless the user attaches one.
    """
    if first_run_project is not None:
        proj_line = f'project_path = "{Path(first_run_project).expanduser().as_posix()}"'
        scope_line = 'access_scope = "project"   # first-run sandbox; change to full when ready'
    else:
        proj_line = (
            '# project_path = ""  # empty = no default; New Session = root (full access)'
        )
        scope_line = '# access_scope = "project"   # project | home | full | untrusted'
    return f"""# Remedy AI Configuration
# Generated by remedy config init
# Owner's manual (desktop F1) and LICENSE / COMMERCIAL.md describe free-tier terms.

name = "Remedy"
# Partner presentation: female (default) | male | neutral
agent_gender = "female"
# What your partner calls you (desktop Settings → You & Agent)
# user_name = "You"
persona = "default"
home_dir = "{home_dir.as_posix()}"

# First-run setup: false until `remedy setup` / desktop wizard / --skip-setup
setup_completed = false

# Optional default folder (Settings). New Session does NOT auto-attach a project —
# sessions without a project are root (full access). Pick a project per session
# or set a default here for tools that need a workspace root.
{proj_line}
{scope_line}

# --- LLM Provider ---
# Supported: openai, anthropic, google, deepseek, xai, groq, mistral, openrouter, ollama, custom
# xAI also supports OAuth (Sign in with xAI) via desktop Settings / `remedy auth login xai`
llm_provider = "openai"
llm_model = "gpt-4o-mini"
llm_base_url = "https://api.openai.com/v1"
# llm_api_key - prefer secure store (desktop Settings) or REMEDY_LLM_API_KEY env:
# llm_api_key = "sk-..."
# XAI_API_KEY auto-selects xAI on first run when present

# Sleev (https://sleev.ai) — local gateway that compresses agent context before
# it hits your provider. Same keys/models; saves tokens on long sessions.
# Install: npm install -g sleev && sleev  (gateway default http://127.0.0.1:17321)
# sleev_enabled = false
# sleev_gateway_url = ""   # empty = auto-discover from Sleev install

# Search paths for bundled + user skills
skills_dir = []

# SQLite memory database
memory_db_path = "{home_dir.joinpath('memory.db').as_posix()}"

# Soft cap for skills in the hot catalog (Settings → Provider catalog)
# skills_active_budget = 80

# Messaging channels (also Settings → Messengers). Tokens live in the secure store.
# Desktop sessions share the same memory.db — messenger chats appear in the sidebar.
# cli, telegram, discord, slack, mattermost, whatsapp, teams, matrix, google_chat, signal, web, api
enabled_channels = ["cli"]

# MCP *client* server list (advanced). Desktop also exposes Remedy as MCP *host*.
mcp_servers = []

# Learning loop / partner skills
allow_skill_creation = true
auto_approve_threshold = 0.8

# Logging: DEBUG | INFO | WARNING | ERROR
log_level = "INFO"

# Optional tone flag (Settings → Advanced); default off
sarcasm_mode = false

# --- Partner controls (also on status bar / Settings → Security & power) ---
# approval_mode = "ask"     # ask (safe default) | auto (work-until-done, full owner power)
# thinking_level = "high"   # off | low | medium | high
# tool_process = "off"      # off | medium | full
# web_tools_enabled = false # opt-in public web_fetch (SSRF-guarded)
# http_bootstrap — omit for auto: False on desktop sidecar, True on plain serve.
# http_bootstrap = true     # browser loopback token; desktop prefers IPC

# Maturity gates (experimental / advanced organs — default off / honest)
# soul_field_enabled = true    # personhood / Soul Field (default on; set false to opt out)
# build_os_advanced = false    # Build OS frontiers A–H tools
# rmb_enabled = true           # RMB local agent host allowed when configured

# Retention (days; 0 = never auto-purge). Soft defaults: shots 14d, undo/logs 30d
# retention_session_days = 0
# retention_attachment_days = 0
# retention_computer_shot_days = 14
# retention_undo_days = 30
# retention_log_days = 30
# memory_encrypt = false       # SQLCipher when a cipher-linked sqlite is installed

# Memory harness (chat compression)
# harness_mode = "auto"     # off | manual | auto
# harness_min_context_pct = 0.75
# harness_max_context_pct = 0.92

# Desktop always-ready (also in Settings / desktop.json)
# launch_at_login = false
# start_in_tray = false
# close_to_tray = true

# In-app Browser slide homepage (Settings → Project workspace)
# browser_home_url = "https://github.com/AhmiDarrow/RemedyAI"

[gateway]
heartbeat_interval = 60
rate_limit = 120

[execution]
default_timeout = 30
max_retries = 3
retry_backoff = 1.0

# Messengers — non-secret fields only (bot tokens via Settings / secret store)
[telegram]
allow_chat_ids = []
allow_all = false

[discord]
channel_id = ""
guild_id = ""

[slack]
channel_id = ""

[mattermost]
base_url = ""
team_id = ""
channel_id = ""

[whatsapp]
phone_number_id = ""
allow_from = []

[teams]
app_id = ""
tenant_id = ""

[matrix]
homeserver = ""
user_id = ""
room_id = ""

[google_chat]
space_id = ""

[signal]
cli_path = "signal-cli"
account = ""

# Local visual decoder (llama-server + SmolVLM2 2.2B) — first-run download; not in installer
[vision]
enabled = false
model_id = "smolvlm2-2.2b"
# Prefer local image→text even when the chat model has native vision (saves provider tokens)
force_decode = false
host = "127.0.0.1"
port = 8740
auto_start = true
idle_stop_s = 600
"""


def create_default_config(home_dir: Path | None = None) -> Path:
    """Create a default config file in the home directory (first run only)."""
    hd = (home_dir or Path("~/.remedy")).expanduser()
    hd.mkdir(parents=True, exist_ok=True)
    config_path = hd / "config.toml"
    if not config_path.exists():
        first_run_project = None
        try:
            from remedy.core.workspace import ensure_new_project_seed

            # One-time: create Documents/…/New Project and record it in config.
            # Users can clear project_path later; New Session stays root either way.
            first_run_project = ensure_new_project_seed()
        except Exception:
            first_run_project = None
        config_path.write_text(
            generate_default_config(hd, first_run_project=first_run_project),
            encoding="utf-8",
        )
    return config_path


def config_path_for_home(home_dir: str | Path | None = None) -> Path:
    """Return the canonical config.toml path for a home directory."""
    hd = Path(home_dir or Path.home() / ".remedy").expanduser()
    return hd / "config.toml"


def config_file_readable(config_path: Path | None = None) -> bool:
    """True when config.toml exists and parses as a non-empty TOML table.

    Corrupt TOML (e.g. root keys written after ``[table]`` sections) fails parse
    and must be treated as first-run / repair territory.
    """
    path = Path(config_path or config_path_for_home()).expanduser()
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(content) or {}
        else:
            data = tomllib.loads(content)
        return isinstance(data, dict)
    except Exception:
        return False


def needs_first_run_setup(
    config: dict[str, Any] | None = None,
    *,
    home_dir: str | Path | None = None,
    config_path: Path | None = None,
) -> bool:
    """Return True when first-run setup should run before launch.

    Rules:
    - No config file → need setup
    - Config unreadable / corrupt → need setup (repair via wizard)
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

    # Corrupt file: load_config returns {} and would look like a "legacy" install
    # without setup_completed — force the wizard so users are not stuck.
    if not config_file_readable(path):
        return True

    cfg = config if config is not None else load_config(path)
    # Empty parse result on an existing file is also first-run territory.
    if not cfg and path.exists():
        return True
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

    # Shared writer: scalars before tables + secret scrub (single code path).
    from remedy.interfaces.api_support import _write_config as api_write

    api_write(path, cfg)
    return path


def _toml_scalar(value: Any) -> str:
    if value is None:
        # Callers should skip None; keep a defined encoding for list elements.
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_toml_scalar(v) for v in value if v is not None)
        return f"[{items}]"
    # Escape backslashes and quotes for TOML strings
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def provider_credentials_ready(config: dict[str, Any] | None = None) -> bool:
    """True when an LLM key is configured (or local URL which needs none)."""
    # Use a copy of the *input* mapping — migrate_provider_keys scrubs llm_api_key.
    raw = dict(config) if config is not None else load_config()
    provider = str(
        raw.get("llm_provider") or os.environ.get("REMEDY_LLM_PROVIDER") or ""
    ).lower()
    base = str(
        raw.get("llm_base_url")
        or os.environ.get("REMEDY_LLM_BASE_URL")
        or ""
    ).strip()
    if base and _is_local_url(base):
        return True
    if provider == "ollama":
        return True
    if provider == "demo":
        # Air-gapped / enterprise builds can disable guest demo.
        return os.environ.get("REMEDY_DEMO_DISABLED", "").strip().lower() not in (
            "1",
            "true",
            "yes",
        )
    plain = str(raw.get("llm_api_key") or "").strip()
    if not plain and config is None:
        plain = str(os.environ.get("REMEDY_LLM_API_KEY") or "").strip()
    if plain:
        if provider == "xai":
            return looks_like_xai_credential(plain) or plain.startswith("xai-")
        return True
    # Secure store / OAuth / env (may migrate leftovers as a side effect).
    key = resolve_provider_api_key(raw, provider or "openai")
    return bool(key)


def public_provider_catalog(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Catalog entries for GET /api/providers and desktop UI.

    ``config`` (optional) lets the caller inject the user-set custom endpoint name
    (``custom_llm_name``) so Settings + the status bar show the friendly label.
    When omitted, the name is loaded lazily from config for the custom entry only.
    """
    items: list[dict[str, Any]] = []
    for pid, meta in PROVIDER_CATALOG.items():
        auth_modes = list(meta.get("auth") or ["api_key"])
        if pid in ("ollama", "demo", "rmb", "llamacpp"):
            auth_modes = ["none"]
        models = list(meta.get("models") or [])
        default_model = str(models[0]["id"]) if models else "default"
        name = meta.get("label") or pid
        if pid == "custom":
            custom_name = str((config or {}).get("custom_llm_name") or "").strip()
            if config is None:
                try:
                    custom_name = str(load_config().get("custom_llm_name") or "").strip()
                except Exception:
                    custom_name = ""
            name = custom_name or name
        items.append(
            {
                "id": pid,
                "name": name,
                "base_url": meta.get("base_url"),
                "models": models,
                "default_model": default_model,
                "auth": auth_modes,
                "oauth": "oauth" in auth_modes,
                "env_keys": list(meta.get("env_keys") or []),
                "show_base_url": bool(meta.get("show_base_url", pid in ("custom", "ollama"))),
                "advanced": bool(meta.get("advanced", False)),
                "key_docs_url": meta.get("key_docs_url"),
                "free_tier": meta.get("free_tier") or "none",
                "badge": meta.get("badge"),
                "limits_blurb": meta.get("limits_blurb"),
                "privacy_note": meta.get("privacy_note"),
            }
        )
    return items


def detect_ollama(base_url: str | None = None, timeout: float = 1.5) -> dict[str, Any]:
    """Probe local Ollama (tags API). Returns available flag + model names."""
    import json
    import urllib.error
    import urllib.request

    url = (base_url or PROVIDER_CATALOG["ollama"]["base_url"] or "").rstrip("/")
    # Native tags endpoint (strip /v1 if present)
    tags_url = url.removesuffix("/v1") + "/api/tags"
    models: list[str] = []
    try:
        req = urllib.request.Request(
            tags_url,
            headers={"Accept": "application/json", "User-Agent": "Remedy/detect-ollama"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8") or "{}")
        for m in body.get("models") or []:
            name = m.get("name") or m.get("model") or ""
            if name:
                # Prefer short name without :latest
                short = name.rstrip(":latest") if name.endswith(":latest") else name
                models.append(short)
        return {
            "available": True,
            "base_url": PROVIDER_CATALOG["ollama"]["base_url"],
            "models": models,
            "tags_url": tags_url,
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return {
            "available": False,
            "base_url": PROVIDER_CATALOG["ollama"]["base_url"],
            "models": [],
            "tags_url": tags_url,
        }


def apply_env_provider_bootstrap(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Preselect provider from env keys on clean / default config.

    Plan: if ``XAI_API_KEY`` is set and the config is still effectively default
    (no explicit key, provider openai default or empty), switch to xAI.
    Also honors provider-specific env keys when config has no API key yet.
    Mutates a copy; does not write disk.
    """
    cfg = dict(config or {})
    has_key = bool(str(cfg.get("llm_api_key") or "").strip())
    if has_key:
        return cfg

    provider = str(cfg.get("llm_provider") or "").strip().lower()
    # Only auto-switch when unset or still on the factory default openai.
    allow_switch = provider in ("", "openai")

    # Priority: xAI first (plan requirement), then other known env keys.
    xai_key = (
        os.environ.get("XAI_API_KEY", "").strip()
        or os.environ.get("REMEDY_XAI_API_KEY", "").strip()
    )
    if xai_key and allow_switch:
        prov, model, url = normalize_llm_settings("xai", cfg.get("llm_model"), cfg.get("llm_base_url"))
        cfg["llm_provider"] = prov
        cfg["llm_model"] = model
        cfg["llm_base_url"] = url
        # Do not persist raw key into config dict here; resolve_bearer/env handles auth.
        return cfg

    # Optional: if Ollama is running and no cloud keys, suggest ollama (detect only
    # when still on default openai with empty key — non-blocking soft prefer).
    if allow_switch and not os.environ.get("REMEDY_LLM_API_KEY", "").strip():
        # Skip network probe unless explicitly requested via env (wizard/API call
        # does active detect). Soft file-free bootstrap stays offline-safe.
        if os.environ.get("REMEDY_PREFER_OLLAMA", "").strip() in ("1", "true", "yes"):
            ollama = detect_ollama()
            if ollama.get("available"):
                models = ollama.get("models") or []
                mid = models[0] if models else "llama3.2"
                cfg["llm_provider"] = "ollama"
                cfg["llm_model"] = mid
                cfg["llm_base_url"] = PROVIDER_CATALOG["ollama"]["base_url"]
                return cfg

    # Map other env API keys → provider when still on default.
    if allow_switch:
        env_map = (
            ("GROQ_API_KEY", "groq"),
            ("MISTRAL_API_KEY", "mistral"),
            ("DEEPSEEK_API_KEY", "deepseek"),
            ("OPENROUTER_API_KEY", "openrouter"),
            ("POE_API_KEY", "poe"),
            ("REMEDY_POE_API_KEY", "poe"),
            ("ANTHROPIC_API_KEY", "anthropic"),
            ("OPENAI_API_KEY", "openai"),
            ("GOOGLE_API_KEY", "google"),
            ("GEMINI_API_KEY", "google"),
        )
        for env_name, pid in env_map:
            if os.environ.get(env_name, "").strip():
                prov, model, url = normalize_llm_settings(
                    pid, cfg.get("llm_model"), cfg.get("llm_base_url")
                )
                cfg["llm_provider"] = prov
                cfg["llm_model"] = model
                cfg["llm_base_url"] = url
                return cfg

    # Zero-setup demo so first run can chat without any keys (opt-out via env).
    if (
        allow_switch
        and os.environ.get("REMEDY_DEMO_DISABLED", "").strip().lower()
        not in ("1", "true", "yes")
        and os.environ.get("REMEDY_PREFER_DEMO", "1").strip().lower()
        not in ("0", "false", "no")
    ):
        prov, model, url = normalize_llm_settings(
            "demo",
            cfg.get("llm_model") if str(cfg.get("llm_provider") or "") == "demo" else None,
            None,
        )
        cfg["llm_provider"] = prov
        cfg["llm_model"] = model
        cfg["llm_base_url"] = url
        return cfg

    return cfg

# Public re-exports (catalog lives in provider_catalog; keep import path stable)
from remedy.interfaces.provider_catalog import free_options_public as free_options_public  # noqa: E402,F401
