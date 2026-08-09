"""Sleev gateway routing — compress agent context before it hits the provider.

Sleev (https://sleev.ai) is a local OpenAI/Anthropic-compatible proxy that
compresses stale conversation history to cut token spend. Remedy stays the
harness: same provider keys, same models — only the request host and a few
headers change when Sleev is enabled.

Protocol (from Sleev docs + live gateway probes):
  * Gateway default: ``http://127.0.0.1:17321``
  * Required header: ``sleev-harness: remedy``
  * Built-in upstreams: ``sleev-provider: <id>`` (anthropic, openai, google, …)
  * Custom upstreams (xAI, DeepSeek, Groq, …): ``sleev-base-url: <provider API base>``
  * Auth is forwarded as the harness already sends it (Bearer / x-api-key)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

SLEEV_HARNESS_ID = "remedy"
SLEEV_DEFAULT_HOST = "127.0.0.1"
SLEEV_DEFAULT_PORT = 17321
SLEEV_DEFAULT_GATEWAY = f"http://{SLEEV_DEFAULT_HOST}:{SLEEV_DEFAULT_PORT}"

# Providers Sleev routes by name (no sleev-base-url needed).
# Everything else uses sleev-base-url with the real provider base.
SLEEV_BUILTIN_PROVIDERS: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "google": "google",
}

# Local / non-cloud endpoints — never route through Sleev (no cloud bill to cut).
_LOCAL_ONLY_PROVIDERS = frozenset(
    {
        "ollama",
        "llamacpp",
        "rmb",
        "demo",
    }
)


def _local_hosts() -> frozenset[str]:
    return frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})


def is_loopback_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(str(url)).hostname or "").lower()
    except Exception:
        return False
    return host in _local_hosts() or host.endswith(".local")


def sleev_config_paths() -> list[Path]:
    """Candidate paths for Sleev's config.json (Windows + Unix)."""
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA") or ""
    if appdata:
        paths.append(Path(appdata) / "sleev" / "config.json")
    xdg = os.environ.get("XDG_CONFIG_HOME") or ""
    if xdg:
        paths.append(Path(xdg) / "sleev" / "config.json")
    home = Path.home()
    paths.append(home / ".config" / "sleev" / "config.json")
    paths.append(home / "AppData" / "Roaming" / "sleev" / "config.json")
    # de-dupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def read_sleev_install_config() -> dict[str, Any] | None:
    """Load Sleev CLI config.json if present."""
    for path in sleev_config_paths():
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.debug("sleev config read failed (%s): %s", path, exc)
    return None


def discover_sleev_gateway_url(cfg: dict[str, Any] | None = None) -> str:
    """Resolve the local Sleev gateway base URL (no trailing slash, no /v1).

    Priority:
      1. Explicit ``cfg['sleev_gateway_url']`` or ``REMEDY_SLEEV_GATEWAY``
      2. Sleev install config (proxy.host / proxy.port)
      3. Default ``http://127.0.0.1:17321``
    """
    raw = ""
    if cfg:
        raw = str(cfg.get("sleev_gateway_url") or "").strip()
    if not raw:
        raw = str(os.environ.get("REMEDY_SLEEV_GATEWAY") or "").strip()
    if raw:
        return _normalize_gateway_root(raw)

    install = read_sleev_install_config() or {}
    proxy = install.get("proxy") if isinstance(install.get("proxy"), dict) else {}
    host = str(proxy.get("host") or SLEEV_DEFAULT_HOST).strip() or SLEEV_DEFAULT_HOST
    try:
        port = int(proxy.get("port") or SLEEV_DEFAULT_PORT)
    except (TypeError, ValueError):
        port = SLEEV_DEFAULT_PORT
    return _normalize_gateway_root(f"http://{host}:{port}")


def normalize_gateway_root(url: str) -> str:
    """Strip trailing slash and a trailing ``/v1`` so adapters append paths cleanly."""
    u = (url or "").strip().rstrip("/")
    if u.lower().endswith("/v1"):
        u = u[:-3].rstrip("/")
    return u or SLEEV_DEFAULT_GATEWAY


# Back-compat alias (internal call sites / older imports).
_normalize_gateway_root = normalize_gateway_root


