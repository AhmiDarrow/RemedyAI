"""Skill progressive disclosure tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error

logger = logging.getLogger(__name__)


def register_skill_tools(runtime: Any) -> None:
    """Progressive disclosure: activate full SKILL.md / run skill scripts."""

    async def skill_activate(
        skill: str = "",
        include_references: bool = False,
        name: str = "",  # alias — avoid clashing with ToolRegistry.execute(name=)
    ) -> str:
        """Load full skill instructions into this turn (stage 2 disclosure)."""
        reg = getattr(runtime, "skills", None)
        if reg is None:
            return format_tool_error(
                "No skill registry",
                code="NO_SKILLS",
                tool_name="skill_activate",
                suggestion="Restart the server so bundled skills can load.",
            )
        nm = (skill or name or "").strip()
        if not nm:
            # Rank against empty → top trusted catalog
            ranked = reg.match_skills("", limit=10)
            names = ", ".join(s.manifest.name for s, _ in ranked) or "(none)"
            return f"Pass skill=. Available (top): {names}"
        # Quarantine: do not inject untrusted SKILL.md into the model (prompt injection).
        # Owner still has full power after Skills panel → Trust.
        sk_obj = reg.get(nm)
        if sk_obj is not None:
            meta_q = sk_obj.manifest.metadata or {}
            if meta_q.get("quarantine"):
                return format_tool_error(
                    f"Skill '{nm}' is quarantined (imported pack)",
                    code="QUARANTINE",
                    tool_name="skill_activate",
                    suggestion=(
                        "Open Skills panel → Trust this skill first. "
                        "Instruction load is blocked until then (scripts already blocked)."
                    ),
                )
            st = getattr(sk_obj.manifest.status, "value", str(sk_obj.manifest.status))
            if str(st).lower() in ("disabled", "archived", "deprecated"):
                return format_tool_error(
                    f"Skill '{nm}' is {st} (not active)",
                    code="SKILL_INACTIVE",
                    tool_name="skill_activate",
                    suggestion="Force-promote or Unarchive the skill in the Skills panel first.",
                )
        body = reg.skill_body(nm, include_references=bool(include_references))
        if body is None:
            # fuzzy match
            hits = reg.match_skills(nm, limit=5)
            hint = ", ".join(s.manifest.name for s, _ in hits) or "none"
            return format_tool_error(
                f"Skill not found: {nm}",
                code="SKILL_NOT_FOUND",
                tool_name="skill_activate",
                suggestion=f"Closest: {hint}",
            )
        reg.mark_activated(nm)
        related = reg.related_skills(nm)
        footer = ""
        if related:
            footer = (
                "\n\n_Related skills (composition): "
                + ", ".join(related)
                + " — activate if needed._"
            )
        with suppress(Exception):
            from remedy.core.metrics import default_registry

            default_registry.counter(
                "remedy_skill_activate_total", status="ok"
            ).inc()
        # Closed-loop re-use: activation ≠ execution success (separate counters)
        with suppress(Exception):
            loop = runtime._get_learning_loop()
            if loop is not None:
                loop.record_skill_activation(
                    nm,
                    session_id=str(getattr(runtime, "_session_id", "") or ""),
                )
        return body + footer

    async def skill_run(
        skill: str = "",
        script: str = "",
        args: str = "",
        name: str = "",  # alias
    ) -> str:
        """Execute a script bundled under a skill's scripts/ directory."""
        reg = getattr(runtime, "skills", None)
        if reg is None:
            return format_tool_error(
                "No skill registry",
                code="NO_SKILLS",
                tool_name="skill_run",
                suggestion="Restart the server.",
            )
        nm = (skill or name or "").strip()
        sk = reg.get(nm) if nm else None
        if sk is None:
            return format_tool_error(
                f"Skill not found: {nm}",
                code="SKILL_NOT_FOUND",
                tool_name="skill_run",
                suggestion="skill_activate first, or check /skills.",
            )
        meta = sk.manifest.metadata or {}
        if meta.get("quarantine"):
            return format_tool_error(
                f"Skill '{nm}' is quarantined (imported pack)",
                code="QUARANTINE",
                tool_name="skill_run",
                suggestion=(
                    "Open Skills panel → Trust / Activate after review. "
                    "Script execution is blocked until quarantine is cleared."
                ),
            )
        st = getattr(sk.manifest.status, "value", str(sk.manifest.status))
        if str(st).lower() in ("disabled", "archived", "deprecated"):
            return format_tool_error(
                f"Skill '{nm}' is {st} (not active)",
                code="SKILL_INACTIVE",
                tool_name="skill_run",
                suggestion="Force-promote or Unarchive the skill in the Skills panel first.",
            )
        from remedy.core.approvals import APPROVALS

        run_cmd = f"skill_run {nm} {script or ''}".strip()
        ask_reason = APPROVALS.needs_ask(run_cmd, tool_name="skill_run")
        sid = getattr(runtime, "_session_id", None)
        if ask_reason and not APPROVALS.is_approved(
            "skill_run", run_cmd, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="skill_run",
                command=run_cmd,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"skill={nm}\n"
                "Do not invent success. Tell the user this needs approval "
                f"(or /approve {item.id})."
            )
        scripts = list(sk.scripts or [])
        if not scripts:
            return format_tool_error(
                f"Skill '{nm}' has no scripts/",
                code="NO_SCRIPTS",
                tool_name="skill_run",
                suggestion="Use skill_activate and follow instructions instead.",
            )
        chosen = (script or "").strip() or scripts[0]
        # Normalize relative path
        if chosen not in scripts:
            # allow bare filename
            matches = [s for s in scripts if s.endswith(chosen) or Path(s).name == chosen]
            if matches:
                chosen = matches[0]
            else:
                return format_tool_error(
                    f"Script not in skill: {chosen}",
                    code="SCRIPT_NOT_FOUND",
                    tool_name="skill_run",
                    suggestion=f"Available: {', '.join(scripts)}",
                )
        base = Path(sk.source_skill_dir or sk.manifest.path or "")
        script_path = (base / chosen).resolve()
        # Jail: must stay under skill dir
        try:
            script_path.relative_to(base.resolve())
        except Exception:
            return format_tool_error(
                "Script path escapes skill directory",
                code="PATH_JAIL",
                tool_name="skill_run",
                suggestion="Use a relative scripts/ path only.",
            )
        arg_list = [a for a in (args or "").split() if a]
        try:
            from remedy.skills.executor import SkillExecutor

            ex = SkillExecutor()
            result = await ex.run_script(script_path, args=arg_list)
            ok = bool(result.success)
            with suppress(Exception):
                loop = runtime._get_learning_loop()
                if loop is not None:
                    loop.record_skill_feedback(
                        nm,
                        success=ok,
                        duration_ms=float(result.duration_ms or 0),
                        session_id=str(getattr(runtime, "_session_id", "") or ""),
                        error=result.error,
                    )
                    loop.auto_refine_skill(sk)
                    with suppress(Exception):
                        from remedy.nanoswarm import get_swarm
                        from remedy.nanoswarm.events import SwarmEvent

                        get_swarm().dispatch(
                            SwarmEvent.skill_result(
                                nm,
                                success=ok,
                                duration_ms=float(result.duration_ms or 0),
                            ),
                            learning_loop=loop,
                            skill=sk,
                        )
            if ok:
                out = (result.stdout or "")[:12000]
                return out or f"Script {chosen} exited 0 (no stdout)."
            return format_tool_error(
                result.error or result.stderr or "script failed",
                code="SCRIPT_FAILED",
                tool_name="skill_run",
                suggestion="Check script args or skill_activate for manual steps.",
            )
        except Exception as e:
            return format_tool_error(
                str(e),
                code="SCRIPT_ERROR",
                tool_name="skill_run",
                suggestion="See logs; try skill_activate instead.",
            )

    async def skill_search(query: str = "", limit: int = 8) -> str:
        """Rank skills for the current task (name/description/tags/effort/status)."""
        reg = getattr(runtime, "skills", None)
        if reg is None:
            return "[]"
        ranked = reg.match_skills(
            query or "",
            limit=max(1, min(int(limit or 8), 20)),
            workspace_hint=str(runtime.effective_project_path()),
        )
        lines = []
        for skill, score in ranked:
            m = skill.manifest
            lines.append(
                f"- {m.name} (score={score:.2f}, {m.status.value}): {m.description[:140]}"
            )
        return "\n".join(lines) if lines else "(no matching skills)"

    runtime.tool_registry.register_builtin_handler(
        "skill_activate",
        "Load full instructions for a skill pack (progressive disclosure). "
        "Use when a catalog skill matches the task. Pass skill= exact skill id.",
        skill_activate,
        {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Skill id (from catalog)",
                },
                "name": {
                    "type": "string",
                    "description": "Alias for skill=",
                },
                "include_references": {
                    "type": "boolean",
                    "description": "Also load references/ files (default false)",
                },
            },
            "required": ["skill"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "skill_run",
        "Run a script from a skill's scripts/ directory in a sandbox. "
        "Prefer skill_activate for procedure-only skills.",
        skill_run,
        {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Skill id"},
                "name": {"type": "string", "description": "Alias for skill="},
                "script": {
                    "type": "string",
                    "description": "Relative script path (default first script)",
                },
                "args": {
                    "type": "string",
                    "description": "Space-separated CLI args",
                },
            },
            "required": ["skill"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "skill_search",
        "Rank available skills for a query (status, description, effort, tags).",
        skill_search,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Task keywords"},
                "limit": {"type": "integer", "description": "Max results (default 8)"},
            },
        },
    )

