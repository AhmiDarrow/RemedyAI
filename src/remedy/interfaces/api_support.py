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
    {"name": "/compact", "description": "Memory Harness: compress session into Session Brief", "aliases": [], "arguments": "focus"},
    {"name": "/harness", "description": "Show Memory Harness Session Brief / stats", "aliases": [], "arguments": None},
    {"name": "/models", "description": "List available models", "aliases": [], "arguments": None},
    {"name": "/thinking", "description": "Toggle thinking visibility", "aliases": [], "arguments": None},
    {"name": "/memory", "description": "Search memory", "aliases": [], "arguments": "query"},
    {"name": "/remember", "description": "Save a durable fact to memory", "aliases": [], "arguments": "text"},
    {"name": "/whoami", "description": "Show what Remedy knows about you", "aliases": [], "arguments": None},
    {"name": "/goals", "description": "List open goals", "aliases": [], "arguments": None},
    {"name": "/goal", "description": "Add a goal: /goal <title>", "aliases": [], "arguments": "title"},
    {"name": "/approve", "description": "Approve a pending high-impact action", "aliases": [], "arguments": "id"},
    {"name": "/deny", "description": "Deny a pending high-impact action", "aliases": [], "arguments": "id"},
    {"name": "/import", "description": "Import a folder of notes into memory", "aliases": [], "arguments": "path"},
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
    command: str,
    session_id: str | None,
    memory,
    runtime: Any = None,
) -> dict:
    """Execute a slash command and return a result."""
    from contextlib import suppress

    stripped = command.strip().lower()
    # Preserve original casing for /remember text
    raw = command.strip()

    if stripped in ("/help", "/h"):
        cmds = "\n".join(f"  {c['name']} — {c['description']}" for c in _BUILTIN_COMMANDS)
        keys = (
            "**Keyboard shortcuts**\n"
            "  Enter — Send message (composer)\n"
            "  Shift+Enter — New line (composer)\n"
            "  Ctrl+N — New chat session\n"
            "  Ctrl+P / Ctrl+K — Command palette\n"
            "  Ctrl+B — Toggle plan mode\n"
            "  Ctrl+, — Settings\n"
            "  Ctrl+/ or F1 — This help / shortcuts\n"
            "  Escape — Close panels and palette\n"
        )
        tips = (
            "\n**Tips**\n"
            "  · Connect a provider in Settings to chat with models.\n"
            "  · Plan mode explores without changing files; Build mode can edit.\n"
            "  · Type @ to reference project files.\n"
            "  · Your data stays in your Remedy folder on this machine.\n"
        )
        return {
            "text": (
                f"**Slash commands**\n{cmds}\n\n{keys}{tips}"
            )
        }

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

    if stripped == "/compact" or stripped.startswith("/compact "):
        focus = raw[len("/compact") :].strip()
        agent = runtime if runtime is not None else None
        # BasicRuntime is often the agent itself
        if agent is not None and hasattr(agent, "tool_registry"):
            try:
                result = await agent.tool_registry.execute(
                    "compress_context", focus=focus
                )
                return {"text": str(result)}
            except Exception as e:
                return {"text": f"Memory Harness compact failed: {e}"}
        return {
            "text": (
                "Memory Harness compact: agent runtime not available. "
                "Ask Remedy to call compress_context in chat."
                + (f" Focus: {focus}" if focus else "")
            )
        }

    if stripped in ("/harness", "/brief"):
        agent = runtime
        brief = getattr(agent, "_session_brief", None) if agent is not None else None
        if brief is None:
            return {
                "text": (
                    "Memory Harness: no Session Brief yet. "
                    "Use /compact after some work, or ask Remedy to compress_context."
                )
            }
        try:
            from remedy.memory.harness.brief import brief_to_context_block

            block = brief_to_context_block(brief) or "(empty brief)"
            return {
                "text": (
                    f"**Memory Harness** · compress passes: {brief.compress_count}\n\n"
                    f"{block}"
                )
            }
        except Exception as e:
            return {"text": f"Harness status error: {e}"}

    if stripped.startswith("/remember"):
        text = raw[len("/remember") :].strip()
        if not text:
            return {"text": "Usage: /remember <fact to store>"}
        if memory is None:
            return {"text": "Memory store not available."}
        try:
            from remedy.models import MemoryEntry, MemoryEntryType

            await memory.upsert(
                MemoryEntry(
                    title="Remembered",
                    content=text,
                    entry_type=MemoryEntryType.NOTE,
                    importance=0.8,
                )
            )
            with suppress(Exception):
                profile = await memory.get_or_create_profile()
                profile.add_fact(text, category="general", confidence=0.9)
                await memory.save_user_profile(profile)
            return {"text": f"Remembered: {text[:300]}"}
        except Exception as e:
            return {"text": f"Could not save: {e}"}

    if stripped in ("/whoami", "/who-am-i"):
        if memory is None:
            return {"text": "Memory store not available."}
        try:
            profile = await memory.get_or_create_profile()
            lines = ["**What I know about you**"]
            if profile.display_name:
                lines.append(f"- Name: {profile.display_name}")
            for key, trait in list(profile.traits.items())[:20]:
                lines.append(f"- {key}: {trait.value}")
            for fact in profile.facts[-15:]:
                lines.append(f"- ({fact.category}) {fact.fact}")
            if len(lines) == 1:
                lines.append(
                    "_Nothing stored yet. Use_ `/remember …` _or tell me preferences to save._"
                )
            return {"text": "\n".join(lines)}
        except Exception as e:
            return {"text": f"Profile error: {e}"}

    if stripped in ("/goals", "/goal"):
        if stripped == "/goal" or stripped.startswith("/goal "):
            title = raw[len("/goal") :].strip()
            if not title:
                return {"text": "Usage: /goal <title>"}
            if runtime is not None and hasattr(runtime, "create_task"):
                task = runtime.create_task(title, tags=["goal"])
                return {"text": f"Goal added: **{task.title}** (`{task.id}`)"}
            return {"text": "Runtime not available to store goals."}
        if runtime is not None and hasattr(runtime, "list_tasks"):
            tasks = runtime.list_tasks()
            goals = [t for t in tasks if "goal" in (t.tags or [])] or list(tasks)
            if not goals:
                return {"text": "No goals yet. `/goal <title>` to add one."}
            lines = [
                f"- [{t.status.value}] {t.title}"
                + (f" — {t.result_summary}" if t.result_summary else "")
                for t in goals[:30]
            ]
            return {"text": "**Goals**\n" + "\n".join(lines)}
        return {"text": "Runtime not available."}

    if stripped.startswith("/approve"):
        aid = raw[len("/approve") :].strip()
        if not aid:
            from remedy.core.approvals import APPROVALS

            pending = APPROVALS.list_pending()
            if not pending:
                return {"text": "No pending approvals."}
            lines = [
                f"- `{p.id}`: {p.reason} — `{p.command[:80]}`" for p in pending[:10]
            ]
            return {
                "text": "**Pending approvals**\n"
                + "\n".join(lines)
                + "\n\n`/approve <id>` to allow."
            }
        from remedy.core.approvals import APPROVALS

        item = APPROVALS.resolve(aid, approve=True, scope="session")
        if not item:
            return {"text": f"Unknown approval id: {aid}"}
        return {
            "text": (
                f"Approved `{item.id}`. Ask Remedy to **retry** the command:\n"
                f"`{item.command[:200]}`"
            )
        }

    if stripped.startswith("/deny"):
        aid = raw[len("/deny") :].strip()
        if not aid:
            return {"text": "Usage: /deny <approval-id>"}
        from remedy.core.approvals import APPROVALS

        item = APPROVALS.resolve(aid, approve=False)
        if not item:
            return {"text": f"Unknown approval id: {aid}"}
        return {"text": f"Denied `{item.id}` — command will not run."}

    if stripped.startswith("/import"):
        path = raw[len("/import") :].strip().strip('"').strip("'")
        if not path:
            return {
                "text": "Usage: /import <folder path>\n"
                "Imports .md/.txt notes into durable memory (knowledge pack)."
            }
        if memory is None:
            return {"text": "Memory store not available."}
        from remedy.memory.knowledge_pack import import_knowledge_pack

        result = await import_knowledge_pack(memory, path)
        if not result.get("ok"):
            return {"text": f"Import failed: {result.get('error')}"}
        return {
            "text": (
                f"Imported **{result['imported']}** notes from `{result['root']}` "
                f"(scanned {result['scanned']}, skipped {result['skipped']})."
                + (
                    f"\nErrors: {'; '.join(result['errors'][:5])}"
                    if result.get("errors")
                    else ""
                )
            )
        }

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
    """Persist non-secret settings only. API keys never land in config.toml."""
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
    for key, value in safe.items():
        if key in ("provider_keys", "llm_api_key"):
            continue  # hard block — never write secrets here
        if isinstance(value, dict):
            lines.append(f"[{key}]\n")
            for k, v in value.items():
                lines.append(f"{k} = {_serialize_toml(v)}\n")
            lines.append("\n")
        else:
            lines.append(f"{key} = {_serialize_toml(value)}\n")
    content = "".join(lines)
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
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


