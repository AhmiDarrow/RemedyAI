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
    PROVIDER_CATALOG,
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
    {"name": "/forget", "description": "Remove a remembered fact: /forget <text>", "aliases": [], "arguments": "text"},
    {"name": "/pin", "description": "Pin a fact so it always injects: /pin <text>", "aliases": [], "arguments": "text"},
    {"name": "/whoami", "description": "Show what Remedy knows about you", "aliases": [], "arguments": None},
    {"name": "/goals", "description": "List open goals", "aliases": [], "arguments": None},
    {"name": "/goal", "description": "Add a goal: /goal <title>", "aliases": [], "arguments": "title"},
    {"name": "/plans", "description": "List structured task plans", "aliases": [], "arguments": None},
    {
        "name": "/plan",
        "description": "Show latest plan, or /plan approve|new <title>",
        "aliases": [],
        "arguments": "approve|new <title>",
    },
    {"name": "/approve", "description": "Approve a pending high-impact action", "aliases": [], "arguments": "id"},
    {"name": "/deny", "description": "Deny a pending high-impact action", "aliases": [], "arguments": "id"},
    {"name": "/import", "description": "Import a folder of notes into memory", "aliases": [], "arguments": "path"},
    {"name": "/export", "description": "Export this session as a .txt file (desktop)", "aliases": [], "arguments": None},
    {
        "name": "/import-session",
        "description": "Import a session from .txt/.md (path or desktop file picker)",
        "aliases": ["/session-import"],
        "arguments": "path",
    },
    {"name": "/skills", "description": "List available skills", "aliases": [], "arguments": None},
    {
        "name": "/helper",
        "description": "Offline help tips, or /helper error <text>",
        "aliases": ["/tip"],
        "arguments": "topic | error <text>",
    },
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
            "  Ctrl+/ or F1 — Full Help wiki (owner's manual)\n"
            "  Escape — Close panels, Help, and palette\n"
        )
        tips = (
            "\n**Tips**\n"
            "  · **F1** opens the offline Help wiki (searchable owner's manual).\n"
            "  · Connect a provider in Settings to chat with models.\n"
            "  · Plan mode explores without changing files; Build mode can edit.\n"
            "  · Type @ to reference project files.\n"
            "  · Your data stays in ~/.remedy on this machine.\n"
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

    if stripped.startswith("/helper") or stripped.startswith("/tip"):
        try:
            from remedy.nanoswarm import get_swarm

            parts = raw.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""
            low = arg.lower()
            if low.startswith("error ") or low.startswith("err "):
                err = arg.split(maxsplit=1)[1] if " " in arg else arg
                out = get_swarm().helper.explain_error(err)
            else:
                out = get_swarm().helper.draft_help(arg)
            return {"text": out.get("markdown") or out.get("error") or "No help available."}
        except Exception as e:
            return {"text": f"Helper error: {e}"}

    if stripped in ("/harness", "/brief", "/nanoswarm", "/swarm"):
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
            quality_line = ""
            try:
                from remedy.core.session_quality import get_session_quality

                sid = getattr(agent, "_session_id", None) if agent is not None else None
                q = get_session_quality(str(sid or "")).snapshot()
                last = q.get("last_compress") or {}
                quality_line = (
                    f"\n\n**Session quality** · turns {q.get('turns')} · "
                    f"tokens in/out {q.get('tokens_in')}/{q.get('tokens_out')} · "
                    f"saved by compress ~{q.get('tokens_saved_by_compress')} · "
                    f"stuck rate {q.get('stuck_rate')} · "
                    f"re-explain rate {q.get('re_explain_rate')}"
                )
                if last:
                    quality_line += (
                        f"\nLast compress: {last.get('tokens_before')}→{last.get('tokens_after')} "
                        f"(quality {last.get('quality_score')}, "
                        f"paths kept {last.get('paths_kept')}, lost {last.get('paths_lost')})"
                    )
                if q.get("avg_compress_quality") is not None:
                    quality_line += f"\nAvg compress quality: {q.get('avg_compress_quality')}"
            except Exception:
                pass
            return {
                "text": (
                    f"**Memory** · compress passes: {brief.compress_count}\n\n"
                    f"{block}"
                    f"{quality_line}"
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
            from remedy.memory.partner_memory import looks_like_secret, upsert_profile_fact
            from remedy.models import MemoryEntry, MemoryEntryType

            if looks_like_secret(text):
                return {
                    "text": (
                        "That looks like a secret (API key/password). "
                        "I won’t store it in Partner Memory — use a secret store or env var."
                    )
                }

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
                upsert_profile_fact(
                    profile,
                    text,
                    category="general",
                    confidence=0.95,
                    source="explicit",
                    force=True,
                )
                await memory.save_user_profile(profile)
            return {"text": f"Remembered: {text[:300]}"}
        except Exception as e:
            return {"text": f"Could not save: {e}"}

    if stripped.startswith("/forget"):
        text = raw[len("/forget") :].strip()
        if not text:
            return {"text": "Usage: /forget <text matching a fact to remove>"}
        if memory is None:
            return {"text": "Memory store not available."}
        try:
            from remedy.memory.partner_memory import forget_facts

            profile = await memory.get_or_create_profile()
            removed = forget_facts(profile, text)
            await memory.save_user_profile(profile)
            if not removed:
                return {
                    "text": (
                        f"No matching facts for “{text[:120]}”. "
                        "Try `/whoami` to see what I know."
                    )
                }
            lines = [f"Forgot {len(removed)} fact(s):"]
            for f in removed[:8]:
                lines.append(f"- {f.fact}")
            return {"text": "\n".join(lines)}
        except Exception as e:
            return {"text": f"Could not forget: {e}"}

    if stripped.startswith("/pin"):
        text = raw[len("/pin") :].strip()
        if not text:
            return {"text": "Usage: /pin <text matching a fact to keep always ready>"}
        if memory is None:
            return {"text": "Memory store not available."}
        try:
            from remedy.memory.partner_memory import pin_facts, upsert_profile_fact

            profile = await memory.get_or_create_profile()
            touched = pin_facts(profile, text, pinned=True)
            if not touched:
                # Create + pin if no match
                uf, _ = upsert_profile_fact(
                    profile,
                    text,
                    category="general",
                    confidence=0.95,
                    source="explicit",
                    force=True,
                    pinned=True,
                )
                if uf is not None:
                    touched = [uf]
            await memory.save_user_profile(profile)
            if not touched:
                return {"text": f"Could not pin “{text[:120]}”."}
            return {
                "text": "Pinned:\n"
                + "\n".join(f"- {f.fact}" for f in touched[:8])
            }
        except Exception as e:
            return {"text": f"Could not pin: {e}"}

    if stripped in ("/whoami", "/who-am-i"):
        if memory is None:
            return {"text": "Memory store not available."}
        try:
            from remedy.memory.partner_memory import format_whoami

            profile = await memory.get_or_create_profile()
            return {"text": format_whoami(profile)}
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

    if stripped in ("/plans", "/plan") or stripped.startswith("/plan "):
        from pathlib import Path

        from remedy.core.plan_store import PlanStore

        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            try:
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
            except Exception:
                home = None
        store = PlanStore(home or Path.home() / ".remedy")
        rest = raw[len("/plan") :].strip() if stripped.startswith("/plan") else ""
        if stripped == "/plans" or rest.lower() in ("", "list"):
            plans = store.list_plans(limit=20)
            if not plans:
                return {
                    "text": "No structured plans yet. Use **Plan** mode (Ctrl+B) and ask "
                    "Remedy to save a plan, or `/plan new <title>`."
                }
            lines = [
                f"- [{p.status}] **{p.title}** (`{p.id}`, {len(p.steps)} steps)"
                for p in plans
            ]
            return {"text": "**Plans**\n" + "\n".join(lines)}
        if rest.lower().startswith("approve"):
            pid = rest[7:].strip()
            plan = store.get(pid) if pid else store.list_plans(limit=1)
            if isinstance(plan, list):
                plan = plan[0] if plan else None
            if plan is None:
                return {"text": "No plan to approve. Save one first."}
            approved = store.set_status(plan.id, "approved")
            if approved is None:
                return {"text": "Failed to approve plan."}
            return {
                "text": f"Plan **approved**: {approved.title} (`{approved.id}`)\n\n"
                "Switch to **Build** mode to execute."
            }
        if rest.lower().startswith("new "):
            title = rest[4:].strip()
            if not title:
                return {"text": "Usage: /plan new <title>"}
            plan = store.create(title, goal=title, steps=[], status="draft")
            return {
                "text": f"Draft plan created: **{plan.title}** (`{plan.id}`). "
                "In Plan mode, ask Remedy to fill steps with `plan_save`."
            }
        # Show latest or by id
        plan = store.get(rest) if rest and not rest.lower().startswith("new") else None
        if plan is None:
            plans = store.list_plans(limit=1)
            plan = plans[0] if plans else None
        if plan is None:
            return {"text": "No plans yet. `/plan new <title>` or use Plan mode."}
        return {"text": plan.summary_markdown()}

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

    if stripped in ("/export", "/export-session"):
        # Desktop intercepts this for file download; CLI/API returns guidance.
        return {
            "text": (
                "Export this session as a plain-text `.txt` file from the desktop:\n"
                "• Right-click the session tab → export\n"
                "• Command palette → **Export Session**\n"
                "• Sidebar → **Export**\n"
                "• API: `GET /api/sessions/{id}/export?format=txt`"
            ),
            "action": "export_session",
        }

    if stripped.startswith("/import-session") or stripped.startswith("/session-import"):
        # Desktop uses the file picker when no path is given.
        path = ""
        for prefix in ("/import-session", "/session-import"):
            if raw.lower().startswith(prefix):
                path = raw[len(prefix) :].strip().strip('"').strip("'")
                break
        if not path:
            return {
                "text": (
                    "Import a session from a `.txt` or `.md` export:\n"
                    "• Desktop: Command palette → **Import Session** (file picker)\n"
                    "• Slash: `/import-session <path-to-file.txt>`\n"
                    "• API: `POST /api/sessions/import` with `{ \"text\": \"...\" }`"
                ),
                "action": "import_session",
            }
        if memory is None:
            return {"text": "Memory store not available."}
        from remedy.core.workspace import (
            allowed_roots_for_scope,
            default_project_from_config,
            resolve_under_roots,
        )
        from remedy.memory.session_io import parse_session_text
        from remedy.models import ChatMessage, ChatMessageRole
        from remedy.models import ChatSession as CS

        cfg = load_config()
        scope = str(cfg.get("access_scope") or "home")
        project = default_project_from_config(cfg)
        roots = allowed_roots_for_scope(scope, project)
        try:
            fpath = resolve_under_roots(path, roots, access_scope=scope)
        except Exception as exc:
            return {"text": f"Path not allowed: {exc}"}
        if not fpath.is_file():
            return {"text": f"File not found: {fpath}"}
        try:
            text = fpath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        try:
            parsed = parse_session_text(text)
        except ValueError as exc:
            return {"text": f"Import failed: {exc}"}
        title = (parsed.title or "Imported Session")[:200]
        session = CS(title=title, model=parsed.model, agent=parsed.agent)
        saved = await memory.create_chat_session(session)
        role_map = {
            "user": ChatMessageRole.USER,
            "assistant": ChatMessageRole.ASSISTANT,
            "system": ChatMessageRole.SYSTEM,
            "tool": ChatMessageRole.TOOL,
        }
        n = 0
        for pm in parsed.messages:
            role = role_map.get((pm.role or "user").lower(), ChatMessageRole.USER)
            if role == ChatMessageRole.SYSTEM and not (pm.content or "").strip():
                continue
            await memory.add_chat_message(
                ChatMessage(
                    session_id=saved.id,
                    role=role,
                    content=pm.content or "",
                    model=pm.model or parsed.model,
                    agent=pm.agent or parsed.agent,
                )
            )
            n += 1
        return {
            "text": (
                f"Imported session **{title}** (`{saved.id[:8]}…`) "
                f"with **{n}** messages."
            ),
            "action": "import_session_done",
            "session_id": saved.id,
        }

    # Knowledge-pack import (folder of notes) — must not match /import-session
    if stripped == "/import" or stripped.startswith("/import "):
        path = raw[len("/import") :].strip().strip('"').strip("'")
        if not path:
            return {
                "text": "Usage: /import <folder path>\n"
                "Imports .md/.txt notes into durable memory (knowledge pack).\n"
                "To import a chat session, use `/import-session <file.txt>`."
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
) -> str:
    """Reload provider/model/url/key from disk into the live runtime.

    Returns the effective API key (may be empty). Re-reads config when the file
    changes (or first call) so settings saved after server start apply without
    a restart, without paying for a full disk parse on every message.

    *provider_override* / *model_override*: per-session picks (status-bar switch).
    Without these, a session on Grok while global config is still DeepSeek would
    send ``model=grok-4.5`` to the DeepSeek base URL every turn.
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
        provider.lower() == "ollama" or (base_url and _is_local_url(base_url))
    ):
        api_key = "local"

    # Partner trust: keep APPROVALS + runtime aligned with config on every turn.
    # Without this, thumbs-up (auto) in Settings/UI can show while tools still
    # emit APPROVAL_REQUIRED because the process started on default ask.
    am = str(cfg.get("approval_mode") or "ask").strip().lower()
    if am not in ("ask", "auto"):
        am = "ask"
    scope = cfg.get("access_scope")
    _apply_llm_to_runtime(
        runtime,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key if api_key else None,
        project_path=cfg.get("project_path"),
        access_scope=str(scope) if scope is not None else None,
        harness_mode=cfg.get("harness_mode"),
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
    path.write_text(content, encoding="utf-8")
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


