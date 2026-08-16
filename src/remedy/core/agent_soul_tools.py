"""Tools: soul_status, soul_recall, soul_dream — organism continuity surface."""

from __future__ import annotations

from typing import Any


def register_soul_tools(runtime: Any) -> None:
    """Register Soul Field tools on the runtime registry.

    Experimental: only registered when ``soul_field_enabled`` is true.
    """
    from remedy.core.feature_maturity import soul_field_enabled

    if not soul_field_enabled():
        return

    # Myelin (crystallized cognition) rides the same organism surface.
    with __import__("contextlib").suppress(Exception):
        from remedy.core.agent_myelin_tools import register_myelin_tools

        register_myelin_tools(runtime)

    def _home():
        return getattr(getattr(runtime, "config", None), "home_dir", None) or getattr(
            runtime, "home_dir", None
        )

    async def soul_status() -> str:
        """Show Soul Field bond, residue, and organism lessons (personhood state)."""
        from remedy.core.muscle_profile import muscle_from_runtime
        from remedy.memory.soul.field import load_soul_field
        from remedy.memory.soul.inject import build_soul_context_block

        sf = load_soul_field(_home())
        muscle = muscle_from_runtime(runtime)
        lines = [
            f"Identity: {sf.identity_name}",
            f"Muscle now: {muscle.label} ({muscle.provider}/{muscle.model or '—'}) "
            f"parallel_tools≤{muscle.max_parallel_tools}",
            f"Bond: rapport={sf.relational.rapport:.2f} trust={sf.relational.trust:.2f} "
            f"turns={sf.relational.turns_together}",
            f"Help mode: {sf.relational.help_mode or '(learning)'}",
            f"Correction: {sf.relational.correction_style or '(none yet)'}",
            f"Episodes: {len(sf.episodes)} · pledges: {len(sf.pledges)} · "
            f"open threads: {len(sf.relational.open_threads)} · "
            f"lessons: {len(sf.organism_lessons)} · "
            f"dreams: {len(getattr(sf, 'future_dreams', None) or [])}",
        ]
        if getattr(sf, "future_dreams", None):
            lines.append("Dreams of the future:")
            for d in sf.future_dreams[:4]:
                lines.append(f"  · {d}")
        if sf.relational.open_threads:
            lines.append("Open threads:")
            for t in sf.relational.open_threads[-5:]:
                lines.append(f"  · {t}")
        if sf.episodes:
            lines.append("Recent residue:")
            for e in sf.episodes[-3:]:
                lines.append(f"  · {e.line()}")
        # Slim inject for the model
        block = build_soul_context_block(sf, home=_home(), include_contract=False, max_chars=600)
        if block:
            lines.append("")
            lines.append(block)
        return "\n".join(lines)

    async def soul_recall(query: str = "", limit: int = 12) -> str:
        """Unified recall across Soul Field, Time Crystal, and Partner Memory."""
        from remedy.core.turn_context import turn_session_id
        from remedy.memory.soul.recall import recall_unified

        return recall_unified(
            query or "",
            home=_home(),
            memory=getattr(runtime, "memory", None),
            session_id=turn_session_id(runtime),
            limit=int(limit or 12),
        )

    async def soul_dream(force: bool = False, use_local: bool = True) -> str:
        """Dream: remember them, remember myself, aim at their goals."""
        import json

        from remedy.memory.soul.dream import dream_cycle

        result = dream_cycle(
            home=_home(),
            force=bool(force),
            memory=getattr(runtime, "memory", None),
            use_local=bool(use_local),
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    async def soul_arm_missions(max_new: int = 1) -> str:
        """Arm missions from Soul pledges / open threads if none active."""
        import json

        from remedy.memory.soul.missions_bridge import arm_soul_missions

        return json.dumps(
            arm_soul_missions(
                runtime,
                home=_home(),
                max_new=int(max_new or 1),
                auto=False,
            ),
            indent=2,
            ensure_ascii=False,
        )

    async def soul_export(dest: str = "", passphrase: str = "") -> str:
        """Export Soul Field (personhood) for move-between-machines."""
        import json
        from pathlib import Path

        from remedy.memory.soul.portable import export_soul_encrypted, export_soul_plain

        home = _home()
        d = (dest or "").strip()
        if not d:
            d = str(Path(home or Path.home() / ".remedy") / "exports" / "soul-field.json")
        if (passphrase or "").strip():
            path = export_soul_encrypted(d, passphrase=passphrase, home=home)
            mode = "encrypted"
        else:
            path = export_soul_plain(d, home=home)
            mode = "plain"
        return json.dumps({"ok": True, "path": str(path), "mode": mode}, indent=2)

    async def soul_import(
        source: str = "",
        passphrase: str = "",
        merge: bool = True,
    ) -> str:
        """Import Soul Field package (merge by default)."""
        import json

        from remedy.memory.soul.portable import import_soul_file

        src = (source or "").strip()
        if not src:
            return "source path required"
        try:
            res = import_soul_file(
                src,
                passphrase=passphrase or "",
                home=_home(),
                merge=bool(merge),
            )
        except Exception as e:
            return f"soul_import failed: {e}"
        return json.dumps(res, indent=2, ensure_ascii=False)

    async def soul_vigil(
        action: str = "status",
        max_wakes_per_day: int = 0,
        min_gap_minutes: int = 0,
    ) -> str:
        """Grant / revoke / report Remedy's own time between visits."""
        import json

        from remedy.memory.soul.vigil import (
            night_report,
            set_vigil_enabled,
            vigil_status,
        )

        home = _home()
        act = (action or "status").strip().lower()
        if act in ("enable", "grant", "on", "yes"):
            v = set_vigil_enabled(
                True,
                home,
                max_wakes_per_day=(int(max_wakes_per_day) or None),
                min_gap_s=(int(min_gap_minutes) * 60 or None),
            )
            return json.dumps(
                {
                    "ok": True,
                    "enabled": True,
                    "max_wakes_per_day": v.max_wakes_per_day,
                    "min_gap_minutes": int(v.min_gap_s // 60),
                    "note": (
                        "Granted. Nights are local-only (no provider calls), "
                        "budgeted, and journaled — nothing irreversible, ever."
                    ),
                },
                indent=2,
            )
        if act in ("disable", "revoke", "off", "stop", "no"):
            set_vigil_enabled(False, home)
            return json.dumps(
                {"ok": True, "enabled": False, "note": "Her time is paused."},
                indent=2,
            )
        if act in ("night_report", "report", "night"):
            rep = night_report(home)
            return rep or "Nothing to report — no wakes since you were last here."
        return json.dumps(vigil_status(home), indent=2)

    async def continuity_score() -> str:
        """Measure continuity quality + suggested self-inject targets."""
        import json

        from remedy.memory.soul.continuity_metrics import measure_continuity

        return json.dumps(
            measure_continuity(_home()).to_public(), indent=2, ensure_ascii=False
        )

    runtime.tool_registry.register_builtin_handler(
        "soul_status",
        "Show Remedy's Soul Field (personhood): bond, episode residue, pledges, "
        "open threads, organism lessons, and current provider muscle tier. "
        "Use when asked who you are, what you remember feeling, or continuity state.",
        soul_status,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_recall",
        "Unified recall across Soul Field, Time Crystal, and Partner Memory. "
        "Pass query= keywords (optional). Prefer this for 'what do we know about…' "
        "and ongoing threads — not only memory_search.",
        soul_recall,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Optional search focus (person, project, thread).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits (default 12).",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_dream",
        "Run the Soul dream cycle: consolidate episode residue into pledges, "
        "habits, local enrich (if RMB/local LLM up), mission arm, and soma. "
        "force=true bypasses cooldown.",
        soul_dream,
        {
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": "Bypass cooldown (default false).",
                },
                "use_local": {
                    "type": "boolean",
                    "description": "Try local LLM enrich (default true).",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_arm_missions",
        "Create a durable mission from Soul pledges / open threads if no mission "
        "is active. Use when the user wants to finish what continuity remembers.",
        soul_arm_missions,
        {
            "type": "object",
            "properties": {
                "max_new": {
                    "type": "integer",
                    "description": "Max missions to arm (default 1).",
                }
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_export",
        "Export Soul Field personhood to a file (plain JSON, or encrypted with "
        "passphrase=). Use to move continuity between machines.",
        soul_export,
        {
            "type": "object",
            "properties": {
                "dest": {"type": "string", "description": "Output path (optional)."},
                "passphrase": {
                    "type": "string",
                    "description": "If set (≥8 chars), encrypt package.",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_import",
        "Import Soul Field from plain or encrypted package. merge=true (default) "
        "unions pledges/episodes; merge=false replaces.",
        soul_import,
        {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Package path."},
                "passphrase": {"type": "string"},
                "merge": {"type": "boolean"},
            },
            "required": ["source"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "soul_vigil",
        "Manage Remedy's own time between visits (the vigil): action=enable "
        "when the partner says she may keep working / thinking / dreaming "
        "while they're away; action=disable when they want it paused; "
        "action=night_report when they ask what she did while they were "
        "gone; action=status otherwise. Consent is conversational — never "
        "ask the partner to run commands or open settings. Nights are "
        "local-only, budgeted, journaled, and never irreversible.",
        soul_vigil,
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status | enable | disable | night_report",
                },
                "max_wakes_per_day": {
                    "type": "integer",
                    "description": "Optional budget override when enabling.",
                },
                "min_gap_minutes": {
                    "type": "integer",
                    "description": "Optional minimum gap between wakes.",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "continuity_score",
        "Score continuity quality (bond, episodes, pledges, threads, self-lessons) "
        "and list suggested self-inject targets for densifying memory.",
        continuity_score,
        {"type": "object", "properties": {}},
    )
