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
# Set when authorize_tool allows this task through so handlers do not
# re-ask (mail one-shot would otherwise be consumed twice).
_gate_passed: ContextVar[str | None] = ContextVar("remedy_gate_passed", default=None)
# Command fingerprint authorize_tool used, so the computer inner gate can
# skip a second payment/vault prompt for the same owner moment.
_gate_command: ContextVar[str | None] = ContextVar("remedy_gate_command", default=None)


def gate_already_passed(name: str) -> bool:
    """True when authorize_tool already allowed this tool on this task."""
    return _gate_passed.get() == (name or "").strip()


def gate_command() -> str:
    """Command string authorize_tool fingerprinted for this task (or "")."""
    return (_gate_command.get() or "").strip()


def clear_tool_gate() -> None:
    """Drop the per-task authorize mark (end of turn / after finish_tool)."""
    _gate_passed.set(None)
    _gate_command.set(None)


def _vault_handles_from_args(args: dict[str, Any]) -> list[str]:
    """Opaque vault handles in tool args — never the secret values."""
    with suppress(Exception):
        from remedy.core.vault import token_handles

        blobs: list[str] = []
        for raw in (args or {}).values():
            if raw is None or raw is False:
                continue
            blobs.append(str(raw))
        return token_handles(" ".join(blobs))
    return []


def _tool_command(args: dict[str, Any], name: str = "") -> str:
    """Command string for policy / approval fingerprints (join host_run argv)."""
    n = (name or "").strip()
    if n == "mail_send":
        to_addr = str(args.get("to") or "").strip()
        subj = str(args.get("subject") or "").strip()[:80]
        return f"mail_send to={to_addr} subject={subj}"
    if n == "mail_reply":
        mid = str(args.get("message_id") or "").strip()
        return f"mail_reply to message={mid} chars={len(args.get('body') or '')}"
    if n.startswith("computer_"):
        parts = [n]
        # Typed payloads can be passwords / PANs. Keep vault tokens (opaque)
        # and click/label/url locators; replace other text with a length.
        redact = n in ("computer_type", "computer_act", "computer_fill")
        for key in ("text", "click", "label", "key", "url", "ref", "query", "fields"):
            raw = args.get(key)
            if raw is None or raw is False:
                continue
            text = str(raw).strip()
            if not text:
                continue
            if key == "fields":
                continue
            if redact and key in ("text", "type", "type_text"):
                vaultish = False
                with suppress(Exception):
                    from remedy.core.vault import contains_vault_token

                    vaultish = contains_vault_token(text)
                if vaultish:
                    parts.append(f"{key}={text[:160]}")
                else:
                    parts.append(f"chars={len(text)}")
                continue
            parts.append(f"{key}={text[:160]}")
        handles = _vault_handles_from_args(args)
        if handles:
            note = f"vault={','.join(handles)}"
            if note not in parts:
                parts.append(note)
        return " ".join(parts)
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


def _approval_key(name: str, args: dict[str, Any]) -> str:
    """Discriminating approval fingerprint for tools with no command-shaped arg.

    ``skill_run(skill="notes", script="sync.py")`` and
    ``skill_run(skill="deploy", script="wipe_prod.py")`` must not share one
    approval: the fingerprint has to carry the arguments that decide what runs.
    """
    parts: list[str] = []
    for key in sorted(args or {}):
        val = args[key]
        if isinstance(val, (str, int, float, bool)):
            text = str(val).strip()
            if text:
                parts.append(f"{key}={text[:120]}")
    return f"{name} {' '.join(parts)}" if parts else name


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
    _gate_passed.set(None)
    _gate_command.set(None)
    hive_block = None
    try:
        from remedy.core.hive.policy import hive_depth, is_mother_only_tool
        from remedy.policy.capabilities import Capability as Cap

        if hive_depth() > 0:
            blocked = {
                Cap.CREDENTIAL_USE,
                Cap.TRANSACT,
                Cap.COMMUNICATE,
                Cap.COMPUTER_INPUT,
                Cap.BROWSER_WRITE,
            }
            if is_mother_only_tool(name) or (desc.capabilities & blocked):
                hive_block = format_tool_error(
                    "Hive workers cannot use credentials or transact.",
                    code="HIVE_CAPABILITY",
                    tool_name=name,
                    suggestion="Ask the mother to do this step.",
                )
            if hive_block is None:
                from remedy.core.hive.policy import (
                    DAUGHTER_CAPABILITIES,
                    hive_granted_capabilities,
                    parse_granted_caps,
                )

                granted = hive_granted_capabilities()
                if granted is None:
                    granted = parse_granted_caps(bound_hive_capabilities()) or DAUGHTER_CAPABILITIES
                extra = desc.capabilities - granted
                if extra:
                    hive_block = format_tool_error(
                        "Hive worker is not granted: "
                        + ", ".join(sorted(c.value for c in extra)),
                        code="HIVE_CAPABILITY",
                        tool_name=name,
                        suggestion="Ask the mother to do this step.",
                    )
    except Exception:
        hive_block = None
    if hive_block:
        inc("tool_failure", tool=name)
        return hive_block
    command = _tool_command(args, name)
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
    cmd = command or _approval_key(name, args)
    origin = ""
    if (name or "").startswith("computer_") and runtime is not None:
        with suppress(Exception):
            from remedy.core.agent_computer_tools import _page_context, _page_origin
            from remedy.core.computer.executor import get_computer_executor

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            origin = _page_origin(_page_context(get_computer_executor(home))) or ""
    if decision.requires_approval:
        from remedy.core.approvals import SENSITIVE_PREFIX

        approved = False
        sensitive = (decision.reason or "").startswith(SENSITIVE_PREFIX)
        if sensitive:
            approved = APPROVALS.take_one_shot(
                name, cmd, session_id=sid, origin=origin
            )
        elif APPROVALS.is_approved(name, cmd, session_id=sid):
            approved = True
        if not approved:
            item = APPROVALS.create(
                tool_name=name,
                command=cmd,
                reason=decision.reason,
                session_id=sid,
                origin=origin,
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
                f"command={cmd[:400]}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry {name} with "
                "the same arguments."
            )
    _gate_passed.set(name)
    _gate_command.set(cmd)
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
    clear_tool_gate()
    return text


def bound_hive_capabilities() -> list[str]:
    """Caps written to the daughter journal and enforced on her tools."""
    from remedy.core.hive.policy import DAUGHTER_CAPABILITIES

    return sorted(c.value for c in DAUGHTER_CAPABILITIES)
