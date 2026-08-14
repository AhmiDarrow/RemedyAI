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

    async def skill_reload() -> str:
        """Rescan disk + re-seed curated packs into the registry (does NOT load bodies).

        Use when the user says reload/rescan/refresh skills — never skill_activate every pack.
        """
        reg = getattr(runtime, "skills", None)
        if reg is None:
            return format_tool_error(
                "No skill registry",
                code="NO_SKILLS",
                tool_name="skill_reload",
                suggestion="Restart the server so skills can load.",
            )
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        before = int(getattr(reg, "count", 0) or 0)
        try:
            n = reg.discover_defaults(home_dir=home)
        except Exception as e:
            return format_tool_error(
                str(e),
                code="RELOAD_FAILED",
                tool_name="skill_reload",
                suggestion="Check REMEDY_HOME / REMEDY_DEV_ROOT and restart serve.",
            )
        # Curated summary (hide auto-learned spam)
        curated: list[str] = []
        learned = 0
        for sk in getattr(reg, "skills", []) or []:
            m = sk.manifest
            meta = m.metadata or {}
            if meta.get("auto_generated"):
                learned += 1
                continue
            curated.append(str(m.name))
        curated.sort(key=str.lower)
        sample = ", ".join(curated[:24])
        more = f" (+{len(curated) - 24} more)" if len(curated) > 24 else ""
        return (
            f"Skills rescanned. Registry count={n} (was {before}). "
            f"Curated packs={len(curated)}; auto-learned hidden from this summary={learned}.\n"
            f"Curated: {sample}{more}\n"
            "_Do not skill_activate every name. Use skill_search(query=task) then "
            "skill_activate only for the pack you will follow. "
            "Self-dev: skill_activate(skill=self-dev-loop)._"
        )

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
        # Guard the "reload all / activate every skill" death spiral
        bulk = nm.lower().replace("_", " ").replace("-", " ")
        if bulk in (
            "all",
            "*",
            "every",
            "everything",
            "reload",
            "reload all",
            "rescan",
            "refresh",
            "all skills",
            "every skill",
        ) or bulk.startswith("all "):
            return (
                "Refusing bulk skill_activate. Progressive disclosure loads **one** "
                "procedure per task — activating every pack floods context and loops.\n"
                "• Rescan disk/catalog: **skill_reload** (no bodies)\n"
                "• Find packs: **skill_search(query=…)**\n"
                "• Load one body: **skill_activate(skill=name)**\n"
                "• Self-dev loop: skill_activate(skill=self-dev-loop)"
            )
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
            with suppress(Exception):
                from remedy.core.metabolism.skill_genome import get_skill_genome

                get_skill_genome().record(nm, ok=False)
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
        # Skill genome phenotype (activation success = body loaded)
        with suppress(Exception):
            from remedy.core.metabolism.skill_genome import get_skill_genome

            get_skill_genome().record(nm, ok=True, latency_ms=0.0)
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
        from remedy.core.turn_context import turn_session_id

        sid = turn_session_id(runtime)
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
        # Packaged skills: only scripts/ tree is executable (never SKILL.md etc.)
        chosen_norm = chosen.replace("\\", "/").lstrip("./")
        if not chosen_norm.startswith("scripts/"):
            return format_tool_error(
                f"Script must live under scripts/: {chosen}",
                code="SCRIPT_NOT_PACKAGED",
                tool_name="skill_run",
                suggestion=f"Available: {', '.join(scripts)}",
            )
        base_raw = (sk.source_skill_dir or sk.manifest.path or "").strip()
        if not base_raw:
            return format_tool_error(
                f"Skill '{nm}' has no source directory",
                code="NO_SKILL_DIR",
                tool_name="skill_run",
                suggestion="Re-discover skills or reinstall the pack.",
            )
        base = Path(base_raw).resolve()
        if not base.is_dir():
            return format_tool_error(
                f"Skill directory missing: {base}",
                code="NO_SKILL_DIR",
                tool_name="skill_run",
                suggestion="Re-install or re-seed the skill pack.",
            )
        try:
            from remedy.skills.script_path import (
                SkillScriptJailError,
                resolve_jailed_skill_script,
            )

            script_path = resolve_jailed_skill_script(base, chosen_norm)
        except SkillScriptJailError:
            return format_tool_error(
                "Script path escapes skill scripts/ directory",
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
                from remedy.core.metrics import default_registry

                default_registry.counter(
                    "remedy_skill_run_total",
                    status=("ok" if ok else "error"),
                ).inc()
            with suppress(Exception):
                loop = runtime._get_learning_loop()
                if loop is not None:
                    _skill_sid = str(getattr(runtime, "_session_id", "") or "")
                    loop.record_skill_feedback(
                        nm,
                        success=ok,
                        duration_ms=float(result.duration_ms or 0),
                        session_id=_skill_sid,
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
                                session_id=_skill_sid,
                                error=result.error,
                                # Feedback already recorded above — avoid double count.
                                record_feedback=False,
                                auto_refine=False,
                            ),
                            learning_loop=loop,
                            skill=sk,
                            session_id=_skill_sid,
                            record_feedback=False,
                            auto_refine=False,
                        )
            with suppress(Exception):
                from remedy.core.metabolism.skill_genome import get_skill_genome

                get_skill_genome().record(
                    nm,
                    ok=ok,
                    latency_ms=float(result.duration_ms or 0),
                )
            if ok:
                out = (result.stdout or "")[:12000]
                # Trust: never echo secret-shaped script output into the model/UI
                with suppress(Exception):
                    from remedy.core.metabolism.redact import redact_text

                    out = redact_text(out)
                return out or f"Script {chosen_norm} exited 0 (no stdout)."
            err_body = result.error or result.stderr or "script failed"
            with suppress(Exception):
                from remedy.core.metabolism.redact import redact_text

                err_body = redact_text(str(err_body))
            return format_tool_error(
                err_body,
                code="SCRIPT_FAILED",
                tool_name="skill_run",
                suggestion="Check script args or skill_activate for manual steps.",
            )
        except Exception as e:
            with suppress(Exception):
                from remedy.core.metrics import default_registry

                default_registry.counter(
                    "remedy_skill_run_total", status="error"
                ).inc()
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

    async def skill_library_search(query: str = "", limit: int = 5) -> str:
        """Rank Skills Library packs (cache only). Does not install."""
        from pathlib import Path

        from remedy.skills.library.suggest import rank_library_skills

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        installed: set[str] = set()
        reg = getattr(runtime, "skills", None)
        if reg is not None and hasattr(reg, "_by_name"):
            installed = {str(n) for n in (reg._by_name or {})}
        hits = rank_library_skills(
            query or "",
            home=Path(home).expanduser() if home else None,
            installed_names=installed,
            limit=max(1, min(int(limit or 5), 10)),
            mark_suppressed=False,
        )
        if not hits:
            return (
                "(no library matches — catalog cache empty or query too weak; "
                "open Skills → Library to refresh catalog)"
            )
        lines = [
            f"- {h.name} (score={h.score:.2f}, id={h.id}): {h.description[:140]}"
            for h in hits
        ]
        lines.append(
            "_Not installed. User installs via Skills → Library (quarantine then Trust). "
            "Do not invent skill bodies._"
        )
        return "\n".join(lines)

    runtime.tool_registry.register_builtin_handler(
        "skill_reload",
        "Rescan disk and re-seed curated skills into the registry. "
        "Use when the user says reload/refresh/rescan skills. "
        "Does NOT load procedure bodies — never a substitute for activating one skill.",
        skill_reload,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "skill_activate",
        "Load full instructions for ONE skill pack (progressive disclosure). "
        "Never pass all/reload/* — use skill_reload to rescan, skill_search to rank, "
        "then skill_activate only for the pack you will follow.",
        skill_activate,
        {
            "type": "object",
            "properties": {
                "skill": {
                    "type": "string",
                    "description": "Exact skill id (e.g. self-dev-loop). Not 'all' or 'reload'.",
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
    runtime.tool_registry.register_builtin_handler(
        "skill_library_search",
        "Search the signed Skills Library catalog (cache only) for packs not yet "
        "installed. Returns top matches — does not install. Prefer skill_search for "
        "installed skills first.",
        skill_library_search,
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Task keywords"},
                "limit": {"type": "integer", "description": "Max results (default 5)"},
            },
        },
    )

