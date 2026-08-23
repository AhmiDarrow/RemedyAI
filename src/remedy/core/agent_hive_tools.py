"""Mother-only hive tools: hire / collect / roster / retire. Daughters never get these."""

from __future__ import annotations

from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.hive.policy import (
    DEFAULT_BUDGET_STEPS,
    DEFAULT_LIVE_PULSES,
    MAX_BUDGET_STEPS,
    hive_depth,
)
from remedy.core.hive.runner import cancel_forager, schedule_forager
from remedy.core.hive.store import get_hive_store
from remedy.core.hive.types import (
    CADENCE_FORAGER,
    CADENCE_POST,
    CADENCES,
    STATUS_PENDING,
    STATUS_RETIRED,
    STATUS_RUNNING,
    ReturnPacket,
)


def _store(runtime: Any):
    home = getattr(getattr(runtime, "config", None), "home_dir", None)
    return get_hive_store(home)


def _parent_sid(runtime: Any) -> str:
    try:
        from remedy.core.turn_context import turn_session_id

        return str(turn_session_id(runtime) or "")
    except Exception:
        return str(getattr(runtime, "_session_id", "") or "")


def _project(runtime: Any) -> str:
    try:
        return str(runtime.effective_project_path() or "")
    except Exception:
        return ""


def _approval(runtime: Any) -> str:
    try:
        from remedy.core.turn_context import current_turn_approval_mode

        return str(current_turn_approval_mode() or "")
    except Exception:
        return str(getattr(runtime, "_approval_mode", "") or "")


def register_hive_tools(runtime: Any) -> None:
    async def hive_spawn(
        goal: str = "",
        cadence: str = CADENCE_FORAGER,
        budget_steps: float = DEFAULT_BUDGET_STEPS,
        pulse_s: float = 0,
    ) -> str:
        """Hire a daughter. She reports to Remedy, never the owner.

        cadence=forager: one bounded job, then retire.
        cadence=post: standing job (PR2) — rejected until posts are live.
        """
        if hive_depth() >= 1:
            return format_tool_error(
                "Daughters cannot hire. Report a packet to Remedy instead.",
                code="HIVE_DEPTH",
                tool_name="hive_spawn",
            )
        g = str(goal or "").strip()
        if not g:
            return format_tool_error(
                "hive_spawn needs a goal.",
                code="MISSING_GOAL",
                tool_name="hive_spawn",
                suggestion='hive_spawn(goal="review auth.py and report blockers")',
            )
        cad = str(cadence or CADENCE_FORAGER).strip().lower()
        if cad not in CADENCES:
            cad = CADENCE_FORAGER
        if cad == CADENCE_POST:
            return format_tool_error(
                "Standing posts are not hired from this tool yet. Use cadence=forager.",
                code="HIVE_POST_UNAVAILABLE",
                tool_name="hive_spawn",
                suggestion='hive_spawn(goal="…", cadence="forager")',
            )
        store = _store(runtime)
        live = store.live_pulses()
        cap = DEFAULT_LIVE_PULSES
        if len(live) >= cap:
            return format_tool_error(
                f"Hive is at capacity ({cap} live pulses). Collect or retire first.",
                code="HIVE_CAP",
                tool_name="hive_spawn",
            )
        try:
            budget = int(budget_steps or DEFAULT_BUDGET_STEPS)
        except (TypeError, ValueError):
            budget = DEFAULT_BUDGET_STEPS
        budget = max(1, min(MAX_BUDGET_STEPS, budget))
        daughter = store.hire(
            g,
            cadence=cad,
            parent_session_id=_parent_sid(runtime),
            project_path=_project(runtime),
            approval_mode=_approval(runtime),
            budget_steps=budget,
        )
        schedule_forager(runtime, daughter)
        return (
            f"hive_id={daughter.id} cadence={daughter.cadence} status={daughter.status}\n"
            "She reports to you, not the owner. Call hive_collect when you want the packet."
        )

    async def hive_collect(hive_id: str = "") -> str:
        """Read the latest packet. Never dumps the daughter's tool log."""
        hid = str(hive_id or "").strip()
        if not hid:
            return format_tool_error(
                "hive_collect needs hive_id.",
                code="MISSING_ID",
                tool_name="hive_collect",
            )
        store = _store(runtime)
        d = store.get(hid)
        if d is None:
            return format_tool_error(
                f"No daughter {hid}.",
                code="HIVE_NOT_FOUND",
                tool_name="hive_collect",
            )
        if d.status in (STATUS_PENDING, STATUS_RUNNING):
            return f"hive_id={d.id} status={d.status} still running"
        pkt = ReturnPacket.from_dict(d.packet)
        return f"hive_id={d.id} status={d.status}\n{pkt.as_mother_text()}"

    async def hive_status() -> str:
        """Compact roster for this hive — not owner-facing."""
        store = _store(runtime)
        rows = store.list_all()
        if not rows:
            return "hive empty"
        lines = [f"{d.id[:8]} {d.cadence} {d.status} {d.goal[:80]}" for d in rows[:12]]
        return "hive roster\n" + "\n".join(lines)

    async def hive_retire(hive_id: str = "") -> str:
        """End a forager. Cancels if still running."""
        hid = str(hive_id or "").strip()
        if not hid:
            return format_tool_error(
                "hive_retire needs hive_id.",
                code="MISSING_ID",
                tool_name="hive_retire",
            )
        store = _store(runtime)
        d = store.get(hid)
        if d is None:
            return format_tool_error(
                f"No daughter {hid}.",
                code="HIVE_NOT_FOUND",
                tool_name="hive_retire",
            )
        if d.status in (STATUS_PENDING, STATUS_RUNNING):
            from remedy.core.turn_context import abort_session

            abort_session(d.session_id)
            cancel_forager(d.id)
        d.status = STATUS_RETIRED
        store.save(d)
        return f"hive_id={d.id} status=retired"

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "hive_spawn",
        "Hire a silent daughter to cover independent work. She reports a compact "
        "packet to you, never to the owner. cadence=forager (one job then done). "
        "Not a second chat. Daughters cannot hire.",
        hive_spawn,
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the daughter should do"},
                "cadence": {
                    "type": "string",
                    "description": "forager (bounded) or post (standing)",
                },
                "budget_steps": {
                    "type": "integer",
                    "description": "Max ReAct steps for this pulse (1–16)",
                },
            },
            "required": ["goal"],
        },
    )
    reg.register_builtin_handler(
        "hive_collect",
        "Read a daughter's return packet (outcome, evidence pointers, blockers). "
        "Never a transcript.",
        hive_collect,
        {
            "type": "object",
            "properties": {
                "hive_id": {"type": "string"},
            },
            "required": ["hive_id"],
        },
    )
    reg.register_builtin_handler(
        "hive_status",
        "Compact hive roster (id, cadence, status, goal). Internal — do not list "
        "tool names to the owner.",
        hive_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "hive_retire",
        "Retire a daughter. Cancels a running forage.",
        hive_retire,
        {
            "type": "object",
            "properties": {"hive_id": {"type": "string"}},
            "required": ["hive_id"],
        },
    )