def cfg_from_runtime(runtime: Any | None = None) -> dict[str, Any] | None:
    """Build a minimal sleev cfg dict from AgentConfig / runtime without disk I/O.

    Falls back to ``load_config()`` only when runtime has no sleev fields.
    """
    if runtime is not None:
        cfg: dict[str, Any] = {}
        conf = getattr(runtime, "config", None)
        # Prefer live attributes (hot-reloaded from Settings)
        for src in (conf, runtime):
            if src is None:
                continue
            if hasattr(src, "sleev_enabled"):
                cfg["sleev_enabled"] = bool(getattr(src, "sleev_enabled"))
            if hasattr(src, "sleev_gateway_url"):
                gw = str(getattr(src, "sleev_gateway_url") or "").strip()
                if gw:
                    cfg["sleev_gateway_url"] = gw
        # Also accept private attrs set on runtime without AgentConfig
        if "sleev_enabled" not in cfg and hasattr(runtime, "_sleev_enabled"):
            cfg["sleev_enabled"] = bool(getattr(runtime, "_sleev_enabled"))
        if "sleev_gateway_url" not in cfg and hasattr(runtime, "_sleev_gateway_url"):
            gw = str(getattr(runtime, "_sleev_gateway_url") or "").strip()
            if gw:
                cfg["sleev_gateway_url"] = gw
        if cfg:
            return cfg
    try:
        from remedy.interfaces.api_support import load_config

        loaded = load_config()
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def is_sleev_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """True when the user opted into routing cloud LLM traffic via Sleev."""
    if cfg is not None and "sleev_enabled" in cfg:
        return bool(cfg.get("sleev_enabled"))
    env = str(os.environ.get("REMEDY_SLEEV_ENABLED") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return False


def sleev_installed() -> bool:
    """Best-effort: Sleev CLI config present on this machine."""
    return read_sleev_install_config() is not None


def should_route_via_sleev(
    provider: str | None,
    base_url: str | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> bool:
    """Whether this binding should go through the Sleev gateway."""
    if not is_sleev_enabled(cfg):
        return False
    prov = str(provider or "").strip().lower()
    if not prov or prov in _LOCAL_ONLY_PROVIDERS:
        return False
    # Already pointing at Sleev (or any loopback proxy) — still inject headers
    # when enabled so re-saves of base_url aren't required.
    if is_loopback_url(base_url):
        # Local llama/Ollama/RMB ports must never get sleev headers.
        try:
            port = urlparse(str(base_url or "")).port
        except Exception:
            port = None
        gw = discover_sleev_gateway_url(cfg)
        try:
            gw_port = urlparse(gw).port or SLEEV_DEFAULT_PORT
        except Exception:
            gw_port = SLEEV_DEFAULT_PORT
        if port is not None and port != gw_port:
            return False
    return True


def _catalog_base_url(provider: str) -> str:
    """Catalog default base for a provider id (empty if unknown)."""
    try:
        from remedy.interfaces.provider_catalog import PROVIDER_CATALOG

        cat = PROVIDER_CATALOG.get(provider) or {}
        return str(cat.get("base_url") or "").strip().rstrip("/")
    except Exception:
        return ""


def _is_generic_openai_default(url: str) -> bool:
    """True when *url* is the stock OpenAI API root (shared adapter default)."""
    u = (url or "").strip().rstrip("/").lower()
    return u in (
        "https://api.openai.com/v1",
        "http://api.openai.com/v1",
        "https://api.openai.com",
        "http://api.openai.com",
    )


def upstream_base_url(
    provider: str | None,
    base_url: str | None = None,
    *,
    cfg: dict[str, Any] | None = None,
) -> str:
    """The real provider API base (for sleev-base-url), never the Sleev gateway.

    Prefer the provider catalog when the bound base URL is empty, already the
    Sleev gateway, or the generic OpenAI default on a non-OpenAI provider
    (openrouter/poe/custom share ``OpenAIProvider.default_base_url``).
    """
    prov = str(provider or "").strip().lower()
    url = str(base_url or "").strip().rstrip("/")
    gw = discover_sleev_gateway_url(cfg)
    cat_url = _catalog_base_url(prov)

    # Wrong shared adapter default (OpenAI URL while provider is openrouter/poe/…)
    if (
        url
        and prov not in ("openai", "custom", "")
        and _is_generic_openai_default(url)
        and cat_url
        and not _is_generic_openai_default(cat_url)
    ):
        return cat_url

    if url and not _is_sleev_gateway_url(url, gw):
        return url
    if cat_url:
        return cat_url
    # Provider adapter default
    try:
        from remedy.core.providers import get_provider

        return str(get_provider(prov).default_base_url or "").rstrip("/")
    except Exception:
        return url


def _is_sleev_gateway_url(url: str, gateway: str) -> bool:
    try:
        a = urlparse(url)
        b = urlparse(gateway)
        if (a.hostname or "").lower() != (b.hostname or "").lower():
            return False
        ap = a.port or (443 if a.scheme == "https" else 80)
        bp = b.port or (443 if b.scheme == "https" else 80)
        return ap == bp
    except Exception:
        return False


def sleev_headers(
    provider: str | None,
    *,
    base_url: str | None = None,
    cfg: dict[str, Any] | None = None,
    harness: str = SLEEV_HARNESS_ID,
) -> dict[str, str]:
    """Headers Sleev requires on every routed request."""
    prov = str(provider or "").strip().lower()
    headers: dict[str, str] = {
        "sleev-harness": harness or SLEEV_HARNESS_ID,
    }
    builtin = SLEEV_BUILTIN_PROVIDERS.get(prov)
    if builtin:
        headers["sleev-provider"] = builtin
        return headers
    # Custom / non-built-in: route by full upstream base URL.
    upstream = upstream_base_url(prov, base_url, cfg=cfg)
    if upstream:
        headers["sleev-base-url"] = upstream
    else:
        # Last resort — some gateways still accept unknown provider ids poorly;
        # prefer base-url. If we have neither, still send harness (will 4xx).
        logger.warning(
            "Sleev route missing upstream base for provider=%s",
            prov,
        )
    return headers


def apply_sleev_routing(
    *,
    provider: str | None,
    base_url: str | None,
    headers: dict[str, str] | None,
    cfg: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(effective_base_url, headers)`` for an LLM HTTP call.

    When Sleev is off or the provider is local-only, returns the inputs unchanged
    (base_url may be empty — caller falls back to adapter default).
    """
    hdrs = dict(headers or {})
    base = str(base_url or "").strip()
    if not should_route_via_sleev(provider, base, cfg=cfg):
        return base, hdrs

    gw = discover_sleev_gateway_url(cfg)
    extra = sleev_headers(provider, base_url=base, cfg=cfg)
    # Do not overwrite Authorization / Content-Type / x-api-key.
    for k, v in extra.items():
        if v:
            hdrs[k] = v
    logger.debug(
        "Sleev route provider=%s gateway=%s headers=%s",
        provider,
        gw,
        {k: v for k, v in extra.items()},
    )
    return gw, hdrs


def prepare_llm_http(
    *,
    provider: str | None,
    base_url: str | None,
    api_key: str | None,
    adapter: Any,
    cfg: dict[str, Any] | None = None,
    runtime: Any | None = None,
) -> tuple[str, dict[str, str]]:
    """Build ``(endpoint, headers)`` for a chat completion call (Sleev-aware).

    Prefer ``runtime`` (or explicit ``cfg``) so hot Settings toggles apply without
    re-reading config.toml on every ReAct step.
    """
    if cfg is None and runtime is not None:
        cfg = cfg_from_runtime(runtime)
    headers = adapter.auth_headers(api_key or "")
    effective_base, headers = apply_sleev_routing(
        provider=provider,
        base_url=base_url or getattr(adapter, "default_base_url", "") or "",
        headers=headers,
        cfg=cfg,
    )
    if not effective_base:
        effective_base = str(getattr(adapter, "default_base_url", "") or "")
    endpoint = adapter.chat_endpoint(effective_base)
    return endpoint, headers


def sleev_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Public status blob for Settings / diagnostics."""
    install = read_sleev_install_config()
    gateway = discover_sleev_gateway_url(cfg)
    enabled = is_sleev_enabled(cfg)
    auth_label = ""
    if isinstance(install, dict):
        auth = install.get("auth") if isinstance(install.get("auth"), dict) else {}
        auth_label = str(auth.get("email") or auth.get("label") or "").strip()
    return {
        "enabled": enabled,
        "installed": install is not None,
        "gateway_url": gateway,
        "harness": SLEEV_HARNESS_ID,
        "account_label": auth_label,
        "docs_url": "https://sleev.ai/docs/harness-setup",
        "home_url": "https://sleev.ai/",
    }
