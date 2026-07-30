"""Partner State tools — subgoals, tool_recall, graph, prospective memory."""

from __future__ import annotations

from contextlib import suppress
from typing import Any


def register_partner_state_tools(runtime: Any) -> None:
    """Register Partner State Machine tools on the runtime registry."""

    def _st():
        from remedy.memory.partner_state import ensure_partner_state

        return ensure_partner_state(runtime)

    async def subgoal_open(title: str = "", notes: str = "") -> str:
        st = _st()
        t = (title or "").strip()
        if not t:
            return "Provide a short subgoal title (what you are working on now)."
        sg = st.open_subgoal(t, notes=notes or "")
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if runtime._session_brief is None:
                runtime._session_brief = SessionBrief(
                    session_id=getattr(runtime, "_session_id", None) or ""
                )
            if not runtime._session_brief.intent:
                runtime._session_brief.intent = t[:500]
            runtime._session_brief.touch()
        return (
            f"Opened subgoal {sg.id}: {sg.title}. "
            "Tool results under this subgoal stay protected until subgoal_close."
        )

    async def subgoal_close(
        summary: str = "",
        subgoal_id: str = "",
        status: str = "closed",
    ) -> str:
        st = _st()
        sg = st.close_subgoal(
            subgoal_id or None,
            status=status or "closed",
            summary=summary or "",
        )
        if sg is None:
            return "No open subgoal to close."
        with suppress(Exception):
            from remedy.memory.partner_state.continuity import schedule_continuity_core

            schedule_continuity_core(runtime, use_local=False)
        with suppress(Exception):
            st.fire_prospectives("subgoal_close")
        return (
            f"Closed subgoal {sg.id} ({sg.title}). "
            f"Status={sg.status}. Summary recorded in epistemic graph."
        )

    async def subgoal_status() -> str:
        st = _st()
        pub = st.status_public()
        lines = [
            f"Active: {pub.get('active_subgoal') or '(none)'}",
            f"Open subgoals: {pub.get('open_subgoals')}",
            f"Tool txns: {pub.get('tool_txns')}",
            f"Unverified writes: {pub.get('unverified_writes')}",
            f"Graph nodes: {pub.get('graph_nodes')}",
            f"Prospective armed: {pub.get('prospective_armed')}",
            f"Continuity passes: {pub.get('continuity_passes')}",
        ]
        open_sgs = [s for s in st.subgoals if s.status == "open"]
        for s in open_sgs[-5:]:
            lines.append(f"  · {s.id} {s.title} tools={len(s.tool_call_ids)}")
        return "Partner State:\n" + "\n".join(lines)

    async def tool_recall(txn_id: str = "", tool_call_id: str = "") -> str:
        st = _st()
        if not (txn_id or tool_call_id):
            # List recent txns
            recent = list(st.tool_txns)[-12:]
            if not recent:
                return "No tool transactions recorded yet."
            lines = ["Recent tool transactions (use tool_recall txn_id=…):"]
            for t in recent:
                lines.append(
                    f"- {t.id} {t.name} {t.outcome} "
                    f"artifacts={t.artifacts[:2]} preview={(t.result_preview or '')[:80]}"
                )
            return "\n".join(lines)
        return st.recall_txn_body(txn_id=txn_id, tool_call_id=tool_call_id)

    async def write_set_verify(path: str = "", how: str = "manual") -> str:
        st = _st()
        p = (path or "").strip()
        if not p:
            unverified = st.unverified_writes()
            if not unverified:
                return "Write-set is clean (no unverified writes)."
            lines = ["Unverified writes:"]
            for w in unverified[:20]:
                lines.append(f"- {w.path} via {w.tool} txn={w.txn_id}")
            return "\n".join(lines) + "\nCall write_set_verify(path=…) after re-read/tests."
        ok = st.verify_write(p, how=how or "manual")
        if not ok:
            return f"Path not in write-set: {p}"
        return f"Verified write: {p} ({how or 'manual'})"

    async def memory_fact(
        text: str = "",
        kind: str = "fact",
        why: str = "",
        rejected: str = "",
        path: str = "",
    ) -> str:
        st = _st()
        t = (text or "").strip()
        if not t:
            return "Provide text for the epistemic node."
        k = (kind or "fact").strip().lower()
        if k not in (
            "fact",
            "decision",
            "artifact",
            "commitment",
            "hypothesis",
            "skill_pattern",
            "affordance",
        ):
            k = "fact"
        node = st.add_node(
            kind=k,
            text=t,
            why=why or "",
            rejected=rejected or "",
            path=path or "",
            source="agent",
            confidence=0.9,
        )
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            st.apply_graph_to_brief(brief)
        return f"Recorded {node.kind} node {node.id}: {node.text[:160]}"

    async def remember_later(
        text: str = "",
        trigger: str = "session_start",
        tool_name: str = "",
    ) -> str:
        st = _st()
        t = (text or "").strip()
        if not t:
            return "Provide the reminder text."
        trig = (trigger or "session_start").strip()
        allowed = {
            "session_start",
            "subgoal_close",
            "tool_success",
            "tool_name",
            "project_switch",
            "tests_pass",
            "epoch_roll",
            "manual",
        }
        if trig not in allowed:
            trig = "manual"
        item = st.add_prospective(
            t,
            trigger=trig,
            tool_name=tool_name or "",
            project_path=str(
                getattr(runtime, "_project_path", None)
                or getattr(getattr(runtime, "config", None), "project_path", None)
                or ""
            ),
        )
        return (
            f"Prospective memory armed {item.id}: when {item.trigger}"
            + (f" tool={item.tool_name}" if item.tool_name else "")
            + f" → {item.text}"
        )

    async def partner_state_sync() -> str:
        """Force Continuity Core tick + graph→brief projection."""
        st = _st()
        with suppress(Exception):
            from remedy.memory.partner_state.continuity import schedule_continuity_core

            schedule_continuity_core(runtime, use_local=True, priority=1)
        tick = st.continuity_tick(brief=getattr(runtime, "_session_brief", None))
        return f"Continuity Core tick: {tick}"

    runtime.tool_registry.register_builtin_handler(
        "subgoal_open",
        "Open a Partner State subgoal. Tool results under it stay full until subgoal_close. "
        "Use at the start of a multi-step coding task.",
        subgoal_open,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["title"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "subgoal_close",
        "Close the active (or given) subgoal and seal its span into the epistemic graph. "
        "Call when a multi-step subtask is finished.",
        subgoal_close,
        {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "subgoal_id": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": "closed | parked",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "subgoal_status",
        "Show Partner State: active subgoal, write-set, tool txns, graph, prospective.",
        subgoal_status,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "tool_recall",
        "Recall a prior tool transaction body by txn_id (or list recent txns). "
        "Use when harness offloaded/collapsed output you need again.",
        tool_recall,
        {
            "type": "object",
            "properties": {
                "txn_id": {"type": "string"},
                "tool_call_id": {"type": "string"},
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "write_set_verify",
        "List or mark verified paths in the session write-set after re-read/tests.",
        write_set_verify,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "how": {"type": "string", "description": "e.g. tests, re-read, user"},
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "memory_fact",
        "Record a durable epistemic node (fact/decision/commitment/hypothesis/artifact).",
        memory_fact,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "kind": {
                    "type": "string",
                    "description": "fact|decision|artifact|commitment|hypothesis",
                },
                "why": {"type": "string"},
                "rejected": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["text"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "remember_later",
        "Prospective memory: remind on a future trigger (session_start, subgoal_close, "
        "tool_name, tests_pass, epoch_roll, …).",
        remember_later,
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "trigger": {"type": "string"},
                "tool_name": {"type": "string"},
            },
            "required": ["text"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "partner_state_sync",
        "Run Continuity Core maintenance (graph → Session Brief, decay, local enrich if available).",
        partner_state_sync,
        {"type": "object", "properties": {}},
    )
