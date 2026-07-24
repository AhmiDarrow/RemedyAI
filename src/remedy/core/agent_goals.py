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

    async def goal_add(title: str = "", description: str = "") -> str:
        t = (title or "").strip()
        if not t:
            return "Provide a goal title."
        task = runtime.create_task(t, description=description or "", tags=["goal"])
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if runtime._session_brief is None:
                runtime._session_brief = SessionBrief()
            if t not in runtime._session_brief.open_tasks:
                runtime._session_brief.open_tasks.append(t)
                runtime._session_brief.open_tasks = runtime._session_brief.open_tasks[-20:]
                runtime._session_brief.touch()
        return f"Goal added id={task.id} title={t}"

    async def goal_list(status: str = "") -> str:
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
            return "No goals yet. Use goal_add to create one."
        lines = []
        for t in use[:30]:
            lines.append(
                f"- [{t.status.value}] {t.title}"
                + (f" — {t.result_summary}" if t.result_summary else "")
                + f"  (id={t.id})"
            )
        return "Goals:\n" + "\n".join(lines)

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
            return f"No open goal matching: {title}"
        task = matches[0]
        task.status = TaskStatus.COMPLETED
        task.result_summary = (evidence or "done").strip()[:500]
        task.completed_at = datetime.now(UTC)
        task.updated_at = datetime.now(UTC)
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
        return f"Goal completed: {task.title}" + (
            f" evidence={evidence[:200]}" if evidence else ""
        )

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

    async def plan_save(
        title: str = "",
        goal: str = "",
        steps: str = "",
        risks: str = "",
        status: str = "draft",
    ) -> str:
        """Save a structured plan. steps/risks: JSON array or newline/bullet list."""
        store = _plan_store()
        t = (title or goal or "Session plan").strip()
        if not t:
            return "Provide a plan title."

        step_list: list[Any] = []
        raw_steps = (steps or "").strip()
        if raw_steps:
            if raw_steps.startswith("["):
                with suppress(json.JSONDecodeError):
                    parsed = json.loads(raw_steps)
                    if isinstance(parsed, list):
                        step_list = parsed
            if not step_list:
                from remedy.core.plan_store import parse_steps_from_text

                step_list = parse_steps_from_text(raw_steps) or [
                    ln.strip("-*• ").strip()
                    for ln in raw_steps.splitlines()
                    if ln.strip()
                ]

        risk_list: list[str] = []
        raw_risks = (risks or "").strip()
        if raw_risks:
            if raw_risks.startswith("["):
                with suppress(json.JSONDecodeError):
                    parsed = json.loads(raw_risks)
                    if isinstance(parsed, list):
                        risk_list = [str(x) for x in parsed]
            if not risk_list:
                risk_list = [
                    ln.strip("-*• ").strip()
                    for ln in raw_risks.splitlines()
                    if ln.strip()
                ]

        sid = getattr(runtime, "_session_id", None)
        plan = store.create(
            t,
            goal=goal or t,
            steps=step_list,
            risks=risk_list,
            session_id=str(sid) if sid else None,
            status=(status or "draft").strip().lower(),
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

    async def plan_show(plan_id: str = "") -> str:
        store = _plan_store()
        plan = None
        if (plan_id or "").strip():
            plan = store.get(plan_id.strip())
        if plan is None:
            sid = getattr(runtime, "_session_id", None)
            plan = store.latest_for_session(str(sid) if sid else None)
        if plan is None:
            plans = store.list_plans(limit=1)
            plan = plans[0] if plans else None
        if plan is None:
            return "No plans saved yet. Use plan_save in Plan mode."
        return plan.summary_markdown()

    async def plan_list(limit: int = 10) -> str:
        store = _plan_store()
        sid = getattr(runtime, "_session_id", None)
        plans = store.list_plans(session_id=str(sid) if sid else None, limit=max(1, min(int(limit or 10), 30)))
        if not plans:
            plans = store.list_plans(limit=max(1, min(int(limit or 10), 30)))
        if not plans:
            return "No plans yet."
        lines = [f"- [{p.status}] {p.title} (id={p.id}, steps={len(p.steps)})" for p in plans]
        return "Plans:\n" + "\n".join(lines)

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
                    "type": "string",
                    "description": "JSON array of step titles/objects, or a bullet/numbered list",
                },
                "risks": {
                    "type": "string",
                    "description": "JSON array or bullet list of risks",
                },
                "status": {
                    "type": "string",
                    "description": "draft | approved | active | done",
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
