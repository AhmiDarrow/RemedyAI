"""Shared API helpers: SSE framing, slash commands, config sync."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from remedy.interfaces.config import (
    CONFIG_PATHS,
)
from remedy.interfaces.config import (
    load_config as _load_toml_config,
)

logger = logging.getLogger(__name__)


async def _sse_stream_text(text: str, *, event: str | None = None) -> str:
    """Format a single SSE frame."""
    prefix = f"event: {event}\n" if event else ""
    payload_obj: dict = {"text": text}
    if event:
        payload_obj["type"] = event
    payload = json.dumps(payload_obj)
    return f"{prefix}data: {payload}\n\n"


def sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }


# Slash commands live in slash_commands.py (re-exported for compatibility).
from remedy.home import default_home  # noqa: E402
from remedy.interfaces.slash_commands import (  # noqa: E402
    _BUILTIN_AGENTS,
    _BUILTIN_COMMANDS,
    _BUILTIN_MODELS,
    handle_slash_command,
)

__all__ = [
    "_BUILTIN_AGENTS",
    "_BUILTIN_COMMANDS",
    "_BUILTIN_MODELS",
    "handle_slash_command",
    "sse_headers",
]


def _default_config_path() -> Path:
    """Canonical user config path (matches desktop sidecar --home).

    Honors ``REMEDY_HOME`` so tests and alternate homes never write into the
    real ``~/.remedy`` by accident.
    """
    env_home = str(os.environ.get("REMEDY_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve() / "config.toml"
    return default_home() / "config.toml"


def _find_config_path() -> Path | None:
    # Prefer the home config so desktop and CLI always share one persistent file.
    primary = _default_config_path()
    if primary.exists():
        return primary
    # When REMEDY_HOME is set (tests), never fall through to the real user home.
    if str(os.environ.get("REMEDY_HOME") or "").strip():
        return None
    for p in CONFIG_PATHS:
        expanded = p.expanduser().resolve()
        if expanded.exists():
            return expanded
    return None


def load_config() -> dict[str, Any]:
    """Load config.toml with mtime cache (routes hit this constantly).

    Returns a shallow copy so callers can mutate without poisoning the cache.
    """
    return _load_config_cached()


def _apply_llm_to_runtime(
    runtime: Any,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str | None = None,
    persona: str | None = None,
    name: str | None = None,
    agent_gender: str | None = None,
    project_path: str | None = None,
    access_scope: str | None = None,
    harness_mode: str | None = None,
    harness_min_context_pct: float | None = None,
    harness_max_context_pct: float | None = None,
    thinking_level: str | None = None,
    approval_mode: str | None = None,
    ui_language: str | None = None,
) -> None:
    """Push LLM settings into the live runtime so chat uses the saved config."""
    if runtime is None:
        return
    if hasattr(runtime, "reconfigure_llm"):
        kwargs: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
            "persona": persona,
            "name": name,
        }
        if agent_gender is not None:
            kwargs["agent_gender"] = agent_gender
        if project_path is not None:
            kwargs["project_path"] = project_path
        if access_scope is not None:
            kwargs["access_scope"] = access_scope
        if harness_mode is not None:
            kwargs["harness_mode"] = harness_mode
        if harness_min_context_pct is not None:
            kwargs["harness_min_context_pct"] = harness_min_context_pct
        if harness_max_context_pct is not None:
            kwargs["harness_max_context_pct"] = harness_max_context_pct
        if thinking_level is not None:
            kwargs["thinking_level"] = thinking_level
        if approval_mode is not None:
            kwargs["approval_mode"] = approval_mode
        if ui_language is not None:
            kwargs["ui_language"] = ui_language
        runtime.reconfigure_llm(**kwargs)
        return
    # Fallback for older runtimes without reconfigure_llm
    if provider:
        runtime._llm_provider = provider
    if model:
        runtime._llm_model = model
    if base_url:
        runtime._llm_base_url = base_url
    if api_key is not None and api_key != "":
        runtime._llm_api_key = api_key
    if project_path is not None and hasattr(runtime, "set_project_path"):
        runtime.set_project_path(project_path, as_default=True)
    if access_scope is not None:
        runtime._access_scope = access_scope
    if thinking_level is not None:
        runtime._thinking_level = str(thinking_level).strip().lower()
    if approval_mode is not None:
        try:
            from remedy.core.approvals import APPROVALS

            APPROVALS.set_mode(str(approval_mode))
        except Exception:
            pass


# Cache config disk reads across chat messages; invalidate on mtime/size change.
_config_cache: dict[str, Any] = {"path": None, "mtime": None, "size": None, "data": None}


def invalidate_config_cache() -> None:
    """Force next load_config() to re-read from disk."""
    _config_cache["path"] = None
    _config_cache["mtime"] = None
    _config_cache["size"] = None
    _config_cache["data"] = None


def _sync_user_providers(cfg: dict[str, Any]) -> None:
    """Saved custom endpoints become catalog providers the moment config loads."""
    try:
        from remedy.interfaces.user_providers import sync_catalog

        sync_catalog(cfg)
    except Exception as exc:  # pragma: no cover - never block config loads
        logger.debug("user provider sync: %s", exc)


def _load_config_cached() -> dict[str, Any]:
    """load_config() with a cheap mtime/size cache to avoid re-reading every request.

    Always returns a shallow copy so route handlers can mutate safely.
    """
    path = _find_config_path()
    if path is None:
        _sync_user_providers({})
        return {}
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        try:
            return dict(_load_toml_config(path) or {})
        except Exception:
            return {}
    if (
        _config_cache["path"] == str(path)
        and _config_cache["mtime"] == mtime
        and _config_cache["size"] == size
        and isinstance(_config_cache["data"], dict)
    ):
        _sync_user_providers(_config_cache["data"])
        return dict(_config_cache["data"])
    data = _load_toml_config(path) or {}
    if not isinstance(data, dict):
        data = {}
    _config_cache.update({"path": str(path), "mtime": mtime, "size": size, "data": data})
    # Fresh read (first load or the file changed): saved custom endpoints
    # become catalog providers right here, so every route sees them.
    _sync_user_providers(data)
    return dict(data)


def resolve_llm_slot(
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
    runtime: Any = None,
) -> tuple[str, str, str, str]:
    """Resolve (provider, model, base_url, api_key) for one turn.

    Does **not** write the process-wide runtime. Session A on Grok and session B
    on DeepSeek (or both on Grok) each get their own slot.
    """
    cfg = _load_config_cached()
    # Live runtime already applied first-run demo; do not let factory
    # openai.toml + dummy key "unused" send the turn to api.openai.com.
    if (
        runtime is not None
        and not str(provider_override or "").strip()
        and str(getattr(runtime, "_llm_provider", "") or "").strip().lower() == "demo"
    ):
        try:
            from remedy.interfaces.config import (
                normalize_llm_settings,
                resolve_provider_api_key,
            )

            provider, model, base_url = normalize_llm_settings(
                "demo",
                getattr(runtime, "_llm_model", None),
                getattr(runtime, "_llm_base_url", None) or None,
            )
            api_key = resolve_provider_api_key({"llm_provider": "demo"}, "demo")
            return provider, model, base_url, api_key or ""
        except Exception as exc:
            logger.debug("runtime demo slot bind failed: %s", exc)
    cfg_provider = str(
        cfg.get("llm_provider")
        or os.environ.get("REMEDY_LLM_PROVIDER")
        or "openai"
    ).strip().lower()
    provider = str(
        (provider_override or "").strip()
        or cfg_provider
        or "openai"
    ).strip().lower()
    model = str(
        (model_override or "").strip()
        or cfg.get("llm_model")
        or os.environ.get("REMEDY_LLM_MODEL")
        or ""
    )
    # Only reuse Settings base_url when this session is still on that provider.
    if provider == cfg_provider:
        base_url = str(
            cfg.get("llm_base_url")
            or os.environ.get("REMEDY_LLM_BASE_URL")
            or ""
        )
    else:
        base_url = ""
    try:
        from remedy.interfaces.config import normalize_llm_settings

        provider, model, base_url = normalize_llm_settings(
            provider, model, base_url or None
        )
    except Exception as exc:
        logger.debug("normalize_llm_settings in slot resolve failed: %s", exc)
    try:
        from remedy.interfaces.config import resolve_provider_api_key

        api_key = resolve_provider_api_key(cfg, provider)
    except Exception as exc:
        logger.debug("resolve_provider_api_key failed: %s", exc)
        api_key = ""
    if not api_key:
        api_key = str(
            cfg.get("llm_api_key")
            or os.environ.get("REMEDY_LLM_API_KEY")
            or ""
        )
    # Tests / embedded AgentConfig: Settings may be empty on CI.
    if not api_key and runtime is not None:
        api_key = str(getattr(runtime, "_llm_api_key", None) or "")
    if not base_url and runtime is not None:
        rt_prov = str(getattr(runtime, "_llm_provider", None) or "").strip().lower()
        rt_url = str(getattr(runtime, "_llm_base_url", None) or "")
        if rt_url and (not rt_prov or rt_prov == provider):
            base_url = rt_url
    if not api_key and provider.lower() in (
        "ollama",
        "rmb",
        "llamacpp",
        "local",
        "custom",
    ):
        api_key = "local" if provider.lower() != "rmb" else "rmb"
    # Factory openai+no key: Settings overlay is demo, but the turn bind
    # used to read raw config.toml and hit api.openai.com with "unused".
    _dummy = str(api_key or "").strip().lower() in ("", "unused", "local", "rmb", "none")
    if (
        _dummy
        and not str(provider_override or "").strip()
        and provider in ("", "openai")
    ):
        try:
            from remedy.interfaces.config import (
                apply_env_provider_bootstrap,
                normalize_llm_settings,
                resolve_provider_api_key,
            )

            boot = apply_env_provider_bootstrap(cfg)
            boot_p = str(boot.get("llm_provider") or "").strip().lower()
            if boot_p and boot_p not in ("", "openai"):
                provider, model, base_url = normalize_llm_settings(
                    boot_p,
                    boot.get("llm_model"),
                    None,
                )
                api_key = resolve_provider_api_key(boot, provider)
        except Exception as exc:
            logger.debug("first-run demo slot bootstrap failed: %s", exc)
    return provider, model, base_url, api_key or ""


def binding_for_session(
    provider: str | None,
    model: str | None,
    *,
    runtime: Any = None,
) -> Any:
    """Per-session LlmBinding. Never mutates runtime._llm_*."""
    from remedy.core.llm_binding import LlmBinding

    p, m, url, key = resolve_llm_slot(
        provider_override=provider,
        model_override=model,
        runtime=runtime,
    )
    return LlmBinding(provider=p, model=m, base_url=url, api_key=key)


def _sync_runtime_llm_from_config(
    runtime: Any,
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    llm_only: bool = False,
) -> str:
    """Reload provider/model/url/key from disk into the live runtime.

    Settings / wizard / cold start. Chat turns must use ``binding_for_session``
    instead — writing the singleton mid-stream cross-wires concurrent tabs.
    """
    if runtime is None:
        return ""
    provider, model, base_url, api_key = resolve_llm_slot(
        provider_override=provider_override,
        model_override=model_override,
        runtime=runtime,
    )

    if llm_only:
        # Chat/messenger turn: only LLM binding (safe under _llm_turn_lock).
        _apply_llm_to_runtime(
            runtime,
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key if api_key else None,
        )
        return str(getattr(runtime, "_llm_api_key", "") or api_key or "")

    # Full sync (settings save / cold start): partner trust + project + harness.
    cfg = _load_config_cached()
    from remedy.core.approvals import normalize_approval_mode

    am = normalize_approval_mode(str(cfg.get("approval_mode") or "auto"))
    scope = cfg.get("access_scope")
    _hm = cfg.get("harness_min_context_pct")
    _hx = cfg.get("harness_max_context_pct")
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key if api_key else None,
        project_path=cfg.get("project_path"),
        access_scope=str(scope) if scope is not None else None,
        harness_mode=cfg.get("harness_mode"),
        harness_min_context_pct=float(_hm) if _hm is not None else None,
        harness_max_context_pct=float(_hx) if _hx is not None else None,
        thinking_level=cfg.get("thinking_level"),
        approval_mode=am,
    )
    try:
        from remedy.core.approvals import APPROVALS

        APPROVALS.sync_from_config(cfg)
    except Exception:
        pass
    return str(getattr(runtime, "_llm_api_key", "") or api_key or "")


def _write_config(path: Path, cfg: dict[str, Any]) -> None:
    """Persist non-secret settings only. API keys never land in config.toml.

    TOML rule: all root keys must appear *before* any ``[table]`` section.
    Writing scalars after tables makes them part of the last table and can
    duplicate keys (``Cannot overwrite a value``) — which made load_config
    return {{}} and setup/save look broken on first run.
    """
    try:
        from remedy.interfaces.secret_store import scrub_config_secrets

        safe = scrub_config_secrets(cfg)
    except Exception:
        safe = dict(cfg or {})
        safe.pop("provider_keys", None)
        if "llm_api_key" in safe:
            safe["llm_api_key"] = ""

    # Refuse to serialize any remaining nested maps that look like key bags.
    lines = []
    lines.append("# Remedy AI Configuration\n\n")
    lines.append(
        "# API keys are stored in ~/.remedy/auth/ (DPAPI-encrypted on Windows),\n"
        "# not in this file.\n\n"
    )
    scalars: list[tuple[str, Any]] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in safe.items():
        if key in ("provider_keys", "llm_api_key"):
            continue  # hard block — never write secrets here
        if value is None:
            continue
        if isinstance(value, dict):
            tables.append((key, value))
        else:
            scalars.append((key, value))
    for key, value in scalars:
        lines.append(f"{key} = {_serialize_toml(value)}\n")
    if scalars and tables:
        lines.append("\n")
    for key, value in tables:
        lines.append(f"[{key}]\n")
        for k, v in value.items():
            if v is None:
                continue
            lines.append(f"{k} = {_serialize_toml(v)}\n")
        lines.append("\n")
    content = "".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace onto a unique scratch name — concurrent settings applies
    # must not interleave writes or share a temp file.
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(path, content, mode=0o600)
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    # Drop mtime cache so the next GET sees the write immediately.
    invalidate_config_cache()
    # Seed cache with what we wrote (skip another parse).
    try:
        st = path.stat()
        _config_cache.update(
            {
                "path": str(path),
                "mtime": st.st_mtime,
                "size": st.st_size,
                "data": dict(safe),
            }
        )
    except OSError:
        pass


def _serialize_toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_serialize_toml(v) for v in value)
        return f"[{items}]"
    if isinstance(value, dict):
        # Inline table — lets one-level tables hold records (custom_providers).
        items = ", ".join(
            f"{_toml_key(k)} = {_serialize_toml(v)}" for k, v in value.items() if v is not None
        )
        return "{" + items + "}"
    return json.dumps(str(value))


def _toml_key(key: Any) -> str:
    k = str(key)
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else json.dumps(k)


