"""Live turn adapter — wires v0.32–0.38 contracts into ReAct without a rewrite."""

from __future__ import annotations

from contextlib import suppress
from contextvars import ContextVar
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.execution.action import ActionRecord

# Per-asyncio-task record so finish_tool resumes the same action authorize opened.
# Never written into tool arguments — models and tests must not see it.
_current_action: ContextVar[ActionRecord | None] = ContextVar(
    "remedy_current_action", default=None
)


def snapshot_live_turn(runtime: Any = None) -> None:
    """Freeze TurnContext + emit GoalStarted. Safe no-op if factory fails."""
    with suppress(Exception):
        from remedy.core.context import TurnFactory
        from remedy.core.optimization_telemetry import inc

        scope = "project"
        if runtime is not None:
            with suppress(Exception):
                scope = str(runtime.access_scope() or "project")
        TurnFactory.create(access_scope=scope, runtime=runtime)
        inc("turn")


def authorize_tool(runtime: Any, name: str, args: dict[str, Any]) -> str | None:
    """PolicyEngine gate. None = run the handler. Else a tool-result string."""
    from remedy.core.approvals import APPROVALS
    from remedy.core.context import TurnFactory
    from remedy.core.optimization_telemetry import inc
    from remedy.core.turn_context import turn_session_id
    from remedy.events import EventType, default_bus
    from remedy.execution.action import ActionRecord, ActionState
    from remedy.policy.decisions import ToolRequest
    from remedy.policy.engine import PolicyEngine
    from remedy.tools.catalog import descriptor_for

    desc = descriptor_for(name)
    args.pop("_action_id", None)
    hive_block = None
    with suppress(Exception):
        from remedy.core.hive.policy import hive_depth
        from remedy.policy.capabilities import Capability as Cap

        if hive_depth() > 0 and (desc.capabilities & {Cap.CREDENTIAL_USE, Cap.TRANSACT}):
            hive_block = format_tool_error(
                "Hive workers cannot use credentials or transact.",
                code="HIVE_CAPABILITY",
                tool_name=name,
                suggestion="Ask the mother to do this step.",
            )
    if hive_block:
        inc("tool_failure", tool=name)
        return hive_block
    command = str(
        args.get("command")
        or args.get("cmd")
        or args.get("path")
        or args.get("url")
        or ""
    )
    ctx = None
    with suppress(Exception):
        ctx = TurnFactory.create(
            access_scope=str(getattr(runtime, "access_scope", lambda: "project")()),
            runtime=runtime,
            emit_start=False,
        )
    decision = PolicyEngine().evaluate(
        ctx, desc, ToolRequest(name=name, arguments=args, command=command)
    )
    if not decision.allowed:
        inc("tool_failure", tool=name)
        return format_tool_error(
            decision.reason or "denied by policy",
            code="POLICY_DENIED",
            tool_name=name,
            suggestion="Use a tool and arguments the owner has authorized.",
        )
    sid = turn_session_id(runtime)
    if decision.requires_approval and not APPROVALS.is_approved(
        name, command, session_id=sid
    ):
        item = APPROVALS.create(
            tool_name=name,
            command=command or name,
            reason=decision.reason,
            session_id=sid,
        )
        with suppress(Exception):
            if ctx is not None:
                default_bus().emit_simple(
                    EventType.APPROVAL_REQUESTED,
                    session_id=ctx.session_id,
                    turn_id=ctx.turn_id,
                    tool=name,
                    reason=decision.reason,
                )
        return (
            f"APPROVAL_REQUIRED id={item.id}\n"
            f"reason={decision.reason}\n"
            f"command={(command or name)[:400]}\n"
            "Do not invent success. Tell the user this needs approval in the UI "
            f"(or /approve {item.id}). After they approve, retry {name} with "
            "the same arguments."
        )
    with suppress(Exception):
        rec = ActionRecord(tool=name)
        rec.advance(ActionState.AUTHORIZED)
        rec.advance(ActionState.RUNNING)
        _current_action.set(rec)
    with suppress(Exception):
        if ctx is not None:
            default_bus().emit_simple(
                EventType.TOOL_STARTED,
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                tool=name,
            )
    inc("tool_call", tool=name)
    return None


