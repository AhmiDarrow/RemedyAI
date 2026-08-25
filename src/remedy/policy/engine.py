"""PolicyEngine — reproduce today's approval/jail semantics (v0.32 M1.3).

Does not change allow/deny outcomes. Later phases replace the internals;
callers should start depending on ``evaluate`` instead of tool-name lists.
"""

from __future__ import annotations

from typing import Any

from remedy.policy.decisions import PolicyDecision, ToolRequest
from remedy.tools.descriptor import ToolDescriptor


class PolicyEngine:
    """Deterministic gate: TurnContext + descriptor + request → decision."""

    def evaluate(
        self,
        ctx: Any,
        tool: ToolDescriptor | str,
        request: ToolRequest | None = None,
    ) -> PolicyDecision:
        if isinstance(tool, ToolDescriptor):
            desc = tool
        else:
            from remedy.tools.catalog import descriptor_for

            desc = descriptor_for(str(tool))
        req = request or ToolRequest(name=desc.name)
        command = req.command
        if not command:
            args = req.arguments
            command = str(
                args.get("command")
                or args.get("cmd")
                or args.get("argv")
                or args.get("path")
                or ""
            )

        from remedy.core.approvals import APPROVALS
        from remedy.core.security import check_dangerous_command

        if desc.name in ("bash_exec", "host_run", "skill_run"):
            argv = ["bash", "-c", command] if command else [desc.name]
            dangerous = check_dangerous_command(argv)
            if dangerous:
                return PolicyDecision(
                    allowed=False,
                    requires_approval=False,
                    reason=str(dangerous),
                    granted_capabilities=frozenset(),
                )

        reason = APPROVALS.needs_ask(command, tool_name=desc.name)
        if reason:
            decision = PolicyDecision(
                allowed=True,
                requires_approval=True,
                reason=reason,
                granted_capabilities=desc.capabilities,
            )
            _emit_policy(ctx, desc.name, decision)
            return decision
        decision = PolicyDecision(
            allowed=True,
            requires_approval=False,
            reason="allowed",
            granted_capabilities=desc.capabilities,
        )
        _emit_policy(ctx, desc.name, decision)
        return decision


def _emit_policy(ctx: Any, tool_name: str, decision: PolicyDecision) -> None:
    if ctx is None:
        return
    try:
        from remedy.events import EventType, default_bus

        sid = str(getattr(ctx, "session_id", "") or "")
        tid = str(getattr(ctx, "turn_id", "") or "")
        if not tid:
            return
        kind = (
            EventType.APPROVAL_REQUESTED
            if decision.requires_approval
            else EventType.TOOL_PROPOSED
        )
        default_bus().emit_simple(
            kind,
            session_id=sid,
            turn_id=tid,
            tool=tool_name,
            allowed=decision.allowed,
            reason=decision.reason,
        )
    except Exception:
        return
