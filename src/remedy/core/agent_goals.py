"""Partner goal + plan tool registration (extracted from BasicRuntime).

Keeps agent.py thinner while preserving the personal-partner goal checklist
and structured Plan mode artifacts.
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any


def register_goal_and_plan_tools(runtime: Any) -> None:
    """Register goal_* and plan_* builtin tools on *runtime*."""

    def _life_store():
        from remedy.memory.life_goals import LifeGoalStore

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        return LifeGoalStore(home)

    async def goal_add(title: str = "", description: str = "") -> str:
        t = (title or "").strip()
        if not t:
            return "Provide a goal title."
        life = None
        drove = None
        with suppress(Exception):
            from remedy.memory.life_drive import invent_next, take_step

            store = _life_store()
            life = store.add(
                t,
                why=description or "",
                source="goal_add",
            )
            if life and not life.next_action:
                store.set_next(life.title, invent_next(life))
            drove = take_step(
                getattr(getattr(runtime, "config", None), "home_dir", None),
                force=True,
            )
        task = runtime.create_task(t, description=description or "", tags=["goal"])
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if runtime._session_brief is None:
                runtime._session_brief = SessionBrief()
            if t not in runtime._session_brief.open_tasks:
                runtime._session_brief.open_tasks.append(t)
                runtime._session_brief.open_tasks = runtime._session_brief.open_tasks[-20:]
                runtime._session_brief.touch()
        # Organism: a stated goal is durable life memory, not only a session task
        with suppress(Exception):
            mem = getattr(runtime, "memory", None)
            if mem is not None:
                from remedy.memory.partner_memory import upsert_profile_fact

                async def _remember() -> None:
                    profile = await mem.get_or_create_profile()
                    text = t if not description else f"{t} — {description.strip()[:160]}"
                    upsert_profile_fact(
                        profile,
                        f"Goal: {text[:240]}",
                        category="goal",
                        confidence=0.9,
                        source="goal_add",
                    )
                    await mem.save_user_profile(profile)

                await _remember()
        # Dream immediately: a new goal should shape how I partner *now*
        with suppress(Exception):
            from remedy.memory.soul.partner_dream import refresh_partner_dreams

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            refresh_partner_dreams(
                home,
                memory=getattr(runtime, "memory", None),
                extra_goals=[t],
            )
        extra = ""
        if life is not None:
            extra = f" life_id={life.id} horizon={life.horizon}"
            if life.next_action:
                extra += f" next={life.next_action}"
        if isinstance(drove, dict) and drove.get("ok"):
            extra += "\n" + str(drove.get("markdown") or "")
        return f"Goal added id={task.id} title={t}{extra}"

    async def goal_list(status: str = "") -> str:
        with suppress(Exception):
            from remedy.memory.life_goals import format_goals_markdown

            st = (status or "").strip().lower()
            include = st in ("done", "dropped", "all", "completed")
            goals = _life_store().list(include_closed=include)
            if st in ("done", "completed"):
                goals = [g for g in goals if g.status == "done"]
            if goals:
                return format_goals_markdown(goals)
        from remedy.models import TaskStatus

        st = (status or "").strip().lower()
        tasks = runtime.list_tasks()
        if st:
            try:
                enum_st = TaskStatus(st)
                tasks = [t for t in tasks if t.status == enum_st]
            except Exception:
                tasks = [t for t in tasks if t.status.value == st]
        tagged = [t for t in tasks if "goal" in (t.tags or [])]
        use = tagged if tagged else list(tasks)
        if not use:
            return "No life goals yet. Use goal_add or /goal <title>."
        lines = []
        for t in use[:30]:
            lines.append(
                f"- [{t.status.value}] {t.title}"
                + (f" — {t.result_summary}" if t.result_summary else "")
                + f"  (id={t.id})"
            )
        return "Goals:\n" + "\n".join(lines)

    async def goal_clear_all() -> str:
        """Mark all open goals complete and wipe session open_tasks (user said clear)."""
        from datetime import UTC, datetime

        from remedy.models import TaskStatus

        tasks = list(runtime.list_tasks() or [])
        to_clear = [
            t
            for t in tasks
            if t.status != TaskStatus.COMPLETED and "goal" in (t.tags or [])
        ]
        n = 0
        for task in to_clear:
            task.status = TaskStatus.COMPLETED
            task.result_summary = "cleared by user"
            task.completed_at = datetime.now(UTC)
            task.updated_at = datetime.now(UTC)
            n += 1
        with suppress(Exception):
            if runtime._session_brief is not None:
                runtime._session_brief.open_tasks = []
                runtime._session_brief.touch()
        with suppress(Exception):
            store = _life_store()
            for g in store.list():
                store.complete(g.title, evidence="cleared by user")
        if n == 0:
            with suppress(Exception):
                if _life_store().open_count() == 0:
                    return "No open goals — already clear."
            return "Cleared open life goals."
        return f"Cleared {n} open goal(s). Session open tasks wiped."

    async def goal_complete(title: str = "", evidence: str = "") -> str:
        from datetime import UTC, datetime

        from remedy.models import TaskStatus

        needle = (title or "").strip().lower()
        if not needle:
            return "Provide goal title (or partial) to complete."
        matches = [
            t
            for t in runtime.list_tasks()
            if needle in t.title.lower() and t.status != TaskStatus.COMPLETED
        ]
        if not matches:
            with suppress(Exception):
                life = _life_store().complete(title, evidence=evidence or "")
                if life is not None:
                    return f"Goal completed: {life.title}" + (
                        f" evidence={evidence[:200]}" if evidence else ""
                    )
            return f"No open goal matching: {title}"
        task = matches[0]
        task.status = TaskStatus.COMPLETED
        task.result_summary = (evidence or "done").strip()[:500]
        task.completed_at = datetime.now(UTC)
        task.updated_at = datetime.now(UTC)
        with suppress(Exception):
            from remedy.nanoswarm import get_swarm

            get_swarm().goal.note_completed(
                task.title,
                session_id=str(getattr(runtime, "_session_id", "") or ""),
            )
        with suppress(Exception):
            if runtime._session_brief is not None:
                runtime._session_brief.open_tasks = [
                    x
                    for x in runtime._session_brief.open_tasks
                    if x.lower() != task.title.lower()
                ]
                if evidence:
                    runtime._session_brief.decisions.append(
                        f"Completed goal: {task.title} — {evidence[:120]}"
                    )
                    runtime._session_brief.decisions = runtime._session_brief.decisions[-20:]
                runtime._session_brief.touch()
        if runtime.memory is not None and evidence:
            with suppress(Exception):
                from remedy.models import MemoryEntry, MemoryEntryType

                await runtime.memory.upsert(
                    MemoryEntry(
                        title=f"Goal done: {task.title}",
                        content=evidence[:2000],
                        entry_type=MemoryEntryType.NOTE,
                        tags=["goal", "verified"],
                        importance=0.7,
                    )
                )
        with suppress(Exception):
            _life_store().complete(task.title, evidence=evidence or "")
        return f"Goal completed: {task.title}" + (
            f" evidence={evidence[:200]}" if evidence else ""
        )

    async def goal_set_next(
        title: str = "", action: str = "", next_by: str = ""
    ) -> str:
        t = (title or "").strip()
        a = (action or "").strip()
        if not t or not a:
            return "Provide title and next action."
        g = None
        with suppress(Exception):
            g = _life_store().set_next(t, a, next_by=next_by or "")
        if g is None:
            return f"No open life goal matching: {title}"
        due = f" by {g.next_by}" if g.next_by else ""
        drove = None
        with suppress(Exception):
            from remedy.memory.life_drive import take_step

            drove = take_step(
                getattr(getattr(runtime, "config", None), "home_dir", None),
                force=True,
            )
        extra = ""
        if isinstance(drove, dict) and drove.get("markdown"):
            extra = "\n" + str(drove.get("markdown"))
        return f"Next for **{g.title}**: {g.next_action}{due}{extra}"

    async def goal_drive() -> str:
        from remedy.memory.life_drive import take_step

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        out = take_step(home, force=True)
        if out.get("ok"):
            return str(out.get("markdown") or "Took a local step.")
        if out.get("skipped") == "needs_you":
            return str(out.get("markdown") or "That next step needs you.")
        if out.get("skipped") == "no_open_goal":
            return "No open life goal. Use goal_add or /goal first."
        return str(out.get("markdown") or out.get("skipped") or "Nothing to do.")

    async def goal_verify(title: str = "", evidence: str = "") -> str:
        if not (evidence or "").strip():
            return "Provide evidence of completion (command output, file path, result)."
        return await goal_complete(title=title, evidence=evidence)

    def _plan_store():
        from pathlib import Path

        from remedy.core.plan_store import PlanStore

        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        if not home:
            with suppress(Exception):
                from remedy.interfaces.config import load_config

                home = load_config().get("home_dir")
        return PlanStore(home or Path.home() / ".remedy")

    def _parse_plan_list_arg(value: Any, *, as_strings: bool = False) -> list[Any]:
        """Accept native JSON array (tool_calls) or a JSON/bullet string.

        Models often pass ``steps`` / ``risks`` as real arrays — never call
        ``.strip()`` on those (AttributeError). Mirrors spread_run task parsing.
        """
        if value is None or value == "" or value == []:
            return []
        if isinstance(value, list):
            items = value
        elif isinstance(value, dict):
            items = [value]
        elif isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            items = []
            if raw.startswith("[") or raw.startswith("{"):
                with suppress(json.JSONDecodeError):
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        items = parsed
                    elif isinstance(parsed, dict):
                        items = [parsed]
            if not items:
                if as_strings:
                    items = [
                        ln.strip("-*• ").strip()
                        for ln in raw.splitlines()
                        if ln.strip()
                    ]
                else:
                    from remedy.core.plan_store import parse_steps_from_text

                    items = parse_steps_from_text(raw) or [
                        ln.strip("-*• ").strip()
                        for ln in raw.splitlines()
                        if ln.strip()
                    ]
        else:
            items = [value]
        if as_strings:
            out: list[Any] = []
            for x in items:
                if isinstance(x, dict):
                    # Risk object → title/detail string
                    s = str(x.get("title") or x.get("risk") or x.get("text") or x).strip()
                else:
                    s = str(x).strip()
                if s:
                    out.append(s)
            return out
        return list(items)

    async def plan_save(
        title: str = "",
        goal: str = "",
        steps: Any = "",
        risks: Any = "",
        status: str = "draft",
    ) -> str:
        """Save a structured plan. steps/risks: native array, JSON array, or bullets."""
        store = _plan_store()
        t = str(title or goal or "Session plan").strip()
        if not t:
            return "Provide a plan title."

        step_list = _parse_plan_list_arg(steps, as_strings=False)
        risk_list = [str(x) for x in _parse_plan_list_arg(risks, as_strings=True)]

        sid = getattr(runtime, "_session_id", None)
        plan = store.create(
            t,
            goal=str(goal or t),
            steps=step_list,
            risks=risk_list,
            session_id=str(sid) if sid else None,
            status=str(status or "draft").strip().lower(),
        )
        # Surface in session brief
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if runtime._session_brief is None:
                runtime._session_brief = SessionBrief()
            runtime._session_brief.intent = plan.goal or plan.title
            runtime._session_brief.touch()
        return (
            f"Plan saved id={plan.id} status={plan.status} steps={len(plan.steps)}.\n\n"
            + plan.summary_markdown()
        )

    def _plan_owned_by_session(plan: Any, sid: str | None) -> bool:
        """Refuse cross-session plan_id reads when both sides are tagged."""
        if not sid:
            return True
        psid = str(getattr(plan, "session_id", None) or "")
        if not psid:
            # Untagged legacy plans: allow only when no stronger isolation available
            return True
        return psid == str(sid)

    async def plan_show(plan_id: str = "") -> str:
        store = _plan_store()
        plan = None
        sid = getattr(runtime, "_session_id", None)
        if (plan_id or "").strip():
            plan = store.get(plan_id.strip())
            if plan is not None and not _plan_owned_by_session(plan, str(sid) if sid else None):
                return (
                    f"Plan {plan_id.strip()} belongs to another session "
                    "(cross-session plan read blocked)."
                )
        if plan is None:
            # Session-scoped only — do not surface another chat's plan.
            plan = store.latest_for_session(str(sid) if sid else None)
        if plan is None:
            return "No plans saved yet for this session. Use plan_save in Plan mode."
        return plan.summary_markdown()

    async def plan_list(limit: int = 10) -> str:
        store = _plan_store()
        sid = getattr(runtime, "_session_id", None)
        # Prefer this session; if none tagged, list recent (explicit, not as "current")
        plans = store.list_plans(
            session_id=str(sid) if sid else None,
            limit=max(1, min(int(limit or 10), 30)),
        )
        if not plans and sid:
            return "No plans for this session yet. Use plan_save in Plan mode."
        if not plans:
            return "No plans yet."
        lines = [f"- [{p.status}] {p.title} (id={p.id}, steps={len(p.steps)})" for p in plans]
        return "Plans:\n" + "\n".join(lines)

    async def plan_step_status(
        step_id: str = "",
        status: str = "done",
        plan_id: str = "",
        plan_status: str = "",
        note: str = "",
    ) -> str:
        """Mark a plan step pending|active|done|skipped. Prefer this over [done] in titles."""
        store = _plan_store()
        sid = getattr(runtime, "_session_id", None)
        pid = (plan_id or "").strip()
        plan = store.get(pid) if pid else None
        if plan is not None and not _plan_owned_by_session(plan, str(sid) if sid else None):
            return (
                f"Plan {pid} belongs to another session "
                "(cross-session plan update blocked)."
            )
        if plan is None:
            plan = store.latest_for_session(
                str(sid) if sid else None,
                actionable_only=True,
            )
        if plan is None and sid:
            plan = store.latest_for_session(str(sid), actionable_only=False)
        if plan is None:
            return (
                "No plan found for this session. Use plan_save first, or pass plan_id=."
            )
        st = (status or "done").strip().lower()
        if st not in ("pending", "active", "done", "skipped"):
            return (
                f"Invalid step status {st!r}. Use pending | active | done | skipped."
            )
        sid_key = (step_id or "").strip()
        if not sid_key:
            # Convenience: mark first pending as active / first active as done
            if st == "active":
                for s in plan.steps:
                    if s.status == "pending":
                        sid_key = s.id
                        break
            elif st == "done":
                for s in plan.steps:
                    if s.status == "active":
                        sid_key = s.id
                        break
                if not sid_key:
                    for s in plan.steps:
                        if s.status == "pending":
                            sid_key = s.id
                            break
            if not sid_key:
                return (
                    "step_id required (or ensure there is a pending/active step). "
                    f"Plan {plan.id} steps: "
                    + ", ".join(f"{s.id}:{s.status}" for s in plan.steps)
                )
        updated = store.update_step_status(plan.id, sid_key, st)
        if updated is None:
            return (
                f"Step not found: {sid_key!r} on plan {plan.id}. "
                "Pass step id (s1), 1-based index, or title. "
                + "Steps: "
                + ", ".join(f"{s.id}={s.title!r}[{s.status}]" for s in plan.steps)
            )
        pst = (plan_status or "").strip().lower()
        if pst in ("draft", "approved", "active", "done", "cancelled"):
            bumped = store.set_status(updated.id, pst)
            if bumped is not None:
                updated = bumped
        note_s = (note or "").strip()
        extra = f" note={note_s}" if note_s else ""
        done_n = sum(1 for s in updated.steps if s.status in ("done", "skipped"))
        return (
            f"Plan {updated.id} step → {st}{extra}. "
            f"plan_status={updated.status} progress={done_n}/{len(updated.steps)}.\n\n"
            + updated.summary_markdown()
        )

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "goal_add",
        "Add a user goal / checklist item for this session (partner loop).",
        goal_add,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
            },
            "required": ["title"],
        },
    )
    reg.register_builtin_handler(
        "goal_list",
        "List tracked goals and their status.",
        goal_list,
        {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional filter: created, in_progress, completed",
                },
            },
        },
    )
    reg.register_builtin_handler(
        "goal_clear_all",
        "Clear all open goals and session open-tasks. Use when the user says clear goals / we have none.",
        goal_clear_all,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "goal_drive",
        "Take one local step toward the active life goal without asking. "
        "Writes notes under ~/.remedy/life. Will not send, pay, or publish.",
        goal_drive,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "goal_set_next",
        "Set the one next action for a life goal (this week / this season).",
        goal_set_next,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "action": {"type": "string"},
                "next_by": {"type": "string", "description": "Optional date or weekday"},
            },
            "required": ["title", "action"],
        },
    )
    reg.register_builtin_handler(
        "goal_complete",
        "Mark a goal complete; optionally store evidence for the verify/learn loop.",
        goal_complete,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["title"],
        },
    )
    reg.register_builtin_handler(
        "goal_verify",
        "Verify a goal with evidence (path, test output, screenshot note) and mark done.",
        goal_verify,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["title", "evidence"],
        },
    )
    reg.register_builtin_handler(
        "plan_save",
        "Save a structured task plan (title, steps, risks). Use in Plan mode so Build can follow it.",
        plan_save,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "goal": {"type": "string"},
                "steps": {
                    "description": (
                        "Step list: native array of titles/objects, a JSON array "
                        "string, or a bullet/numbered list"
                    ),
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "title": {"type": "string"},
                                            "detail": {"type": "string"},
                                            "tools": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                            "risks": {
                                                "type": "array",
                                                "items": {"type": "string"},
                                            },
                                        },
                                    },
                                ]
                            },
                        },
                        {"type": "string"},
                    ],
                },
                "risks": {
                    "description": (
                        "Risk list: native array of strings, JSON array string, "
                        "or bullet list"
                    ),
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "object"},
                                ]
                            },
                        },
                        {"type": "string"},
                    ],
                },
                "status": {
                    "type": "string",
                    "description": (
                        "draft | approved | active (preferred). "
                        "done/cancelled on a fresh save with pending steps are "
                        "normalized to draft — use UI Cancel or status API to finish/quit."
                    ),
                },
            },
            "required": ["title"],
        },
    )
    reg.register_builtin_handler(
        "plan_show",
        "Show the latest (or specified) structured task plan as markdown.",
        plan_show,
        {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
            },
        },
    )
    reg.register_builtin_handler(
        "plan_list",
        "List recent structured task plans.",
        plan_list,
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
            },
        },
    )
    reg.register_builtin_handler(
        "plan_step_status",
        "Update one plan step status (pending|active|done|skipped). "
        "Use after finishing a step — never fake progress with '[done]' in titles. "
        "step_id may be s1, 1-based index, or title. Omit plan_id for latest session plan.",
        plan_step_status,
        {
            "type": "object",
            "properties": {
                "step_id": {
                    "type": "string",
                    "description": "Step id (s1), 1-based index, or title. "
                    "Omit to auto-pick first pending→active or active→done.",
                },
                "status": {
                    "type": "string",
                    "description": "pending | active | done | skipped",
                },
                "plan_id": {
                    "type": "string",
                    "description": "Optional plan id (default: latest for session)",
                },
                "plan_status": {
                    "type": "string",
                    "description": "Optional plan-level status override "
                    "(draft|approved|active|done|cancelled)",
                },
                "note": {
                    "type": "string",
                    "description": "Optional short note (shown in tool result only)",
                },
            },
            "required": ["status"],
        },
    )
