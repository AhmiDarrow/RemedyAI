"""Shared API helpers: SSE framing, slash commands, config sync."""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from remedy.interfaces.config import (
    CONFIG_PATHS,
    _is_local_url,
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
    return Path.home() / ".remedy" / "config.toml"


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


def _load_config_cached() -> dict[str, Any]:
    """load_config() with a cheap mtime/size cache to avoid re-reading every request.

    Always returns a shallow copy so route handlers can mutate safely.
    """
    path = _find_config_path()
    if path is None:
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
        return dict(_config_cache["data"])
    data = _load_toml_config(path) or {}
    if not isinstance(data, dict):
        data = {}
    _config_cache.update({"path": str(path), "mtime": mtime, "size": size, "data": data})
    return dict(data)


def _sync_runtime_llm_from_config(
    runtime: Any,
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    llm_only: bool = False,
) -> str:
    """Reload provider/model/url/key from disk into the live runtime.

    Returns the effective API key (may be empty). Re-reads config when the file
    changes (or first call) so settings saved after server start apply without
    a restart, without paying for a full disk parse on every message.

    *provider_override* / *model_override*: per-session picks (status-bar switch).
    Without these, a session on Grok while global config is still DeepSeek would
    send ``model=grok-4.5`` to the DeepSeek base URL every turn.

    *llm_only*: when True (chat turns), only bind provider/model/url/key — do not
    thrash project_path / harness / approval_mode mid concurrent streams.
    """
    if runtime is None:
        return ""
    cfg = _load_config_cached()
    cfg_provider = str(
        cfg.get("llm_provider")
        or getattr(runtime, "_llm_provider", None)
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
        or getattr(runtime, "_llm_model", None)
        or os.environ.get("REMEDY_LLM_MODEL")
        or ""
    )
    # Only reuse global base_url when still on the same provider; otherwise
    # normalize_llm_settings must pick the provider's default API host.
    if provider == cfg_provider:
        base_url = str(
            cfg.get("llm_base_url")
            or getattr(runtime, "_llm_base_url", None)
            or os.environ.get("REMEDY_LLM_BASE_URL")
            or ""
        )
    else:
        base_url = ""
    # Migrate retired model ids (deepseek-chat → v4, old grok-3 → current, …)
    # and align base_url with provider so chat works without a settings re-save.
    try:
        from remedy.interfaces.config import normalize_llm_settings

        provider, model, base_url = normalize_llm_settings(
            provider, model, base_url or None
        )
    except Exception as exc:
        logger.debug("normalize_llm_settings in runtime sync failed: %s", exc)
    # Per-provider only — never reuse DeepSeek sk-… for xAI, etc.
    try:
        from remedy.interfaces.config import resolve_provider_api_key

        api_key = resolve_provider_api_key(cfg, provider)
    except Exception as exc:
        logger.debug("resolve_provider_api_key failed: %s", exc)
        api_key = str(
            cfg.get("llm_api_key")
            or os.environ.get("REMEDY_LLM_API_KEY")
            or getattr(runtime, "_llm_api_key", "")
            or ""
        )
    # Local providers: ensure a dummy key so stream path does not fall back.
    if not api_key and (
        provider.lower() in ("ollama", "rmb", "llamacpp", "local")
        or (base_url and _is_local_url(base_url))
    ):
        api_key = "local" if provider.lower() != "rmb" else "rmb"

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
    am = str(cfg.get("approval_mode") or "ask").strip().lower()
    if am not in ("ask", "auto"):
        am = "ask"
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
    # Atomic replace — concurrent settings applies must not interleave writes.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    with contextlib.suppress(OSError):
        tmp.chmod(0o600)
    os.replace(tmp, path)
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
    return json.dumps(str(value))


