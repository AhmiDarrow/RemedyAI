"""PolicyEngine — reproduce today's approval/jail semantics (v0.32 M1.3).

Does not change allow/deny outcomes. Later phases replace the internals;
callers should start depending on ``evaluate`` instead of tool-name lists.

v0.37: ``TrustProfile.AUTONOMOUS`` may skip high-impact asks the same way
``auto`` mode does; owner checkpoints (mail / pay) are never waived.
"""

from __future__ import annotations

from typing import Any

from remedy.policy.decisions import PolicyDecision, ToolRequest
from remedy.tools.descriptor import ToolDescriptor


def _resolve_trust_profile():
    """Read ``trust_profile`` from config; default BALANCED on missing/invalid."""
    from remedy.core.trust_profile import TrustProfile, normalize_trust_profile

    try:
        from remedy.interfaces.api_support import load_config

        cfg = load_config() or {}
        return normalize_trust_profile(cfg.get("trust_profile"))
    except Exception:
        return TrustProfile.BALANCED


def _command_from_args(args: dict[str, Any] | None) -> str:
    """Join host_run argv lists; never str(list) for the dangerous-command check."""
    if not args:
        return ""
    for key in ("command", "cmd"):
        raw = args.get(key)
        if raw:
            return str(raw)
    argv = args.get("argv")
    if isinstance(argv, (list, tuple)):
        return " ".join(str(x) for x in argv if str(x).strip())
    if argv:
        return str(argv)
    for key in ("path", "url"):
        raw = args.get(key)
        if raw:
            return str(raw)
    return ""


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
        command = req.command or _command_from_args(req.arguments)

        from remedy.core.approvals import APPROVALS
        from remedy.core.security import check_dangerous_command

        if desc.name in ("bash_exec", "host_run", "skill_run"):
            raw_argv = req.arguments.get("argv") if req.arguments else None
            if desc.name == "host_run" and isinstance(raw_argv, (list, tuple)) and raw_argv:
                check_argv = [str(x) for x in raw_argv]
            else:
                check_argv = ["bash", "-c", command] if command else [desc.name]
            dangerous = check_dangerous_command(check_argv)
            if dangerous:
                return PolicyDecision(
                    allowed=False,
                    requires_approval=False,
                    reason=str(dangerous),
                    granted_capabilities=frozenset(),
                )

        # Trust profiles live in needs_ask so handler inner-gates match this gate.
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
