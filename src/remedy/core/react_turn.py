"""ReAct turn control — shared state, tool arming, synthesis, phases.

Extracted from ``call_llm_stream`` so arming/recovery rules are testable and
the main loop can call pure helpers instead of re-encoding policy inline.

Addresses deep-dive items:
  1) shared TurnState
  2) single resolve_tools decision
  3) stale-epoch defaults (import REACT_MAX_STALE_EPOCHS)
  5) multi-shot pseudo recovery caps
  7) tools_armed = schemas actually sent
  8) synthesize_from_tools fallback
  9) local tools-per-step cap
  10) RESEARCH → PLAN → BUILD phase tracking
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from remedy.core.react_policy import REACT_MAX_STALE_EPOCHS

logger = logging.getLogger(__name__)

# Pseudo-tool recoveries allowed per turn (local models re-emit JSON often).
MAX_PSEUDO_RECOVERIES = 4
# Disconnect / transient transport retries (non-stream re-POST).
# Partner path: WinError 64 / mid-load drops are common on local RMB — retry hard.
MAX_DISCONNECT_RETRIES = 8
# Local: max tool schemas per model step after write-first pack.
LOCAL_MAX_TOOLS_PER_STEP = 8
# Cloud work pack. A 194-schema dump (33k→61k prompt) is never the live
# round. Tight pack (xAI / Grok window) vs richer pack (Claude / GPT /
# DeepSeek / Gemini / OpenRouter-non-Grok). Recall stays in the core so
# every provider can search memory while operating.
WORK_MAX_TOOLS_PER_STEP = 34
WORK_MAX_TOOLS_CLOUD = 64
# Prefer these when capping — first-N used to drop host_run behind help/goal.
_OPERATE_CORE_TOOLS = (
    "file_read",
    "file_write",
    "file_edit",
    "file_edit_batch",
    "list_dir",
    "file_glob",
    "repo_search",
    "memory_search",
    "soul_recall",
    "host_run",
    "host_mkdir",
    "host_which",
    "bash_exec",
    "todo_write",
    "todo_read",
    "mission_start",
    "mission_update",
    "mission_verify",
    "git_status",
    "skill_activate",
    "computer_navigate",
    "computer_snapshot",
    "computer_click",
    "computer_type",
    "computer_act",
    "computer_key",
    "computer_fill",
    "computer_scroll",
    "computer_wait",
    "computer_select",
    "computer_hover",
    "apply_patch",
    "web_search",
    "web_fetch",
    # After the tight pack — Claude / GPT / DeepSeek / Gemini keep these.
    "computer_press_hold",
    "computer_drag",
    "computer_screenshot",
    "computer_page_text",
    "computer_app",
    "computer_find",
    "computer_windows",
    "vault_list",
    "host_script",
)
_OPERATE_DEFER_TOOLS = frozenset(
    {
        "help_list",
        "help_read",
        "goal_add",
        "goal_list",
        "goal_complete",
        "goal_verify",
        "goal_set_next",
        "goal_drive",
        "goal_clear_all",
        "companion_context",
        "companion_design",
    }
)

_EXPLORE_TOOLS = frozenset(
    {"file_read", "list_dir", "repo_search", "memory_search", "soul_recall", "web_search", "web_fetch"}
)
_WRITE_TOOLS = frozenset({"file_write", "file_edit", "file_edit_batch", "apply_patch"})
_VERIFY_TOOLS = frozenset(
    {
        "bash_exec",
        "shell_exec",
        "job_run",
        "mission_verify",
        "mission_update",
        "godot_run",
        "godot_check",
        "analysis_run",
        "analysis_ledger",
        "cite_check",
        "manuscript_build",
        "manuscript_check",
    }
)


@dataclass
class ToolsDecision:
    """Result of resolve_tools — single source of truth for the step/turn."""

    tools: list[dict[str, Any]] | None
    run_until_done: bool
    reason: str
    pack: str = "full"  # full | write_first | plan | none


@dataclass
class TurnState:
    """Mutable control plane for one ``call_llm_stream`` invocation."""

    message: str = ""
    session_id: str = ""
    plan_mode: bool = False
    all_tools: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    run_until_done: bool = False
    arm_reason: str = ""
    pack: str = "none"
    # Counters
    tools_executed: int = 0
    tool_batches: int = 0
    pseudo_recoveries: int = 0
    disconnect_retries: int = 0
    # Armed-ceiling forcing fires at most once; a second decline is a signal.
    armed_ceiling_fired: bool = False
    intent_declined_recorded: bool = False
    # Task loop phases (product language)
    phase: str = "idle"  # idle | research | plan | build | done
    research_batches: int = 0
    plan_seen: bool = False
    write_batches: int = 0
    verify_batches: int = 0
    paths_written: list[str] = field(default_factory=list)
    # Inject budget (avoid user-role inject spam)
    inject_count: int = 0
    max_injects: int = 24
    # Per-turn isolation (must not live on shared runtime under parallel tabs)
    fingerprint_loop_hits: int = 0
    evidence_inject_eu: int = -1
    mission_gate_nudge_done: bool = False
    # After a mid-SSE RST (WinError 64 / TransferEncodingError) keep the rest
    # of the turn on a single JSON POST. Re-enabling stream on the next step
    # is what made xAI drop three times in one Quickcast turn (2026-08-27).
    force_nonstream: bool = False

    def tools_armed(self) -> bool:
        """True when tool schemas will actually be sent this step."""
        return bool(self.tools)

    def can_rearm(self) -> bool:
        return bool(self.all_tools)

    def rearm(self, *, reason: str = "rearm") -> None:
        if not self.all_tools:
            return
        chat_pin = False
        with suppress(Exception):
            from remedy.core.turn_context import current_chat_mode, turn_has_attachments

            chat_pin = bool(current_chat_mode()) and not bool(turn_has_attachments())
        if chat_pin:
            self.tools = None
            self.run_until_done = False
            self.arm_reason = "chat_mode"
            logger.info("react_tools skip rearm reason=chat_mode")
            return
        if self.plan_mode:
            # Never restore file_write / bash_exec into a Plan turn.
            try:
                from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

                self.tools = [
                    t
                    for t in self.all_tools
                    if ((t.get("function") or {}).get("name") or "")
                    in PLAN_MODE_TOOL_NAMES
                ] or None
            except Exception:
                self.tools = None
            self.run_until_done = False
            self.arm_reason = reason
            logger.info("react_tools rearm plan_mode reason=%s", reason)
            return
        packed = cap_tools_for_step(
            list(self.all_tools),
            local=False,
            max_tools=work_max_tools_for_step(local=False),
        )
        self.tools = list(packed or self.all_tools)
        self.run_until_done = True
        self.arm_reason = reason
        logger.info("react_tools rearm reason=%s count=%d", reason, len(self.tools))

    def disarm(self, *, reason: str = "disarm") -> None:
        self.tools = None
        self.run_until_done = False
        self.arm_reason = reason
        logger.info("react_tools disarm reason=%s", reason)

    def record_tool_batch(
        self,
        names: list[str],
        *,
        paths: list[str] | None = None,
    ) -> None:
        self.tool_batches += 1
        self.tools_executed += len(names)
        explore = bool(names) and all(n in _EXPLORE_TOOLS for n in names)
        wrote = any(n in _WRITE_TOOLS for n in names)
        verified = any(n in _VERIFY_TOOLS for n in names)
        if explore:
            self.research_batches += 1
            if self.phase in ("idle", "research"):
                self.phase = "research"
        if wrote:
            self.write_batches += 1
            self.phase = "build"
            for p in paths or []:
                if p and p not in self.paths_written:
                    self.paths_written.append(p)
        if verified:
            self.verify_batches += 1
            if self.phase != "done":
                self.phase = "build"
        # Plan: mission_start / brief update often named in tools
        if any(n in ("mission_start", "mission_update", "plan_save") for n in names):
            self.plan_seen = True
            if self.phase == "research":
                self.phase = "plan"

    def mark_plan_from_nudge(self) -> None:
        self.plan_seen = True
        if self.phase in ("idle", "research"):
            self.phase = "plan"

    def phase_nudge(self) -> dict[str, str] | None:
        """User inject if task turn skips research/plan (once each)."""
        if not self.run_until_done or self.phase == "idle":
            return None
        if self.tool_batches == 0:
            return None
        # After some research, push plan then build
        if (
            self.research_batches >= 1
            and not self.plan_seen
            and self.write_batches == 0
            and self.inject_count < self.max_injects
        ):
            self.plan_seen = True
            self.phase = "plan"
            self.inject_count += 1
            return {
                "role": "user",
                "content": (
                    "[Task loop · PLAN] Research received. Set a short checklist "
                    "(mission_start or state open steps), then BUILD with "
                    "file_write/file_edit — do not only describe the plan."
                ),
            }
        if (
            self.research_batches >= 2
            and self.write_batches == 0
            and self.inject_count < self.max_injects
        ):
            self.inject_count += 1
            self.phase = "build"
            return {
                "role": "user",
                "content": (
                    "[Task loop · BUILD] Enough RESEARCH. Implement now with "
                    "file_write/file_edit, then verify. No more explore-only steps."
                ),
            }
        return None

    def allow_pseudo_recovery(self) -> bool:
        return self.pseudo_recoveries < MAX_PSEUDO_RECOVERIES

    def note_pseudo_recovery(self) -> None:
        self.pseudo_recoveries += 1

    def allow_disconnect_retry(self) -> bool:
        return self.disconnect_retries < MAX_DISCONNECT_RETRIES

    def note_disconnect_retry(self) -> None:
        self.disconnect_retries += 1
        self.force_nonstream = True

    def note_fingerprint_loop(self) -> int:
        """Increment fingerprint-loop hits; return new count."""
        self.fingerprint_loop_hits += 1
        return self.fingerprint_loop_hits


def soft_api_recovery_action(
    *,
    force_answer_api_fail_once: bool,
    force_answer_sticky: bool,
    api_soft_failures: int,
    max_api_soft_failures: int = 3,
    keep_tools: bool = False,
) -> str:
    """Decide next step after a non-fatal LLM HTTP error.

    Returns:
      ``stop`` — hard stop with user-facing error
      ``retry_with_tools`` — same request shape, tools stay on
      ``force_answer_rebuild`` — rebuild no-tool body and POST again

    Unfinished work must retry with tools. Inventing an answer from
    context after a 400 is a silent drop, not recovery.
    """
    if force_answer_api_fail_once:
        return "stop"
    # Sticky means we already rebuilt for a force-answer attempt.
    if force_answer_sticky:
        return "stop"
    if int(api_soft_failures) < int(max_api_soft_failures):
        return "retry_with_tools" if keep_tools else "force_answer_rebuild"
    return "stop"


# Read-only tools an ambiguous (non-work-verdict) turn may still use to peek
# before asking. No shell, no writes, no computer input — misclassification
# in either direction stays cheap.
AMBIGUOUS_READONLY_TOOLS = frozenset(
    {
        "file_read",
        "list_dir",
        "file_glob",
        "repo_search",
        "memory_search",
        "skill_search",
        "todo_read",
        "help_list",
        "help_read",
    }
)


def _readonly_peek_tools(
    all_tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    picked = [
        t
        for t in (all_tools or [])
        if ((t.get("function") or {}).get("name") or "") in AMBIGUOUS_READONLY_TOOLS
    ]
    return picked or None


def resolve_tools(
    *,
    message: str,
    all_tools: list[dict[str, Any]] | None,
    plan_mode: bool = False,
    turn_tier: int = 1,
    open_tasks: list[str] | None = None,
    history: list[dict[str, Any]] | None = None,
    pure_action_kick: bool = False,
    clear_goals_only: bool = False,
    browse_pre_url: str | None = None,
    page_interaction: bool = False,
    open_only_browse: bool = False,
    build_active: bool = False,
    step_index: int = 0,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    writes_done: int = 0,
    has_attachments: bool | None = None,
) -> ToolsDecision:
    """Single pure decision: which tool schemas to send this turn/step."""
    all_t = list(all_tools or [])
    if not all_t:
        return ToolsDecision(None, False, "no_tools_registered", pack="none")

    has_att = bool(has_attachments) if has_attachments is not None else False
    if has_attachments is None:
        with suppress(Exception):
            from remedy.core.turn_context import turn_has_attachments

            has_att = bool(turn_has_attachments())

    chat_pin = False
    with suppress(Exception):
        from remedy.core.turn_context import current_chat_mode

        chat_pin = bool(current_chat_mode())
    # Chat pin yields to files they just handed over — don't blind companion tools.
    if chat_pin and not has_att:
        logger.info("react_tools disarm reason=chat_mode")
        return ToolsDecision(None, False, "chat_mode", pack="none")

    if plan_mode:
        try:
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            plan_tools = [
                t
                for t in all_t
                if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
            ]
            return ToolsDecision(
                plan_tools or None, False, "plan_mode", pack="plan"
            )
        except Exception:
            return ToolsDecision(all_t, False, "plan_mode_fallback", pack="full")

    with suppress(Exception):
        from remedy.core.self_inject_draft import (
            INTERNAL_IMPROVE_TOOLS,
            in_internal_improve,
        )

        if in_internal_improve():
            improve_tools = [
                t
                for t in all_t
                if ((t.get("function") or {}).get("name") or "")
                in INTERNAL_IMPROVE_TOOLS
            ]
            return ToolsDecision(
                improve_tools or None, True, "internal_improve", pack="self_fix"
            )

    # Only proven trivia / pasted markup strip tools. Knowledge questions
    # ("why is this failing?") stay armed — the host acts.
    with suppress(Exception):
        from remedy.core.react_policy import is_pure_trivia_message

        if is_pure_trivia_message(message or ""):
            logger.info("react_tools disarm reason=non_work")
            return ToolsDecision(None, False, "non_work", pack="none")

    # Do not pick a tool pack from lexical classifiers (``shipping``, ``goal``,
    # ``this week``, …). That stripped file/host tools on coding asks that
    # merely mentioned a life-shaped word. Memory may still tag life vs code;
    # arming stays full unless an explicit mode (Plan) or proven chat-only.

    # Page interaction / browse full agency
    if page_interaction:
        return ToolsDecision(all_t, True, "page_interaction", pack="full")

    # Bare greetings/acks never inherit leftover build_active or open_tasks.
    # "Hi keep going" is not chat-only (action-kick) and stays armed below.
    # Attachments are a request to look — do not treat a blank caption as Hi.
    # A 'yes' after Remedy's own "want me to add X?" is a continue, not chat.
    offered_confirm = False
    with suppress(Exception):
        from remedy.core.react_policy import confirmation_continues_offered_work

        offered_confirm = confirmation_continues_offered_work(
            message or "", history
        )
    with suppress(Exception):
        from remedy.core.react_policy import is_chat_only_message

        if (
            is_chat_only_message(message or "")
            and not has_att
            and not offered_confirm
        ):
            logger.info("react_tools disarm reason=l1_pure_chat")
            return ToolsDecision(None, False, "l1_pure_chat", pack="none")

    # Presence/feeling is an answer, not leftover Build tools. Peek + BUILD
    # mode made local RMB emit empty bash_exec/host_run on "how does a local
    # model feel?". Other knowledge questions still get the read-only peek.
    with suppress(Exception):
        from remedy.core.react_policy import is_feeling_presence_question

        if is_feeling_presence_question(message or "") and not has_att:
            logger.info("react_tools disarm reason=knowledge")
            return ToolsDecision(None, False, "knowledge", pack="none")

    msg_wants = False
    with suppress(Exception):
        from remedy.core.react_policy import _message_wants_tools

        msg_wants = bool(_message_wants_tools(message or ""))

    # Build packing (write-first schemas) may still consult the task detector
    # *after* work is already decided. Arm from regex build verbs (godot /
    # create app) so ability is not stripped; do not arm from length/fence
    # alone or from social "make me laugh".
    task_like = False
    with suppress(Exception):
        from remedy.core.build_engine import _BUILD_RE, looks_like_task_request

        raw = message or ""
        task_like = bool(looks_like_task_request(raw))
        m = _BUILD_RE.search(raw)
        if m:
            hit = m.group(0).lower()
            if re.fullmatch(r"make\s+(it|me)", hit):
                rest = raw[: m.start()] + raw[m.end() :]
                if _BUILD_RE.search(rest):
                    msg_wants = True
            else:
                msg_wants = True

    # Learned per-partner intent (arm-only): teaches toward the regex verdict
    # every turn and may override False→True once confidently trained. It can
    # never disarm — the regex layer stays the floor.
    if step_index == 0:
        with suppress(Exception):
            from remedy.core.intent_learn import consult

            msg_wants = bool(consult(message or "", regex_verdict=bool(msg_wants)))

    if has_att:
        msg_wants = True
    if offered_confirm:
        msg_wants = True

    local = False
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        local = is_local_binding(provider, model, base_url)

    if msg_wants:
        tools = all_t
        pack = "full"
        if build_active:
            reason = "build_active"
        elif has_att and not task_like:
            reason = "attachments"
        elif task_like:
            reason = "task"
        else:
            reason = "message_wants_tools"
        if local and step_index <= 1 and writes_done <= 0 and task_like:
            with suppress(Exception):
                from remedy.core.local_agent_optimize import filter_tools_write_first

                filtered = filter_tools_write_first(
                    tools, user_message=message or "", step_index=step_index
                )
                if filtered:
                    tools = filtered
                    pack = "write_first"
                    reason = "task_write_first"
        cap_n = work_max_tools_for_step(
            local=local, provider=provider or "", model=model or ""
        )
        if tools and len(tools) > cap_n:
            capped = cap_tools_for_step(tools, local=local, max_tools=cap_n)
            if capped is not None:
                tools = capped
            reason = reason + ("+local_cap" if local else "+work_cap")
        logger.info(
            "react_tools arm reason=%s pack=%s count=%d local=%s step=%d",
            reason,
            pack,
            len(tools or []),
            local,
            step_index,
        )
        return ToolsDecision(tools, True, reason, pack=pack)

    # Pure action kicks: limited agency
    if pure_action_kick or clear_goals_only or open_only_browse:
        return ToolsDecision(
            all_t if (open_only_browse or clear_goals_only) else all_t,
            bool(open_only_browse or clear_goals_only or browse_pre_url),
            "browse_or_kick",
            pack="full",
        )

    # Proven non-work (verbal token / trivia / pasted tool markup) never
    # inherits tools from continuity rebound. "Reply only STILLALIVE" was
    # falling through to default_armed → local RMB tool-looped for minutes.
    with suppress(Exception):
        from remedy.core.react_policy import is_pure_trivia_message

        if is_pure_trivia_message(message or ""):
            logger.info("react_tools disarm reason=non_work")
            return ToolsDecision(None, False, "non_work", pack="none")

    # Ambiguous + leftover work: ask, don't assume they want tools or silence.
    open_work = bool(open_tasks) or bool(build_active)
    if not open_work and history:
        with suppress(Exception):
            from remedy.core.react_policy import history_suggests_open_work

            open_work = history_suggests_open_work(
                history, open_tasks=open_tasks or None
            )
    # Ambiguous middle: keep a small read-only peek pack instead of stripping
    # everything. The model may still just answer (run_until_done stays off,
    # the ceiling never forces these), but a misread work ask isn't blind —
    # it can look before asking. Strictly added ability.
    peek = _readonly_peek_tools(all_t)
    if open_work:
        ack = False
        knowledge = False
        with suppress(Exception):
            from remedy.core.react_policy import (
                is_ambiguous_ack_message,
                is_knowledge_question,
            )

            ack = is_ambiguous_ack_message(message or "")
            knowledge = is_knowledge_question(message or "")
        # Long session: leftover work + a real follow-up is a continue.
        # Only short acks / knowledge questions still ask first.
        # Live 2026-08-26: "ok on to assets" / "fix the movement issues"
        # became "are we just talking?" and killed the build.
        if not ack and not knowledge:
            packed = cap_tools_for_step(
                all_t,
                local=local,
                max_tools=work_max_tools_for_step(
                    local=local, provider=provider or "", model=model or ""
                ),
            )
            logger.info(
                "react_tools arm reason=open_work_continue pack=full count=%d",
                len(packed or []),
            )
            return ToolsDecision(
                packed or all_t, True, "open_work_continue", pack="full"
            )
        # One peek round, then the one question. Extra peek steps were the
        # 2026-08-27 Firefox turn: 18 file_reads, no writes, minutes of "loop".
        if int(step_index or 0) > 0:
            logger.info("react_tools disarm reason=ask_first peek_spent")
            return ToolsDecision(None, False, "ask_first", pack="none")
        logger.info("react_tools soft-disarm reason=ask_first pack=peek")
        return ToolsDecision(peek, False, "ask_first", pack="peek" if peek else "none")

    logger.info("react_tools soft-disarm reason=no_work_request pack=peek")
    return ToolsDecision(
        peek, False, "no_work_request", pack="peek" if peek else "none"
    )


def apply_tools_decision(state: TurnState, decision: ToolsDecision) -> None:
    state.tools = decision.tools
    state.run_until_done = decision.run_until_done
    state.arm_reason = decision.reason
    state.pack = str(getattr(decision, "pack", "") or "none")
    if decision.run_until_done and state.phase == "idle":
        state.phase = "research"


def effective_stale_epochs(runtime: Any = None) -> int:
    """Single source of truth for stale-epoch pause (default 8, not 2)."""
    try:
        v = int(getattr(runtime, "_react_max_stale_epochs", 0) or 0)
        if v > 0:
            return max(1, v)
    except (TypeError, ValueError):
        pass
    return max(1, int(REACT_MAX_STALE_EPOCHS))


def synthesize_from_tools(
    messages: list[dict[str, Any]] | None,
    *,
    paths_written: list[str] | None = None,
    max_chars: int = 2_400,
) -> str:
    """Deterministic user-facing summary when the model dies after tools."""
    msgs = list(messages or [])
    paths = list(paths_written or [])
    tool_lines: list[str] = []
    errors: list[str] = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        name = str(m.get("name") or "tool")
        content = str(m.get("content") or "")
        preview = content.strip().replace("\n", " ")
        if len(preview) > 220:
            preview = preview[:217] + "…"
        low = content.lower()
        if "error" in low or "failed" in low or "path_denied" in low:
            errors.append(f"- **{name}**: {preview}")
        elif preview:
            tool_lines.append(f"- **{name}**: {preview}")
        # harvest paths from content
        for mpath in re.finditer(
            r"(?i)(?:wrote|written|saved|path[=:]\s*)([A-Za-z]:[\\/][^\s\"']+\.\w+|/[\w./-]+\.\w+)",
            content,
        ):
            p = mpath.group(1)
            if p not in paths:
                paths.append(p)

    parts: list[str] = []
    if paths:
        parts.append("**Files touched**")
        for p in paths[-12:]:
            parts.append(f"- `{p}`")
    if tool_lines:
        parts.append("**Tool results**")
        parts.extend(tool_lines[-10:])
    if errors:
        parts.append("**Issues**")
        parts.extend(errors[-8:])
        parts.append(
            "Issues above will be addressed on the next tool steps automatically."
        )
    if not parts:
        parts.append(
            "Tools ran this turn but the model stream stopped before a summary. "
            "History is intact; the next step will resume the build."
        )
    else:
        parts.insert(
            0,
            "Tool work completed (model stream cut mid-summary). What landed:",
        )
    text = "\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…(truncated)"
    return text


def is_connect_refused_error(exc: BaseException | str) -> bool:
    """True when nothing is listening — not a mid-stream RST.

    Live 2026-08-26: 'review project' hit 127.0.0.1:8787 refused, then eight
    'disconnect' retries + RMB waits instead of saying the local model is off.
    """
    s = str(exc or "").lower()
    return any(
        x in s
        for x in (
            "actively refused",
            "connection refused",
            "winerror 10061",
            "errno 111",
            "errno 61",
            "10061",
        )
    ) or (
        "cannot connect" in s
        and ("refused" in s or "8787" in s or "127.0.0.1" in s)
    )


def is_disconnect_error(exc: BaseException | str) -> bool:
    s = str(exc or "").lower()
    return any(
        x in s
        for x in (
            "server disconnected",
            "connection reset",
            "connection closed",
            "clientconnectorerror",
            "cannot connect",
            "transferencodingerror",
            "clientpayloaderror",
            "payload is not completed",
            "not enough data",
            "connection aborted",
            "broken pipe",
            "network name is no longer available",
            "winerror 64",
            "winerror 10054",
            "winerror 10053",
            "winerror 10061",  # connection refused (RMB down)
            "actively refused",
            "connectionerror",
            "timeout",
            "timed out",
            "network error",
            "failed to fetch",
            "err_connection",
        )
    )


def _tool_schema_name(t: dict[str, Any]) -> str:
    fn = t.get("function") if isinstance(t.get("function"), dict) else {}
    return str((fn or {}).get("name") or t.get("name") or "")


def work_max_tools_for_step(
    *,
    local: bool = False,
    provider: str = "",
    model: str = "",
) -> int:
    """Per-provider operate cap. Local stays tight; xAI/Grok uses the
    tight cloud pack; Claude / GPT / DeepSeek / Gemini / OpenRouter-non-Grok
    get the richer pack. Never 194 schemas.
    """
    if local:
        return LOCAL_MAX_TOOLS_PER_STEP
    p = str(provider or "").strip().lower()
    m = str(model or "").strip().lower()
    if not p and not m:
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding

            bind = get_llm_binding()
            p = str(getattr(bind, "provider", "") or "").strip().lower()
            m = str(getattr(bind, "model", "") or "").strip().lower()
    if p in ("xai", "grok") or "grok" in m:
        return WORK_MAX_TOOLS_PER_STEP
    return WORK_MAX_TOOLS_CLOUD


def cap_tools_for_step(
    tools: list[dict[str, Any]] | None,
    *,
    local: bool,
    max_tools: int | None = None,
) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    cap = (
        int(max_tools)
        if max_tools is not None
        else (LOCAL_MAX_TOOLS_PER_STEP if local else WORK_MAX_TOOLS_PER_STEP)
    )
    if len(tools) <= cap:
        return tools
    max_tools = cap
    by_name: dict[str, dict[str, Any]] = {}
    for t in tools:
        n = _tool_schema_name(t)
        if n and n not in by_name:
            by_name[n] = t
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in _OPERATE_CORE_TOOLS:
        picked = by_name.get(n)
        if picked is None or n in seen:
            continue
        out.append(picked)
        seen.add(n)
        if len(out) >= max_tools:
            return out
    for t in tools:
        n = _tool_schema_name(t)
        if not n or n in seen or n in _OPERATE_DEFER_TOOLS:
            continue
        out.append(t)
        seen.add(n)
        if len(out) >= max_tools:
            return out
    for t in tools:
        n = _tool_schema_name(t)
        if not n or n in seen:
            continue
        out.append(t)
        seen.add(n)
        if len(out) >= max_tools:
            break
    return out


def extract_tool_names(tool_calls: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        n = str((fn or {}).get("name") or tc.get("name") or "").strip().lower()
        if n:
            names.append(n)
    return names


def extract_write_paths(tool_calls: list[dict[str, Any]] | None) -> list[str]:
    paths: list[str] = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str((fn or {}).get("name") or "").lower()
        if name not in _WRITE_TOOLS:
            continue
        raw = (fn or {}).get("arguments") or "{}"
        try:
            obj = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("path"):
            paths.append(str(obj["path"]))
        edits = obj.get("edits") if isinstance(obj, dict) else None
        if isinstance(edits, list):
            for e in edits:
                if isinstance(e, dict) and e.get("path"):
                    paths.append(str(e["path"]))
    return paths


def mid_turn_fit_messages(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    """Hard-fit messages+tools for local hosts before the next LLM POST."""
    local = False
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        local = is_local_binding(provider, model, base_url)
    if not local:
        return messages, tools
    try:
        from remedy.core.endless_context import fit_local_request, resolve_local_window

        win = resolve_local_window(
            provider=provider, model=model, base_url=base_url
        )
        fitted_m, fitted_t, meta = fit_local_request(
            messages,
            tools,
            window=win,
            provider=provider,
            model=model,
            coding_bias=True,
        )
        if meta.get("levels") and meta["levels"] != ["ok"]:
            logger.info(
                "mid_turn_fit levels=%s est=%s→%s",
                meta.get("levels"),
                meta.get("est_before"),
                meta.get("est_after"),
            )
        return fitted_m, fitted_t
    except Exception:
        logger.debug("mid_turn_fit failed", exc_info=True)
        return messages, tools
