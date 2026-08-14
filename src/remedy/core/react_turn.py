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

_EXPLORE_TOOLS = frozenset(
    {"file_read", "list_dir", "repo_search", "memory_search", "soul_recall", "web_search", "web_fetch"}
)
_WRITE_TOOLS = frozenset({"file_write", "file_edit", "file_edit_batch"})
_VERIFY_TOOLS = frozenset(
    {"bash_exec", "shell_exec", "job_run", "mission_verify", "mission_update"}
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
    # Counters
    tools_executed: int = 0
    tool_batches: int = 0
    pseudo_recoveries: int = 0
    disconnect_retries: int = 0
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

    def tools_armed(self) -> bool:
        """True when tool schemas will actually be sent this step."""
        return bool(self.tools)

    def can_rearm(self) -> bool:
        return bool(self.all_tools)

    def rearm(self, *, reason: str = "rearm") -> None:
        if self.all_tools:
            self.tools = list(self.all_tools)
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
) -> ToolsDecision:
    """Single pure decision: which tool schemas to send this turn/step."""
    all_t = list(all_tools or [])
    if not all_t:
        return ToolsDecision(None, False, "no_tools_registered", pack="none")

    if plan_mode:
        try:
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            pack = [
                t
                for t in all_t
                if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
            ]
            return ToolsDecision(
                pack or None, False, "plan_mode", pack="plan"
            )
        except Exception:
            return ToolsDecision(all_t, False, "plan_mode_fallback", pack="full")

    with suppress(Exception):
        from remedy.core.self_inject_draft import (
            INTERNAL_IMPROVE_TOOLS,
            in_internal_improve,
        )

        if in_internal_improve():
            pack = [
                t
                for t in all_t
                if ((t.get("function") or {}).get("name") or "")
                in INTERNAL_IMPROVE_TOOLS
            ]
            return ToolsDecision(
                pack or None, True, "internal_improve", pack="self_fix"
            )

    # Knowledge / verbal-only FIRST. Leftover build_active or implement
    # keywords must not drag "1 + 1" into a local tool loop.
    with suppress(Exception):
        from remedy.core.react_policy import (
            is_knowledge_question,
            is_verbal_only_request,
            looks_like_injected_tool_markup,
        )

        if (
            is_verbal_only_request(message or "")
            or is_knowledge_question(message or "")
            or looks_like_injected_tool_markup(message or "")
        ):
            logger.info("react_tools disarm reason=non_work")
            return ToolsDecision(None, False, "non_work", pack="none")

    # Page interaction / browse full agency
    if page_interaction:
        return ToolsDecision(all_t, True, "page_interaction", pack="full")

    msg_wants = False
    task_like = False
    with suppress(Exception):
        from remedy.core.react_policy import message_wants_tools

        msg_wants = bool(message_wants_tools(message or ""))
    with suppress(Exception):
        from remedy.core.local_agent_optimize import message_wants_implement

        msg_wants = msg_wants or bool(message_wants_implement(message or ""))
    with suppress(Exception):
        from remedy.core.build_engine import looks_like_task_request

        task_like = bool(looks_like_task_request(message or ""))
        msg_wants = msg_wants or task_like

    local = False
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        local = is_local_binding(provider, model, base_url)

    # Task / tool-wanting messages: never strip (P0)
    if msg_wants or build_active or task_like:
        tools = all_t
        pack = "full"
        reason = (
            "build_active"
            if build_active
            else ("task" if task_like else "message_wants_tools")
        )
        # Local early steps: write-first until a write succeeds
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
        if local and tools and len(tools) > LOCAL_MAX_TOOLS_PER_STEP:
            tools = tools[:LOCAL_MAX_TOOLS_PER_STEP]
            reason = reason + "+local_cap"
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
        from remedy.core.react_policy import (
            is_knowledge_question,
            is_verbal_only_request,
            looks_like_injected_tool_markup,
        )

        if (
            is_verbal_only_request(message or "")
            or is_knowledge_question(message or "")
            or looks_like_injected_tool_markup(message or "")
        ):
            logger.info("react_tools disarm reason=non_work")
            return ToolsDecision(None, False, "non_work", pack="none")

    # L1 may strip *only* proven social/meta chat. Any other ask stays armed.
    if int(turn_tier or 1) == 1 and not browse_pre_url and not page_interaction:
        chat_only = False
        with suppress(Exception):
            from remedy.core.react_policy import is_chat_only_message

            chat_only = bool(is_chat_only_message(message or ""))
        if chat_only:
            open_work = bool(open_tasks)
            if not open_work and history:
                with suppress(Exception):
                    from remedy.core.react_policy import history_suggests_open_work

                    open_work = history_suggests_open_work(
                        history, open_tasks=open_tasks or None
                    )
            if not open_work:
                logger.info("react_tools disarm reason=l1_pure_chat tier=%s", turn_tier)
                return ToolsDecision(None, False, "l1_pure_chat", pack="none")

    return ToolsDecision(all_t, bool(all_t), "default_armed", pack="full")


def apply_tools_decision(state: TurnState, decision: ToolsDecision) -> None:
    state.tools = decision.tools
    state.run_until_done = decision.run_until_done
    state.arm_reason = decision.reason
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
        )
    )


def cap_tools_for_step(
    tools: list[dict[str, Any]] | None,
    *,
    local: bool,
    max_tools: int = LOCAL_MAX_TOOLS_PER_STEP,
) -> list[dict[str, Any]] | None:
    if not tools or not local:
        return tools
    if len(tools) <= max_tools:
        return tools
    return tools[:max_tools]


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