def finish_tool(
    runtime: Any,
    name: str,
    args: dict[str, Any],
    content: str,
    *,
    ok: bool,
) -> str:
    """Verify, clip, provenance, events. Never swallow the model-visible body."""
    from remedy.core.context_budget import ContextBudget, clip_tool_result
    from remedy.core.optimization_telemetry import inc
    from remedy.core.react_policy import TOOL_RESULT_CHAR_CAP
    from remedy.events import EventType, default_bus
    from remedy.execution.action import ActionRecord, ActionState
    from remedy.tools.catalog import descriptor_for
    from remedy.verification.evidence import (
        ActionResult,
        VerificationResult,
        VerificationStatus,
    )
    from remedy.verification.verifier import verify_action

    text = content or ""
    args.pop("_action_id", None)
    desc = descriptor_for(name)
    path = str(args.get("path") or "")
    vr = VerificationResult(
        status=VerificationStatus.NOT_REQUIRED,
        reason="no verification policy for this tool",
    )
    if desc.requires_verification:
        vr = verify_action(
            ActionResult(
                tool=name,
                ok=ok,
                exit_code=0 if ok else 1,
                stdout=text[:2000],
                path=path,
                extra={"status": int(args.get("status") or 0)},
            )
        )
        with suppress(Exception):
            from remedy.core.context import TurnFactory

            ctx = TurnFactory.create(runtime=runtime, emit_start=False)
            default_bus().emit_simple(
                EventType.VERIFICATION_COMPLETED,
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                tool=name,
                reason=vr.reason,
                status=vr.status.value,
            )
        if vr.status == VerificationStatus.FAIL:
            inc("verification_failure", tool=name)
            text = (
                f"{text}\n[verification FAIL: {vr.reason}]"
                if text
                else f"[verification FAIL: {vr.reason}]"
            )
            ok = False
    cap = int(TOOL_RESULT_CHAR_CAP or 0)
    if cap > 0 and len(text) > cap:
        text = clip_tool_result(text, ContextBudget(tool_result_chars=cap))
    if name in ("web_fetch", "web_search"):
        with suppress(Exception):
            from remedy.core.turn_context import current_turn_id, turn_session_id
            from remedy.memory.provenance import ingest_web_text

            ingest_web_text(
                text[:800],
                session_id=str(turn_session_id(runtime) or ""),
                turn_id=str(current_turn_id() or ""),
            )
    with suppress(Exception):
        rec = _current_action.get()
        if rec is None or rec.tool != name:
            rec = ActionRecord(tool=name)
            rec.advance(ActionState.AUTHORIZED)
            rec.advance(ActionState.RUNNING)
        rec.advance(ActionState.RESULT)
        rec.advance(ActionState.VERIFYING)
        rec.verification = vr
        if vr.status == VerificationStatus.FAIL:
            rec.advance(ActionState.FAILED)
        else:
            rec.advance(ActionState.VERIFIED)
            rec.advance(ActionState.COMPLETED)
        from remedy.core.context import TurnFactory

        ctx = TurnFactory.create(runtime=runtime, emit_start=False)
        default_bus().emit_simple(
            EventType.TOOL_COMPLETED if ok else EventType.TOOL_FAILED,
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
            tool=name,
            ok=ok,
        )
        _current_action.set(None)
    return text


def bound_hive_capabilities() -> list[str]:
    """Daughters get parent caps minus credential.use / transact."""
    from remedy.core.hive_caps import child_capabilities
    from remedy.policy.capabilities import Capability

    parent = frozenset(Capability)
    requested = parent - {Capability.CREDENTIAL_USE, Capability.TRANSACT}
    child = child_capabilities(parent, requested)
    return sorted(c.value for c in child)
