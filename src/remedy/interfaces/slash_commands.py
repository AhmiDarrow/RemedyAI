"""Built-in slash commands for chat (/help, /reset, …).

Extracted from ``api_support.py`` so config/SSE helpers stay lean and command
tables can grow without bloating the shared API support module.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from remedy.interfaces.config import PROVIDER_CATALOG

logger = logging.getLogger(__name__)

# -- built-in slash commands -------------------------------------------------

_BUILTIN_COMMANDS: list[dict] = [
    {"name": "/help", "description": "Show available commands", "aliases": [], "arguments": None},
    {"name": "/new", "description": "Create a new chat session", "aliases": [], "arguments": None},
    {
        "name": "/reset",
        "description": (
            "Full reset of this session (history, plans, brief, attachments) "
            "— stay in the same tab; like a new chat without switching"
        ),
        "aliases": ["/clear"],
        "arguments": None,
    },
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
    {
        "name": "/stretch",
        "description": "Map this PC (hardware, tools, rooms) — first-home census",
        "aliases": ["/home"],
        "arguments": None,
    },
    {"name": "/goals", "description": "List open goals", "aliases": [], "arguments": None},
    {"name": "/goal", "description": "Add a goal: /goal <title>", "aliases": [], "arguments": "title"},
    {"name": "/plans", "description": "List structured task plans", "aliases": [], "arguments": None},
    {
        "name": "/plan",
        "description": "Show latest plan, or /plan approve|cancel|new <title>",
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
    {"name": "/security-status", "description": "Show partner control settings (approval mode, web tools, timeouts)", "aliases": ["/security", "/secstatus"], "arguments": None},
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

    if stripped in ("/reset", "/clear"):
        # Full in-place clean slate — same session id, no jump to another tab.
        if not session_id:
            return {
                "text": "No active session to reset. Open a chat first, or use `/new`.",
            }
        if memory is None:
            return {"text": "Memory store not available; cannot reset session."}
        try:
            from remedy.core.session_reset import full_reset_session

            stats = await full_reset_session(
                str(session_id), memory, runtime=runtime
            )
        except Exception as exc:
            return {"text": f"Could not reset session: {exc}"}
        if not stats.get("ok", True):
            return {
                "text": f"Could not reset session: {stats.get('error') or 'unknown error'}",
            }
        n = int(stats.get("messages") or 0)
        bits = [f"{n} message{'s' if n != 1 else ''}"]
        if stats.get("plans"):
            bits.append(f"{stats['plans']} plan(s)")
        if stats.get("checkpoints"):
            bits.append(f"{stats['checkpoints']} checkpoint(s)")
        if stats.get("attachments_purged"):
            bits.append("attachments")
        if stats.get("memory_entries"):
            bits.append(f"{stats['memory_entries']} session note(s)")
        detail = ", ".join(bits)
        return {
            "text": (
                f"Session fully reset ({detail}). "
                "Same session tab — history, plans, brief, goals buffer, and "
                "attachments cleared. Durable memory (/remember) kept. "
                "Send a message to start as if new."
            ),
            "action": "reset_session",
            "cleared": n,
            "stats": stats,
        }

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
                meta = q.get("metabolism") or {}
                if meta:
                    quality_line += (
                        f"\n\n**Metabolism** · tier L{meta.get('last_tier', '?')} · "
                        f"EU {meta.get('evidence_units', 0)} · DU {meta.get('decision_units', 0)} · "
                        f"waste_tok {meta.get('waste_tokens', 0)} · "
                        f"spread×{meta.get('force_spread_count', 0)} · "
                        f"shadow {meta.get('shadow_catch_count', 0)} · "
                        f"verify {meta.get('verify_catch_count', 0)} · "
                        f"IR steps {meta.get('ir_step_count', 0)}"
                    )
                with contextlib.suppress(Exception):
                    from remedy.core.metabolism.turn import metabolism_public_snapshot

                    # Lean: last_actions are still present; skip recent/list thrash
                    msnap = metabolism_public_snapshot(str(sid or ""), lean=True)
                    gov = (msnap.get("governor") or {}).get("last_actions") or []
                    if gov:
                        quality_line += f"\nGovernor last: {', '.join(gov[:8])}"
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
            # Time Crystal life horizon (explicit pins)
            with contextlib.suppress(Exception):
                from remedy.core.metabolism.time_crystal import get_time_crystal

                sid_p = ""
                if runtime is not None:
                    sid_p = str(getattr(runtime, "_session_id", "") or "")
                tc = get_time_crystal(sid_p or "_default")
                for f in touched[:8]:
                    fact = getattr(f, "fact", None) or str(f)
                    tc.admit(str(fact)[:400], horizon="life", source="pin")
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
            text = format_whoami(profile)
            home = None
            if runtime is not None:
                home = getattr(getattr(runtime, "config", None), "home_dir", None)
            with suppress(Exception):
                from remedy.memory.soul.field import load_soul_field

                sf = load_soul_field(home)
                if sf.future_dreams:
                    text += "\n\n**Dreams of the future** (how I will help):\n"
                    text += "\n".join(f"- {d}" for d in sf.future_dreams[:5])
            with suppress(Exception):
                from remedy.execution.host.stretch import format_home_whoami

                home_blk = format_home_whoami(home=home)
                if home_blk:
                    text += "\n\n" + home_blk
            return {"text": text}
        except Exception as e:
            return {"text": f"Profile error: {e}"}

    if stripped in ("/stretch", "/home"):
        home = None
        if runtime is not None:
            home = getattr(getattr(runtime, "config", None), "home_dir", None)
        try:
            from remedy.execution.host.stretch import format_home_whoami, stretch_home

            census = stretch_home(home, force=True)
            return {"text": format_home_whoami(census, home=home)}
        except Exception as e:
            return {"text": f"Could not stretch this home: {e}"}

    if stripped in ("/goals", "/goal"):
        if stripped == "/goal" or stripped.startswith("/goal "):
            title = raw[len("/goal") :].strip()
            if not title:
                return {"text": "Usage: /goal <title>"}
            if runtime is not None and hasattr(runtime, "create_task"):
                task = runtime.create_task(title, tags=["goal"])
                drive_note = ""
                with suppress(Exception):
                    from remedy.memory.life_drive import invent_next, take_step
                    from remedy.memory.life_goals import LifeGoalStore

                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    store = LifeGoalStore(home)
                    g = store.add(title, source="slash_goal")
                    if g and not g.next_action:
                        store.set_next(g.title, invent_next(g))
                    drove = take_step(home, force=True)
                    if isinstance(drove, dict) and drove.get("markdown"):
                        drive_note = "\n" + str(drove.get("markdown"))
                with suppress(Exception):
                    if memory is not None:
                        from remedy.memory.partner_memory import upsert_profile_fact

                        profile = await memory.get_or_create_profile()
                        upsert_profile_fact(
                            profile,
                            f"Goal: {title[:240]}",
                            category="goal",
                            confidence=0.9,
                            source="slash_goal",
                        )
                        await memory.save_user_profile(profile)
                dream_note = ""
                with suppress(Exception):
                    from remedy.memory.soul.partner_dream import refresh_partner_dreams

                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    out = refresh_partner_dreams(
                        home,
                        memory=memory,
                        extra_goals=[title],
                    )
                    dreams = out.get("dreams") or []
                    if dreams:
                        dream_note = "\nDream: " + str(dreams[0])
                return {
                    "text": (
                        f"Goal added: **{task.title}** (`{task.id}`)"
                        f"{dream_note}{drive_note}"
                    )
                }
            return {"text": "Runtime not available to store goals."}
        if runtime is not None and hasattr(runtime, "list_tasks"):
            with suppress(Exception):
                from remedy.memory.life_drive import drive_digest
                from remedy.memory.life_goals import LifeGoalStore, format_goals_markdown

                home = getattr(getattr(runtime, "config", None), "home_dir", None)
                life = LifeGoalStore(home).list()
                if life:
                    text = format_goals_markdown(life)
                    digest = str(drive_digest(home).get("markdown") or "")
                    if digest and "No new Life steps" not in digest:
                        text = text + "\n\n" + digest
                    return {"text": text}
            tasks = runtime.list_tasks()
            goals = [t for t in tasks if "goal" in (t.tags or [])] or list(tasks)
            if not goals:
                return {"text": "No life goals yet. `/goal <title>` to add one."}
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
        if rest.lower().startswith("cancel"):
            pid = rest[6:].strip()
            plan = store.get(pid) if pid else store.latest_for_session(None, actionable_only=True)
            if plan is None and not pid:
                # Prefer newest non-terminal overall when no id
                plans = store.list_plans(limit=20)
                plan = next(
                    (p for p in plans if p.status in ("draft", "approved", "active")),
                    None,
                )
            if plan is None:
                return {"text": "No plan to cancel. Save one first, or pass `/plan cancel <id>`."}
            cancelled = store.set_status(plan.id, "cancelled")
            if cancelled is None:
                return {"text": "Failed to cancel plan."}
            return {
                "text": f"Plan **cancelled**: {cancelled.title} (`{cancelled.id}`).\n\n"
                "It will no longer stick in the Plan banner. Start a new plan in **Plan** mode when ready."
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

    if stripped in ("/security-status", "/security", "/secstatus"):
        """Report partner control / security settings from config and live runtime."""
        cfg = load_config()
        lines = ["## 🔐 Security Status\n"]

        # -- Approval mode
        from remedy.core.approvals import normalize_approval_mode

        am = normalize_approval_mode(str(cfg.get("approval_mode") or "ask"))
        # Check live runtime override if available
        if runtime is not None and hasattr(runtime, "_approval_mode"):
            am = normalize_approval_mode(str(runtime._approval_mode))
        if am == "full":
            emoji, blurb = "⚠️", "Full (warn) — write jail off except auth"
        elif am == "auto":
            emoji, blurb = "✅", "Auto (in-project) — build/write without prompts"
        else:
            emoji, blurb = "⛔", "Ask before high-impact actions"
        lines.append(f"{emoji} **Approval Mode:** `{am}` — {blurb}")

        # -- Web tools
        web = bool(cfg.get("web_tools_enabled", False))
        emoji = "🌐" if web else "🚫"
        lines.append(f"{emoji} **Web Tools:** `{'enabled' if web else 'disabled'}`")

        # -- Access scope
        scope = str(cfg.get("access_scope") or "home")
        lines.append(f"📁 **Access Scope:** `{scope}`")

        # -- HTTP bootstrap
        http = cfg.get("http_bootstrap")
        if http is not None:
            hb = "enabled" if bool(http) else "disabled (IPC-only)"
            lines.append(f"🔌 **HTTP Bootstrap:** `{hb}`")

        # -- Auto-approve threshold
        aat = cfg.get("auto_approve_threshold")
        if aat is not None:
            lines.append(f"⚖️ **Auto-Approve Threshold:** `{aat}`")

        # -- Thinking level
        tl = str(cfg.get("thinking_level") or "high").strip().lower()
        lines.append(f"🧠 **Thinking Level:** `{tl}`")

        # -- Tool process visibility
        tp = cfg.get("tool_process")
        if tp is not None:
            lines.append(f"🔧 **Tool Process:** `{tp}`")

        # -- Harness mode
        hm = str(cfg.get("harness_mode") or "auto").strip().lower()
        lines.append(f"⚙️ **Harness Mode:** `{hm}`")

        # -- Execution timeout (if available in config or runtime)
        timeout = cfg.get("execution_timeout") or cfg.get("tool_timeout")
        if timeout is not None:
            lines.append(f"⏱️ **Execution Timeout:** `{timeout}s`")

        # -- Vision
        ve = bool(cfg.get("vision_enabled", True))
        lines.append(f"👁️ **Vision Decoder:** `{'enabled' if ve else 'disabled'}`")

        # -- Skills budget
        sb = cfg.get("skills_active_budget")
        if sb is not None:
            lines.append(f"📦 **Skills Active Budget:** `{sb}`")

        lines.append("\nRun `/help` for all commands.")
        return {"text": "\n".join(lines)}

    return {"text": f"Unknown command: {command}\nType /help for available commands."}

