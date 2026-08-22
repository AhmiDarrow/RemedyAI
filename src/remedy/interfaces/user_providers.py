"""User-defined providers ("saved custom endpoints").

The *Custom endpoint* row in Settings is a template: saving it under a name
creates a provider of its own (id ``custom-<slug>``) that shows up in every
picker like a built-in one, and the template stays blank for the next entry.

Specs live in ``config.toml`` under ``[custom_providers]`` as inline tables —
never secrets; keys go to the secure store under the provider id, exactly
like built-ins. At load time the specs are merged into ``PROVIDER_CATALOG``
so the rest of the code (normalize/validate, discovery, routes, adapters)
sees them without special cases.

    custom-gpu-box = {label = "GPU box", base_url = "http://gpu:8000/v1",
                      flavour = "openai", auth = "api_key"}
"""
from __future__ import annotations

import re
from typing import Any

from remedy.interfaces.provider_catalog import PROVIDER_CATALOG

USER_PROVIDER_PREFIX = "custom-"
CONFIG_KEY = "custom_providers"

_FLAVOURS = ("openai", "anthropic", "ollama", "gemini")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def is_user_provider(pid: str | None) -> bool:
    p = (pid or "").strip().lower()
    return p.startswith(USER_PROVIDER_PREFIX) or bool(
        (PROVIDER_CATALOG.get(p) or {}).get("user_defined")
    )


def slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return s[:40] or "endpoint"


def provider_id_for(name: str, existing: set[str] | None = None) -> str:
    base = USER_PROVIDER_PREFIX + slugify(name)
    taken = set(existing or ()) | set(PROVIDER_CATALOG)
    pid, n = base, 2
    while pid in taken and not (PROVIDER_CATALOG.get(pid) or {}).get("user_defined"):
        pid = f"{base}-{n}"
        n += 1
    return pid


def normalize_spec(spec: dict[str, Any] | None) -> dict[str, Any]:
    """Shape-check one stored spec (tolerates hand-edited config)."""
    spec = spec if isinstance(spec, dict) else {}
    flavour = str(spec.get("flavour") or "openai").strip().lower()
    if flavour not in _FLAVOURS:
        flavour = "openai"
    auth = str(spec.get("auth") or "api_key").strip().lower()
    if auth not in ("api_key", "none"):
        auth = "api_key"
    return {
        "label": str(spec.get("label") or "").strip(),
        "base_url": str(spec.get("base_url") or "").strip().rstrip("/"),
        "flavour": flavour,
        "auth": auth,
    }


def specs_from_config(cfg: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    raw = (cfg or {}).get(CONFIG_KEY)
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for pid, spec in raw.items():
        p = str(pid or "").strip().lower()
        if not p.startswith(USER_PROVIDER_PREFIX):
            continue
        norm = normalize_spec(spec)
        if not norm["base_url"]:
            continue
        norm["label"] = norm["label"] or p[len(USER_PROVIDER_PREFIX):]
        out[p] = norm
    return out


def catalog_entry(pid: str, spec: dict[str, Any]) -> dict[str, Any]:
    spec = normalize_spec(spec)
    return {
        "label": spec["label"] or pid,
        "base_url": spec["base_url"],
        "auth": [spec["auth"]],
        "env_keys": [],
        "show_base_url": True,
        "user_defined": True,
        "flavour": spec["flavour"],
        "free_tier": "none",
        "limits_blurb": "Saved custom endpoint. Models come from the host itself.",
        "models": [],
    }


def sync_catalog(cfg: dict[str, Any] | None) -> list[str]:
    """Make PROVIDER_CATALOG's user-defined rows match *cfg*. Returns ids."""
    specs = specs_from_config(cfg)
    for pid in [p for p, m in PROVIDER_CATALOG.items() if m.get("user_defined")]:
        if pid not in specs:
            PROVIDER_CATALOG.pop(pid, None)
    for pid, spec in specs.items():
        PROVIDER_CATALOG[pid] = catalog_entry(pid, spec)
    return list(specs)


def adapter_flavour(pid: str | None) -> str | None:
    meta = PROVIDER_CATALOG.get((pid or "").strip().lower()) or {}
    if not meta.get("user_defined"):
        return None
    return str(meta.get("flavour") or "openai")


def upsert_spec(
    cfg: dict[str, Any],
    *,
    name: str,
    base_url: str,
    flavour: str | None = None,
    auth: str | None = None,
    pid: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Add/replace a spec in *cfg* (mutates a copy) → (cfg, provider id)."""
    cfg = dict(cfg or {})
    table = dict(cfg.get(CONFIG_KEY) or {}) if isinstance(cfg.get(CONFIG_KEY), dict) else {}
    pid = (pid or "").strip().lower() or provider_id_for(name, set(table))
    spec = normalize_spec(
        {"label": name, "base_url": base_url, "flavour": flavour, "auth": auth}
    )
    if not spec["base_url"]:
        raise ValueError("base_url is required")
    if not spec["label"]:
        raise ValueError("name is required")
    table[pid] = spec
    cfg[CONFIG_KEY] = table
    sync_catalog(cfg)
    return cfg, pid


def remove_spec(cfg: dict[str, Any], pid: str) -> dict[str, Any]:
    cfg = dict(cfg or {})
    table = dict(cfg.get(CONFIG_KEY) or {}) if isinstance(cfg.get(CONFIG_KEY), dict) else {}
    table.pop((pid or "").strip().lower(), None)
    cfg[CONFIG_KEY] = table
    sync_catalog(cfg)
    return cfg
