"""Shared API helpers: SSE framing, slash commands, config sync."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from remedy import __version__ as _remedy_version
from remedy.interfaces.config import CONFIG_PATHS
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    load_config as _load_toml_config,
)
from remedy.interfaces.config import _is_local_url

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


# -- built-in slash commands -------------------------------------------------

_BUILTIN_COMMANDS: list[dict] = [
    {"name": "/help", "description": "Show available commands", "aliases": [], "arguments": None},
    {"name": "/new", "description": "Create a new chat session", "aliases": [], "arguments": None},
    {"name": "/sessions", "description": "List recent sessions", "aliases": [], "arguments": None},
    {"name": "/compact", "description": "Compact / summarize the current session", "aliases": [], "arguments": None},
    {"name": "/models", "description": "List available models", "aliases": [], "arguments": None},
    {"name": "/thinking", "description": "Toggle thinking visibility", "aliases": [], "arguments": None},
    {"name": "/memory", "description": "Search memory", "aliases": [], "arguments": "query"},
    {"name": "/skills", "description": "List available skills", "aliases": [], "arguments": None},
    {"name": "/handoff", "description": "List handoff notes", "aliases": [], "arguments": None},
    {"name": "/init", "description": "Scan the project and generate AGENTS.md", "aliases": [], "arguments": "path"},
]

# Legacy flat list kept for slash-command fallback only; list_models uses
# PROVIDER_CATALOG and filters strictly by the configured provider.
_BUILTIN_MODELS: list[dict] = []
for _prov, _meta in PROVIDER_CATALOG.items():
    for _m in _meta.get("models") or []:
        _BUILTIN_MODELS.append(
            {
                "id": _m["id"],
                "name": _m.get("name", _m["id"]),
                "provider": _prov,
                "default": False,
            }
        )

_BUILTIN_AGENTS: list[dict] = [
    {"name": "default", "description": "Remedy — general-purpose agent", "build_mode": True},
    {"name": "remedy", "description": "Remedy — meta-orchestrator with skill routing", "build_mode": True},
    {"name": "explore", "description": "Codebase explorer for search and analysis", "build_mode": False},
    {"name": "general", "description": "General-purpose agent for complex tasks", "build_mode": True},
]


async def handle_slash_command(
    command: str, session_id: str | None, memory
) -> dict:
    """Execute a slash command and return a result."""
    stripped = command.strip().lower()

    if stripped in ("/help", "/h"):
        cmds = "\n".join(f"  {c['name']} — {c['description']}" for c in _BUILTIN_COMMANDS)
        return {"text": f"Available commands:\n{cmds}"}

    if stripped in ("/new", "/n"):
        return {"text": "Session marked for creation.", "action": "new_session"}

    if stripped in ("/sessions", "/s"):
        if memory is None:
            return {"text": "Memory store not available."}
        sessions = await memory.list_chat_sessions(limit=10)
        if not sessions:
            return {"text": "No sessions found."}
        lines = []
        for s in sessions:
            sid = getattr(s, "id", None) or (s.get("id") if isinstance(s, dict) else "")
            title = getattr(s, "title", None) or (s.get("title") if isinstance(s, dict) else "Untitled")
            count = getattr(s, "message_count", None)
            if count is None and isinstance(s, dict):
                count = s.get("message_count", 0)
            lines.append(f"  {title} — {count or 0} msg — {str(sid)[:8]}")
        return {"text": "Recent sessions:\n" + "\n".join(lines)}

    if stripped in ("/models", "/m"):
        return {
            "text": (
                "Model list is filtered by your configured provider. "
                "Use the model picker in the status bar, or GET /api/models."
            ),
            "action": "list_models",
        }

    if stripped == "/thinking":
        return {"text": "Thinking visibility toggled."}

    if stripped.startswith("/memory "):
        query = command[len("/memory "):].strip()
        if not query or memory is None:
            return {"text": "Usage: /memory <query>"}
        entries = await memory.search(query, limit=5)
        if not entries:
            return {"text": "No memory entries found."}
        lines = []
        for e in entries:
            lines.append(f"  **{e.title}** — {e.content[:120]}")
        return {"text": "Memory results:\n" + "\n".join(lines)}

    if stripped in ("/memory", "/mem"):
        return {"text": "Usage: /memory <query>"}

    if stripped in ("/skills", "/sk") or stripped.startswith("/skills "):
        # Prefer live runtime registry; fall back to empty guidance.
        # Note: handle_slash_command doesn't receive runtime — use memory path via app state.
        # Callers that pass runtime through a side channel aren't available here, so we
        # re-read from a module-level hook set by create_app when possible.
        registry = getattr(handle_slash_command, "_skills_registry", None)
        count = int(getattr(registry, "count", 0) or 0) if registry is not None else 0
        if registry is not None and count > 0:
            lines = registry.summary_lines()
            tools_hint = (
                "\n\n**Built-in tools** (always available): "
                "`file_read`, `file_write`, `list_dir`, `bash_exec`.\n"
                "Skills are procedure packs the agent follows; tools are executable actions."
            )
            return {
                "text": f"**{count} skills loaded:**\n" + "\n".join(lines) + tools_hint
            }
        return {
            "text": (
                "No skills loaded yet. Default skills ship with Remedy — restart the server "
                "to discover bundled skills, or drop SKILL.md packages into `~/.remedy/skills/`.\n\n"
                "**Built-in tools:** `file_read`, `file_write`, `list_dir`, `bash_exec`."
            )
        }

    if stripped in ("/handoff", "/ho"):
        if memory is None:
            return {"text": "Memory store not available."}
        handoffs = await memory.list_handoffs(limit=5)
        if not handoffs:
            return {"text": "No handoff notes found."}
        lines = []
        for h in handoffs:
            lines.append(f"  **{h.title}** — {h.content[:100]}")
        return {"text": "Handoffs:\n" + "\n".join(lines)}

    if stripped == "/compact":
        return {"text": "Session compaction requested (stub)."}

    if stripped.startswith("/init"):
        parts = stripped.split(" ", 1)
        path = parts[1] if len(parts) > 1 else "."
        return {"text": f"Project scan requested for: {path}\nUse the API endpoint POST /api/projects/scan?path=... for detailed results.", "action": "init_scan"}

    return {"text": f"Unknown command: {command}\nType /help for available commands."}


def _default_config_path() -> Path:
    """Canonical user config path (matches desktop sidecar --home)."""
    return Path.home() / ".remedy" / "config.toml"


def _find_config_path() -> Path | None:
    # Prefer the home config so desktop and CLI always share one persistent file.
    primary = _default_config_path()
    if primary.exists():
        return primary
    for p in CONFIG_PATHS:
        expanded = p.expanduser().resolve()
        if expanded.exists():
            return expanded
    return None


def load_config() -> dict[str, Any]:
    path = _find_config_path()
    if path is None:
        return {}
    return _load_toml_config(path)


def _apply_llm_to_runtime(
    runtime: Any,
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str | None = None,
    persona: str | None = None,
    name: str | None = None,
    project_path: str | None = None,
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
        if project_path is not None:
            kwargs["project_path"] = project_path
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


# Cache config disk reads across chat messages; invalidate on mtime/size change.
_config_cache: dict[str, Any] = {"path": None, "mtime": None, "size": None, "data": None}


def _load_config_cached() -> dict[str, Any]:
    """load_config() with a cheap mtime/size cache to avoid re-reading every message."""
    from pathlib import Path

    candidates = [
        Path("~/.remedy/config.toml").expanduser(),
        Path("~/.remedy/config.yaml").expanduser(),
        Path("remedy.toml"),
        Path("remedy.yaml"),
    ]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return load_config()
    try:
        st = path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        return load_config()
    if (
        _config_cache["path"] == str(path)
        and _config_cache["mtime"] == mtime
        and _config_cache["size"] == size
        and isinstance(_config_cache["data"], dict)
    ):
        return _config_cache["data"]
    # Prefer explicit path when known; load_config() also scans defaults.
    data = _load_toml_config(path) if path else load_config()
    _config_cache.update({"path": str(path), "mtime": mtime, "size": size, "data": data})
    return data


def _sync_runtime_llm_from_config(
    runtime: Any,
    *,
    model_override: str | None = None,
) -> str:
    """Reload provider/model/url/key from disk into the live runtime.

    Returns the effective API key (may be empty). Re-reads config when the file
    changes (or first call) so settings saved after server start apply without
    a restart, without paying for a full disk parse on every message.
    """
    if runtime is None:
        return ""
    cfg = _load_config_cached()
    provider = str(
        cfg.get("llm_provider")
        or getattr(runtime, "_llm_provider", None)
        or os.environ.get("REMEDY_LLM_PROVIDER")
        or "openai"
    )
    model = str(
        model_override
        or cfg.get("llm_model")
        or getattr(runtime, "_llm_model", None)
        or os.environ.get("REMEDY_LLM_MODEL")
        or ""
    )
    base_url = str(
        cfg.get("llm_base_url")
        or getattr(runtime, "_llm_base_url", None)
        or os.environ.get("REMEDY_LLM_BASE_URL")
        or ""
    )
    api_key = str(
        cfg.get("llm_api_key")
        or os.environ.get("REMEDY_LLM_API_KEY")
        or getattr(runtime, "_llm_api_key", "")
        or ""
    )
    # Local providers: ensure a dummy key so stream path does not fall back.
    if not api_key and (
        provider.lower() == "ollama" or (base_url and _is_local_url(base_url))
    ):
        api_key = "local"

    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key if api_key else None,
    )
    return str(getattr(runtime, "_llm_api_key", "") or api_key or "")


def _write_config(path: Path, cfg: dict[str, Any]) -> None:
    lines = []
    lines.append("# Remedy AI Configuration\n\n")
    for key, value in cfg.items():
        if isinstance(value, dict):
            lines.append(f"[{key}]\n")
            for k, v in value.items():
                lines.append(f"{k} = {_serialize_toml(v)}\n")
            lines.append("\n")
        else:
            lines.append(f"{key} = {_serialize_toml(value)}\n")
    content = "".join(lines)
    path.write_text(content, encoding="utf-8")


def _serialize_toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        items = ", ".join(_serialize_toml(v) for v in value)
        return f"[{items}]"
    return json.dumps(str(value))


