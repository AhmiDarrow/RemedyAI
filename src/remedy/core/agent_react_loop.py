"""ReAct LLM stream loop.

Extracted from BasicRuntime._call_llm_stream so agent.py remains a thin
orchestrator. Takes a runtime instance for config, tools, and side effects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import aiohttp

from remedy.core.agent_tool_batch import execute_tool_calls
from remedy.core.provider_sanitize import sanitize_chat_body
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _looks_like_pseudo_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    batch_has_approval_required,
    batch_has_empty_search,
    batch_has_tool_errors,
    epoch_continue_message,
    is_productive_tool_batch,
    is_serial_explore_batch,
    looks_like_false_progress,
    mission_verify_gate_message,
    recovery_nudge_message,
    speed_batch_nudge_message,
    strip_tool_markup,
    turn_has_unfinished_work,
)
from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    build_assistant_api_message,
    build_runtime_system_block,
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    finalize_round_text,
    normalize_tool_calls,
    parse_sse_data_line,
    repair_reasoning_content_in_messages,
    repair_tool_arguments_in_messages,
    should_enable_tools,
    strip_broken_tool_call_turns,
)

logger = logging.getLogger(__name__)


def _is_fatal_llm_api_error(status: int, body: str) -> bool:
    """True when retrying the same model/request cannot succeed.

    e.g. HTTP 404 model-not-found — soft-continue spam looks like a stuck agent.
    """
    if status in (404, 410, 422):
        return True
    low = (body or "").lower()
    # Wrong model name for this host (e.g. grok id on DeepSeek API)
    if "supported api model" in low or "supported models are" in low:
        return True
    fatal_phrases = (
        "does not exist",
        "model_not_found",
        "invalid model",
        "unknown model",
        "not have access",
        "model is not available",
        "no such model",
        "unsupported model",
        "invalid_request_error",  # often permanent model/route issues
    )
    if any(p in low for p in fatal_phrases) and (
        "model" in low or status in (400, 403, 404)
    ):
        return True
    # 401/403 without a chance to refresh (non-xAI handled elsewhere)
    if status in (401, 403) and "expired" not in low:
        # Still allow one soft path for generic auth; treat "does not have access" as fatal above
        pass
    return False


async def call_llm_stream(runtime, message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
    """Call the LLM with a smooth multi-epoch ReAct loop.

    Yields status tokens prefixed with '@@' for tool-call lifecycle events.
    Soft epoch walls compact context and checkpoint — they do **not** strip
    tools while work is unfinished. Only the absolute safety ceiling (or a
    no-progress stale-epoch stop) forces a final answer.

    When *plan_mode* is True, only planning tools run (no shell/file writes).
    """
    try:
        from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding
        from remedy.interfaces.attachments import build_multimodal_user_content

        # For Partner Memory ranking + quiet distillation hooks
        with suppress(Exception):
            runtime._last_user_text = (message or "")[:4000]
        # Explicit “remember …” → distill BEFORE tools/LLM so same-turn recall works
        with suppress(Exception):
            from remedy.core.agent_post_turn import distill_user_message_now
            from remedy.memory.partner_memory import distill_user_text, is_explicit_remember_intent

            msg0 = message or ""
            if is_explicit_remember_intent(msg0) and getattr(runtime, "memory", None) is not None:
                # Prefer native await (no nested-loop races)
                project_path = str(
                    getattr(getattr(runtime, "config", None), "project_path", None)
                    or getattr(runtime, "_project_path", None)
                    or ""
                ) or None
                await distill_user_text(
                    runtime.memory,
                    msg0,
                    brief=getattr(runtime, "_session_brief", None),
                    session_id=session_id,
                    project_path=project_path,
                )
                # Belt-and-suspenders sync force path
                distill_user_message_now(runtime, msg0, session_id=session_id)
            else:
                distill_user_message_now(runtime, msg0, session_id=session_id)
        context = await runtime._build_context()
        # Surface active plan + plan-mode instructions
        with suppress(Exception):
            from pathlib import Path

            from remedy.core.plan_store import PlanStore

            home = getattr(runtime.config, "home_dir", None) or (Path.home() / ".remedy")
            store = PlanStore(home)
            plan = store.latest_for_session(session_id)
            if plan is not None:
                context = (
                    (context or "")
                    + "\n\n## Active task plan\n"
                    + plan.summary_markdown()
                )
            if plan_mode:
                from remedy.core.plan_store import PLAN_MODE_SYSTEM_ADDENDUM

                context = (context or "") + "\n\n" + PLAN_MODE_SYSTEM_ADDENDUM
            else:
                # Build mode: surgical edits, real plan step status, no junk files.
                with suppress(Exception):
                    from remedy.core.plan_store import BUILD_MODE_SYSTEM_ADDENDUM

                    context = (context or "") + "\n\n" + BUILD_MODE_SYSTEM_ADDENDUM
            # In-house computer use (provider-agnostic) — always available in Build;
            # Plan mode still only expose read/navigate tools via allowlist.
            with suppress(Exception):
                from remedy.core.computer.guidance import COMPUTER_USE_SYSTEM_ADDENDUM

                context = (context or "") + "\n\n" + COMPUTER_USE_SYSTEM_ADDENDUM
        history = await runtime._load_session_history(session_id, message)
        # Memory Harness L0: light prune of send-view (stored transcript untouched).
        # auto mode skips this pre-pass — apply_auto_harness_send_policy owns full
        # prune/offload/budget and would re-walk the same history (double work).
        # manual mode keeps a cheap dedupe + optional char-cap pass only.
        with suppress(Exception):
            from remedy.memory.harness.pruner import prune_messages_for_send

            mode = str(getattr(runtime, "_harness_mode", "auto") or "auto").lower()
            if mode == "manual":
                history = prune_messages_for_send(
                    history,
                    max_tool_chars=_TOOL_RESULT_CHAR_CAP,
                    dedupe_tools=True,
                )
        # Visual decoder path for text-only chat models + image attachments.
        vision_mode = "native"
        decode_brief: str | None = None
        with suppress(Exception):
            from remedy.vision.service import decode_for_turn

            cfg_for_vision: dict[str, Any] = {}
            with suppress(Exception):
                from remedy.interfaces.config import load_config

                cfg_for_vision = load_config() or {}
            _b = get_llm_binding(runtime)
            vres = decode_for_turn(
                attachments,
                provider=_b.provider,
                model=_b.model,
                cfg=cfg_for_vision,
            )
            mode = str(vres.get("mode") or "native")
            if mode == "decode" and vres.get("combined"):
                vision_mode = "decode"
                decode_brief = str(vres.get("combined") or "")
                for ev in vres.get("events") or []:
                    yield f"@@status:{ev}\n"
            elif mode == "unavailable" and vres.get("hint"):
                # Inject hint text (no image_url) so text-only models stay safe
                vision_mode = "decode"
                decode_brief = (
                    f"[Visual decoder unavailable] {vres.get('hint')}\n"
                    "Image files are attached by path only."
                )
                yield (
                    "@@status:Visual decoder unavailable — "
                    "enable in Settings for local image understanding\n"
                )

        user_content = build_multimodal_user_content(
            message,
            attachments,
            vision_mode=vision_mode,
            decode_brief=decode_brief,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": build_runtime_system_block(
                    system_prompt=runtime._system_prompt,
                    provider=get_llm_binding(runtime).provider,
                    model=get_llm_binding(runtime).model,
                    base_url=get_llm_binding(runtime).base_url,
                    max_steps=runtime._max_react_steps,
                    context=context,
                ),
            },
            *history,
            {"role": "user", "content": user_content},
        ]
        # Continuity / Memory Harness v2: enforce lean send-view + optional local brief
        with suppress(Exception):
            if runtime._harness_mode == "auto":
                from remedy.memory.harness.send_policy import (
                    apply_auto_harness_send_policy,
                )

                sid = str(getattr(runtime, "_session_id", "") or session_id or "")
                messages, _hmeta = apply_auto_harness_send_policy(
                    runtime,
                    messages,
                    user_text=message or "",
                    session_id=sid,
                    tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
                )
                # Soft library skill tip for desktop chip (cache-ranked; not an install)
                with suppress(Exception):
                    snap = getattr(runtime, "_last_context_snapshot", None)
                    lib = (getattr(snap, "signals", None) or {}).get("library_suggest")
                    if isinstance(lib, dict) and lib.get("id"):
                        import json as _json

                        yield (
                            "@@library_suggest:"
                            + _json.dumps(lib, default=str, separators=(",", ":"))
                            + "\n"
                        )
        all_tools = runtime._openai_tools()
        if plan_mode:
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            tools = [
                t
                for t in all_tools
                if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
            ]
        else:
            # Creative image prompts ("make something cool/spacey") must keep tools on.
            # Also: unfinished multi-turn work (history tool use / open tasks) so short
            # follow-ups like "go with your suggestions" keep agency.
            open_tasks: list[str] = []
            with suppress(Exception):
                brief = getattr(runtime, "_session_brief", None)
                if brief is not None:
                    open_tasks = list(getattr(brief, "open_tasks", None) or [])
            tools = (
                all_tools
                if should_enable_tools(
                    message,
                    all_tools,
                    has_attachments=bool(attachments),
                    history=history,
                    open_tasks=open_tasks or None,
                )
                or bool(
                    re.search(
                        r"\b(comfy|image|picture|nebula|spacey|generate|draw|illustrat|logo|asset|png)\b",
                        message or "",
                        re.I,
                    )
                )
                else []
            )

        # High-confidence browse kicks ("goto gmail", "goto google and search X"):
        # force tools on + pre-run computer_navigate so the rail opens even if
        # the model only narrates intent (session bug 2026-07-28).
        browse_pre_url: str | None = None
        clear_goals_only = False
        pure_action_kick = False
        open_only_browse = False
        page_interaction = False
        with suppress(Exception):
            from remedy.core.computer.browse_intent import (
                is_clear_goals_intent,
                is_open_only_browse,
                is_pure_action_kick,
                parse_browse_navigate_url,
                wants_page_interaction,
            )

            browse_pre_url = parse_browse_navigate_url(message or "")
            clear_goals_only = is_clear_goals_intent(message or "")
            pure_action_kick = is_pure_action_kick(message or "")
            open_only_browse = is_open_only_browse(message or "")
            page_interaction = wants_page_interaction(message or "")
        # Metabolism: turn cost tier + evidence/governor injects (silent)
        with suppress(Exception):
            from remedy.core.metabolism.turn import begin_turn_metabolism

            sid_m = str(getattr(runtime, "_session_id", "") or session_id or "")
            intent_m = "chat"
            with suppress(Exception):
                snap = getattr(runtime, "_last_context_snapshot", None)
                if snap is not None:
                    intent_m = str(getattr(snap, "intent", None) or "chat")
            roots_m: list[str] = []
            with suppress(Exception):
                roots_m = list(getattr(runtime, "_work_roots", None) or [])
            meta = begin_turn_metabolism(
                session_id=sid_m,
                user_text=message or "",
                intent=intent_m,
                plan_mode=bool(plan_mode),
                has_attachments=bool(attachments),
                tools_enabled=bool(all_tools),
                pure_action=bool(pure_action_kick),
                browse=bool(browse_pre_url or page_interaction),
                project_path=str(getattr(runtime, "_project_path", "") or ""),
                work_roots=roots_m or None,
                brief_head=(message or "")[:200],
            )
            runtime._turn_tier = int(meta.get("tier") or 1)
            runtime._turn_tier_label = str(meta.get("tier_label") or "")
            runtime._force_spread = bool(meta.get("force_spread"))
            runtime._action_ir = meta.get("action_ir")
            runtime._metabolism_allow_verify = bool(
                meta.get("allow_critical_verify")
            )
            runtime._shadow_strict = bool(
                (meta.get("policy") or {}).get("shadow_high_blast")
                and int(meta.get("tier") or 0) >= 2
            )
            with suppress(Exception):
                from remedy.core.metabolism.governor import get_governor

                if get_governor(sid_m).shadow_strict:
                    runtime._shadow_strict = True
            injects = list(meta.get("injects") or [])
            # Pending verify remedy from prior turn (one-shot)
            with suppress(Exception):
                pending = getattr(runtime, "_pending_verify_remedy", None)
                if pending:
                    injects.append(str(pending))
                    runtime._pending_verify_remedy = None
            if injects:
                messages.insert(
                    -1,
                    {
                        "role": "system",
                        "content": "\n\n".join(str(x) for x in injects if x),
                    },
                )
        if (browse_pre_url or page_interaction) and all_tools and not plan_mode:
            tools = all_tools

        seen_fps: set[str] = set()
        result_cache: dict[str, str] = {}
        produced_user_text = False
        pseudo_recovery_done = False
        pseudo_nudge_count = 0
        # Nudge once when the model claims progress without native tool_calls.
        false_progress_nudge_count = 0
        # One automatic recovery nudge per turn after a failing tool batch.
        recovery_nudge_done = False
        # One speed nudge if the model serializes explore as 1 tool/step.
        speed_batch_nudge_done = False
        serial_explore_streak = 0
        # Per-turn binding (parallel multi-provider); never use another turn's host/key.
        _bind = get_llm_binding(runtime)
        _adapter = _bind.adapter()
        headers = _adapter.auth_headers(_bind.api_key)
        endpoint = _adapter.chat_endpoint(_bind.base_url)

        # Long agent runs: high wall-clock + read idle so multi-step work
        # (and long thinking streams) are not killed mid-flight.
        timeout = aiohttp.ClientTimeout(total=3_600, sock_read=900, connect=60)
        connector = aiohttp.TCPConnector(
            limit=24,
            ttl_dns_cache=300,
        )
        # Auto-continue after finish_reason=length / max_tokens until complete.
        # No artificial short-answer wall — keep going until the model finishes.
        max_length_continuations = 10_000
        length_continuations = 0
        # Retry once after repairing DeepSeek reasoning_content on tool turns.
        reasoning_repair_done = False
        # Truncated tool-call JSON (stream cut / old sanitizer) → repair then strip.
        tool_args_repair_done = False
        tool_args_strip_done = False
        # Soft API errors: keep going when we already have tool context.
        # Low cap — fatal model errors hard-stop (see _is_fatal_llm_api_error).
        api_soft_failures = 0
        max_api_soft_failures = 3
        # Sticky force-answer after recoverable provider failures.
        force_answer_sticky = False
        # After one force-answer API attempt fails, stop (no 404 spam loop).
        force_answer_api_fail_once = False
        # Inject "Stop calling tools / final answer" user nudge at most once.
        force_answer_nudge_done = False
        # Empty-answer recovery (model thought but sent no content).
        empty_answer_retries = 0
        max_empty_answer_retries = 8
        # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
        auth_refresh_done = False
        # Multi-epoch: soft walls compact; absolute total is safety only.
        max_total = max(1, int(getattr(runtime, "_max_react_steps", 10_000) or 10_000))
        epoch_size = max(
            16, int(getattr(runtime, "_epoch_react_steps", 256) or 256)
        )
        auto_continue = bool(getattr(runtime, "_react_auto_continue", True))
        max_stale_epochs = max(
            1, int(getattr(runtime, "_react_max_stale_epochs", 2) or 2)
        )
        epoch_index = 1
        productive_in_epoch = 0
        tool_batches_in_epoch = 0
        stale_epochs = 0
        tool_batches_this_turn = 0
        runtime._fingerprint_loop_hits = 0
        open_tasks_for_wall: list[str] = []
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            if brief is not None:
                open_tasks_for_wall = list(getattr(brief, "open_tasks", None) or [])
        # Coding / tool-enabled turns: Grok Build style — run until finished.
        # Pure action kicks ("goto google and search X", "clear goals") must NOT
        # resume older open_tasks / wiki work from history — latest message only.
        run_until_done = bool(tools) or bool(all_tools)
        if pure_action_kick or clear_goals_only or browse_pre_url or page_interaction:
            open_tasks_for_wall = []
        # Open-only: short path. Interaction (login/type/click): full agent loop.
        if page_interaction:
            run_until_done = True
            if all_tools:
                tools = all_tools
        elif pure_action_kick or clear_goals_only or open_only_browse:
            run_until_done = bool(open_only_browse or clear_goals_only)

        # L1 lean: bias tools off (chat can still answer; agency was L2+)
        if (
            int(getattr(runtime, "_turn_tier", 1) or 1) == 1
            and not plan_mode
            and not pure_action_kick
            and not browse_pre_url
            and not page_interaction
            and not clear_goals_only
        ):
            tools = None  # type: ignore[assignment]
            run_until_done = False
            # Keep all_tools for recovery if model later needs agency

        # Accumulated assistant text for critical verify at end
        assistant_text_acc: list[str] = []

        # L0 system fast path: model/skills/whoami/version — no frontier tokens.
        if (
            not plan_mode
            and not attachments
            and not browse_pre_url
            and not page_interaction
            and not clear_goals_only
            and int(getattr(runtime, "_turn_tier", 1) or 1) == 0
        ):
            with suppress(Exception):
                from remedy.core.metabolism.l0 import try_l0_system_reply

                l0 = try_l0_system_reply(runtime, message or "")
                if l0:
                    yield l0
                    return

        # Personal-assistant fast path: high-confidence tool-only asks skip the
        # provider model (calendar list, budget status, brief, simple log, …).
        # Low confidence / complex → fall through to full ReAct (unchanged).
        if (
            not plan_mode
            and not attachments
            and not browse_pre_url
            and not page_interaction
            and not clear_goals_only
        ):
            with suppress(Exception):
                from remedy.assistant.fast_path import (
                    assistant_fast_path_enabled,
                    format_fast_path_reply,
                    match_assistant_fast_path,
                )

                pa_plan = match_assistant_fast_path(message or "")
                if pa_plan is not None and assistant_fast_path_enabled(
                    getattr(getattr(runtime, "config", None), "home_dir", None)
                ):
                    has_tool = any(
                        ((t.get("function") or {}).get("name") or "") == pa_plan.tool
                        for t in (all_tools or [])
                    )
                    if has_tool:
                        from uuid import uuid4

                        fp_id = f"pa_fp_{uuid4().hex[:10]}"
                        pre_calls = normalize_tool_calls(
                            [
                                {
                                    "id": fp_id,
                                    "type": "function",
                                    "function": {
                                        "name": pa_plan.tool,
                                        "arguments": json.dumps(pa_plan.arguments),
                                    },
                                }
                            ]
                        )
                        logger.info(
                            "assistant_fast_path tool=%s label=%s",
                            pa_plan.tool,
                            pa_plan.label,
                        )
                        yield "@@tool_calls"
                        messages.append(
                            build_assistant_api_message(
                                content=None, tool_calls=pre_calls
                            )
                        )
                        tool_body = ""
                        async for event, tool_msg in execute_tool_calls(
                            runtime,
                            pre_calls,
                            seen_fps=seen_fps,
                            result_cache=result_cache,
                        ):
                            if event.startswith("@@"):
                                yield event
                            if tool_msg:
                                messages.append(tool_msg)
                                tool_body = str(tool_msg.get("content") or "")
                        reply = format_fast_path_reply(pa_plan.tool, tool_body)
                        yield reply if reply else "Done."
                        logger.info(
                            "assistant_fast_path done tool=%s chars=%s",
                            pa_plan.tool,
                            len(reply or ""),
                        )
                        return

        # "clear goals" / "we have none" — no LLM, no replaying earlier browses
        if clear_goals_only and not plan_mode:
            clear_msg = "No open goals — already clear."
            with suppress(Exception):
                from uuid import uuid4

                has_clear = any(
                    ((t.get("function") or {}).get("name") or "") == "goal_clear_all"
                    for t in (all_tools or [])
                )
                if has_clear:
                    cid = f"goal_clear_{uuid4().hex[:10]}"
                    pre_calls = normalize_tool_calls(
                        [
                            {
                                "id": cid,
                                "type": "function",
                                "function": {
                                    "name": "goal_clear_all",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    )
                    yield "@@tool_calls"
                    messages.append(
                        build_assistant_api_message(content=None, tool_calls=pre_calls)
                    )
                    async for event, tool_msg in execute_tool_calls(
                        runtime,
                        pre_calls,
                        seen_fps=seen_fps,
                        result_cache=result_cache,
                    ):
                        if event.startswith("@@"):
                            yield event
                        if tool_msg:
                            messages.append(tool_msg)
                            body = str(tool_msg.get("content") or "").strip()
                            if body:
                                clear_msg = body
                else:
                    # No tool registered — wipe session brief open_tasks only
                    brief = getattr(runtime, "_session_brief", None)
                    if brief is not None:
                        brief.open_tasks = []
                        with suppress(Exception):
                            brief.touch()
                        clear_msg = "Session open tasks cleared (no goal list entries)."
            yield clear_msg if clear_msg else "Goals cleared."
            logger.info("clear_goals short-circuit")
            return

        # Pre-open Browser rail for "goto X" (with or without follow-up work).
        # Open-only → short confirm + end turn.
        # Interaction (sign in / type / click) → navigate then CONTINUE agent loop.
        if browse_pre_url and not plan_mode:
            has_nav = any(
                ((t.get("function") or {}).get("name") or "") == "computer_navigate"
                for t in (all_tools or [])
            )
            if has_nav:
                from uuid import uuid4

                from remedy.core.computer.browse_intent import short_site_label

                nav_id = f"browse_pre_{uuid4().hex[:10]}"
                pre_calls = normalize_tool_calls(
                    [
                        {
                            "id": nav_id,
                            "type": "function",
                            "function": {
                                "name": "computer_navigate",
                                "arguments": json.dumps(
                                    {
                                        "url": browse_pre_url,
                                        "target": "browser",
                                    }
                                ),
                            },
                        }
                    ]
                )
                logger.info(
                    "browse_intent pre-navigate url=%s interaction=%s",
                    browse_pre_url,
                    page_interaction,
                )
                yield "@@tool_calls"
                messages.append(
                    build_assistant_api_message(
                        content=None,
                        tool_calls=pre_calls,
                    )
                )
                browse_ok = False
                browse_fail_snip = ""
                async for event, tool_msg in execute_tool_calls(
                    runtime,
                    pre_calls,
                    seen_fps=seen_fps,
                    result_cache=result_cache,
                ):
                    if event.startswith("@@"):
                        yield event
                    if tool_msg:
                        messages.append(tool_msg)
                        body = str(tool_msg.get("content") or "")
                        low = body.lower()
                        if (
                            "success" in low
                            or '"ok": true' in low
                            or '"ok":true' in low
                            or "user_visible" in low
                        ) and "rail_failed" not in low and '"ok": false' not in low:
                            browse_ok = True
                        elif '"ok": false' in low or "rail_failed" in low:
                            browse_fail_snip = body[:400]
                tool_batches_this_turn += 1
                productive_in_epoch += 1
                if browse_ok and open_only_browse and not page_interaction:
                    # Pure open-URL — stop (no spiral).
                    label = short_site_label(browse_pre_url)
                    yield f"Opened **{label}** in the Browser rail."
                    logger.info(
                        "browse_intent done short-circuit url=%s",
                        browse_pre_url,
                    )
                    return
                if browse_ok and page_interaction:
                    # Keep going: click/type/login. Tools stay on.
                    tools = all_tools
                    run_until_done = True
                    cred_hint = ""
                    with suppress(Exception):
                        from remedy.core.computer.elements import (
                            extract_typed_credentials,
                        )

                        creds = extract_typed_credentials(message or "")
                        if creds.get("email") or creds.get("username"):
                            u = creds.get("email") or creds.get("username")
                            cred_hint = (
                                f" Username/email from user message: `{u}`. "
                                "Prefer computer_act(click=\"Sign in\" or \"Email\", "
                                f"type=\"{u}\") or computer_click text= + computer_type."
                            )
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Browser rail already opened {browse_pre_url} (SUCCESS). "
                                "Finish the **rest** of the latest user request only "
                                "(sign-in, type username/email, click). "
                                "Prefer **computer_act** (click+type in one call) or "
                                "computer_click text=… then computer_type. "
                                "Use snapshot SoM refs if click-by-text fails once. "
                                "No screenshot/vision. No unrelated older tasks."
                                f"{cred_hint}"
                            ),
                        }
                    )
                elif not browse_ok and not page_interaction:
                    # Failed open-only — one short force-answer, tools off
                    force_answer_sticky = True
                    tools = []
                    run_until_done = False
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"computer_navigate for {browse_pre_url} did not succeed"
                                f"{(': ' + browse_fail_snip) if browse_fail_snip else '.'} "
                                "Reply in **one short sentence** with the error. "
                                "Do **not** call more tools unless the user asks."
                            ),
                        }
                    )

        # If we still enter the LLM loop for a short kick, pin focus to latest msg
        # so history (old wiki / goals / navigates) is not re-executed.
        if pure_action_kick or browse_pre_url:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "IMPORTANT: Fulfill **only** the latest user request. "
                        "Do not reopen earlier wikis, navigates, membership flows, "
                        "or goals unless that latest message asks for them. "
                        "When the latest request is done, stop — no extra tools."
                    ),
                }
            )

        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector
        ) as http:
            for step in range(max_total):
                # Cooperative abort between ReAct steps (Stop generation).
                with suppress(Exception):
                    from remedy.core.turn_context import is_turn_aborted

                    if is_turn_aborted():
                        yield "@@aborted\n"
                        return

                # Soft epoch roll every epoch_size model rounds: compact + checkpoint.
                # Tools stay on — run until finished (not a tool-budget stop).
                if (
                    step > 0
                    and step % epoch_size == 0
                    and auto_continue
                    and not force_answer_sticky
                ):
                    unfinished = turn_has_unfinished_work(
                        runtime,
                        session_id=session_id,
                        tools_enabled=bool(tools or all_tools),
                        tool_steps_this_turn=tool_batches_this_turn,
                        open_tasks=open_tasks_for_wall or None,
                    )
                    coding_in_flight = run_until_done and (
                        unfinished
                        or tool_batches_this_turn > 0
                        or bool(tools or all_tools)
                    )
                    if coding_in_flight:
                        # Stale only when an epoch had *zero* tool calls (dead air).
                        # Failed tools still count as activity — keep recovering.
                        if tool_batches_in_epoch <= 0 and productive_in_epoch <= 0:
                            stale_epochs += 1
                        else:
                            stale_epochs = 0
                        if stale_epochs >= max_stale_epochs:
                            logger.warning(
                                "Stale epochs=%d at step=%d — safety pause "
                                "(no tool activity for many epochs)",
                                stale_epochs,
                                step,
                            )
                            yield (
                                "@@status:Paused after long idle (no tool activity) "
                                "— say continue to resume from the checkpoint\n"
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="step_wall",
                                    title="Idle safety pause",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                            force_answer_sticky = True
                            tools = []
                        else:
                            epoch_index += 1
                            logger.info(
                                "ReAct soft epoch roll → epoch %d at step %d "
                                "(productive=%d tools=%d stale=%d)",
                                epoch_index,
                                step,
                                productive_in_epoch,
                                tool_batches_in_epoch,
                                stale_epochs,
                            )
                            yield (
                                f"@@status:Continuing until task finished "
                                f"(epoch {epoch_index}, step {step})…\n"
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="step_wall",
                                    title=f"Epoch {epoch_index - 1} complete",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                            with suppress(Exception):
                                from remedy.memory.harness.send_policy import (
                                    slim_messages_mid_turn,
                                )

                                messages[:] = slim_messages_mid_turn(
                                    runtime,
                                    messages,
                                    session_id=str(
                                        getattr(runtime, "_session_id", "")
                                        or session_id
                                        or ""
                                    ),
                                    tool_result_char_cap=int(
                                        _TOOL_RESULT_CHAR_CAP or 0
                                    ),
                                )
                            with suppress(Exception):
                                from remedy.memory.partner_state import (
                                    ensure_partner_state,
                                )
                                from remedy.memory.partner_state.continuity import (
                                    schedule_continuity_core,
                                )

                                ensure_partner_state(runtime).fire_prospectives(
                                    "epoch_roll"
                                )
                                schedule_continuity_core(runtime, use_local=False)
                            messages.append(
                                epoch_continue_message(
                                    epoch=epoch_index - 1,
                                    total_step=step,
                                )
                            )
                            productive_in_epoch = 0
                            tool_batches_in_epoch = 0
                            # Keep tools; re-enable if a prior loop cleared them.
                            if all_tools:
                                tools = all_tools
                    elif step >= epoch_size and not run_until_done:
                        # Pure chat (tools never enabled) — wrap up.
                        force_answer_sticky = True
                    elif step >= epoch_size and run_until_done and all_tools:
                        # Tools enabled but model has not used them yet — nudge, keep going.
                        epoch_index += 1
                        tools = all_tools
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Keep going until the user request is finished. "
                                    "Use tools now (do not stop for a step count). "
                                    "Call list_dir / file_read / file_edit / bash_exec / "
                                    "mission_* / spread_run as needed and complete the work."
                                ),
                            }
                        )
                        productive_in_epoch = 0
                        tool_batches_in_epoch = 0

                is_final_step = step >= max_total - 1
                # Absolute safety wall only — soft epochs never force-answer alone.
                force_answer = (
                    is_final_step or not tools or force_answer_sticky
                )
                step_tools = None if force_answer else tools

                # Metabolism: mark model boundary + inject evidence delta before call
                with suppress(Exception):
                    from remedy.core.metabolism.turn import mark_model_call
                    from remedy.core.metabolism.evidence import get_evidence_ledger

                    sid_mm = str(
                        getattr(runtime, "_session_id", "") or session_id or ""
                    )
                    mark_model_call(sid_mm)
                    if int(getattr(runtime, "_turn_tier", 1) or 1) >= 2:
                        eblock = get_evidence_ledger(sid_mm).pointer_block(limit=8)
                        if eblock and tool_batches_this_turn > 0:
                            messages.append(
                                {"role": "system", "content": eblock}
                            )

                if (
                    force_answer
                    and step > 0
                    and length_continuations == 0
                    and not force_answer_nudge_done
                ):
                    # Once only — repeating this every step bloated context and looked stuck.
                    force_answer_nudge_done = True
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Stop calling tools. Using the information above, "
                                "give your complete final answer to the user now. "
                                "Be thorough and finish every section — "
                                "do not cut off mid-section or summarize-only."
                            ),
                        }
                    )

                # Mid-turn Memory Harness: re-slim after tool accumulation
                if step > 0 and getattr(runtime, "_harness_mode", "auto") != "off":
                    with suppress(Exception):
                        from remedy.memory.harness.send_policy import (
                            slim_messages_mid_turn,
                        )

                        messages[:] = slim_messages_mid_turn(
                            runtime,
                            messages,
                            session_id=str(
                                getattr(runtime, "_session_id", "") or session_id or ""
                            ),
                            tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
                        )

                # Never send incomplete tool_calls/tool pairings (HTTP 400).
                # Also force valid JSON on every tool-call arguments blob before
                # POST (guards length-truncated streams + legacy history).
                repair_tool_arguments_in_messages(messages)
                messages[:] = ensure_tool_call_pairings(messages)
                # OpenAI-compatible providers (openai, deepseek, ollama, …) stream SSE.
                # Anthropic currently uses a single JSON response (stream=False).
                _bind = get_llm_binding(runtime)
                _adapter = _bind.adapter()
                headers = _adapter.auth_headers(_bind.api_key)
                endpoint = _adapter.chat_endpoint(_bind.base_url)
                use_openai_sse = bool(
                    getattr(_adapter, "uses_openai_sse", True)
                )
                body = _adapter.build_body(
                    model=_bind.model,
                    messages=messages,
                    tools=step_tools,
                    stream=use_openai_sse,
                    thinking_level=getattr(runtime, "_thinking_level", "high"),
                )
                # Trust boundary: fail closed — never POST unsanitized tool bodies.
                try:
                    body = sanitize_chat_body(body if isinstance(body, dict) else {})
                except Exception as sanitize_exc:
                    logger.error(
                        "provider sanitize failed (aborting LLM call): %s",
                        sanitize_exc,
                    )
                    raise RuntimeError(
                        "Refusing to send chat to provider: sanitization failed. "
                        "Retry the turn; if it persists, check tool results for "
                        "unexpected shapes."
                    ) from sanitize_exc

                collected: dict[str, Any] = {"content": None, "tool_calls": None}
                round_state = StreamRoundState()

                _llm_t0 = time.perf_counter()
                async with http.post(
                    endpoint, headers=headers, json=body
                ) as resp:
                    _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error(
                            "LLM API error %d: %s", resp.status, text[:500]
                        )
                        with suppress(Exception):
                            from remedy.nanoswarm import get_swarm
                            from remedy.nanoswarm.events import SwarmEvent

                            get_swarm().dispatch(
                                SwarmEvent.provider_health(
                                    provider=_bind.provider,
                                    model=_bind.model,
                                    ok=False,
                                    latency_ms=_llm_ms,
                                    error=text[:200],
                                    status_code=int(resp.status),
                                )
                            )
                        # xAI (and similar): expired OAuth → refresh once, retry.
                        if (
                            resp.status in (401, 403)
                            and not auth_refresh_done
                            and str(_bind.provider or "").lower() == "xai"
                        ):
                            auth_refresh_done = True
                            try:
                                from remedy.interfaces.xai_auth import (
                                    refresh_if_needed,
                                    resolve_bearer,
                                )

                                home = None
                                if getattr(runtime, "config", None) is not None:
                                    hd = getattr(runtime.config, "home_dir", None)
                                    if hd:
                                        from pathlib import Path

                                        home = Path(hd).expanduser()
                                refresh_if_needed(home)
                                new_token = resolve_bearer(home)
                                if new_token and new_token != _bind.api_key:
                                    runtime._llm_api_key = new_token
                                    _bind = LlmBinding(
                                        provider=_bind.provider,
                                        model=_bind.model,
                                        base_url=_bind.base_url,
                                        api_key=new_token,
                                    )
                                    set_llm_binding(_bind)
                                    _adapter = _bind.adapter()
                                    headers = _adapter.auth_headers(_bind.api_key)
                                    logger.warning(
                                        "xAI credentials refreshed after HTTP %s; retrying",
                                        resp.status,
                                    )
                                    yield (
                                        "\n[auth] Refreshed xAI session; "
                                        "retrying request…\n"
                                    )
                                    continue
                            except Exception as auth_exc:
                                logger.debug("xAI re-auth failed: %s", auth_exc)
                            # Refresh failed → clear soft-continue noise with guidance.
                            yield (
                                "\n[auth required] xAI session expired or rejected. "
                                "Sign in again in Settings (Sign in with xAI) or "
                                "update your API key.\n"
                            )
                            return
                        # DeepSeek thinking mode: tool turns require reasoning_content.
                        if (
                            resp.status == 400
                            and "reasoning_content" in text.lower()
                            and not reasoning_repair_done
                        ):
                            reasoning_repair_done = True
                            if repair_reasoning_content_in_messages(messages):
                                logger.warning(
                                    "Repaired missing reasoning_content on tool "
                                    "turns; retrying request"
                                )
                                yield (
                                    "\n[provider fix] Restored thinking-mode "
                                    "reasoning for tool turns; continuing…\n"
                                )
                                continue
                        # Truncated / invalid tool-call JSON in history (often after
                        # a long plan_save or max_tokens mid-arguments stream).
                        _tool_arg_err = resp.status == 400 and (
                            "tool argument" in text.lower()
                            or "eof while parsing" in text.lower()
                            or "invalid-argument" in text.lower()
                            or "unmodified tool arguments" in text.lower()
                        )
                        if _tool_arg_err and not tool_args_repair_done:
                            tool_args_repair_done = True
                            try:
                                repaired_n = repair_tool_arguments_in_messages(messages)
                                messages[:] = ensure_tool_call_pairings(messages)
                                logger.warning(
                                    "Repaired tool-call arguments on %d assistant "
                                    "turn(s) after provider 400; retrying",
                                    repaired_n,
                                )
                                yield (
                                    "\n[provider fix] Repaired incomplete tool "
                                    "arguments; continuing…\n"
                                )
                                continue
                            except Exception as repair_exc:
                                logger.debug(
                                    "tool-args repair failed: %s", repair_exc
                                )
                        if _tool_arg_err and tool_args_repair_done and not tool_args_strip_done:
                            tool_args_strip_done = True
                            try:
                                stripped = strip_broken_tool_call_turns(messages)
                                messages[:] = ensure_tool_call_pairings(messages)
                                logger.warning(
                                    "Stripped %d broken tool-call turn(s) after "
                                    "provider 400; retrying without replaying them",
                                    stripped,
                                )
                                yield (
                                    "\n[provider fix] Dropped truncated tool calls "
                                    "from context; continuing…\n"
                                )
                                # Prefer finishing from context rather than more tools
                                # that re-inflate huge argument payloads.
                                tools = []
                                force_answer_sticky = True
                                continue
                            except Exception as strip_exc:
                                logger.debug(
                                    "tool-args strip failed: %s", strip_exc
                                )
                        # Fatal: wrong/missing model — do not soft-retry 16× (looks stuck).
                        if _is_fatal_llm_api_error(resp.status, text):
                            model_name = str(_bind.model or "unknown")
                            prov = str(_bind.provider or "unknown")
                            yield (
                                f"\n[LLM ERROR — HTTP {resp.status}]\n"
                                f"{text[:500]}\n[END LLM ERROR]\n\n"
                                f"**Cannot continue:** model `{model_name}` "
                                f"(provider `{prov}`) is not available or this "
                                f"account cannot use it.\n\n"
                                "Pick a working model in the model picker / Settings "
                                "(e.g. your previous Grok or DeepSeek id), then resend. "
                                "This is not a tool-budget limit.\n"
                            )
                            return
                        # Already forced a no-tool answer and API still failed → stop.
                        if force_answer_sticky or force_answer_api_fail_once:
                            yield (
                                f"\n[LLM ERROR — HTTP {resp.status}]\n"
                                f"{text[:500]}\n[END LLM ERROR]\n\n"
                                "Stopped after repeated provider errors. "
                                "Check model/API key in Settings and try again "
                                "(or say **continue** after switching models).\n"
                            )
                            return
                        api_soft_failures += 1
                        # Transient path: one force-answer attempt from any tool context.
                        if api_soft_failures <= max_api_soft_failures:
                            yield (
                                f"\n[LLM notice — HTTP {resp.status}; "
                                f"trying to finish from context "
                                f"({api_soft_failures}/{max_api_soft_failures})]\n"
                                f"{text[:200]}\n"
                            )
                            tools = []
                            force_answer_sticky = True
                            force_answer_api_fail_once = True
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "The model API returned an error. "
                                        "Using any tool results already gathered, "
                                        "give your best complete answer now. "
                                        "Do not call tools."
                                    ),
                                }
                            )
                            continue
                        yield (
                            f"\n[LLM ERROR — HTTP {resp.status}]\n"
                            f"{text[:500]}\n[END LLM ERROR]\n"
                            "Stopped after repeated API errors. "
                            "Switch model or check the provider, then resend.\n"
                        )
                        return

                    with suppress(Exception):
                        from remedy.nanoswarm import get_swarm
                        from remedy.nanoswarm.events import SwarmEvent

                        get_swarm().dispatch(
                            SwarmEvent.provider_health(
                                provider=_bind.provider,
                                model=_bind.model,
                                ok=True,
                                latency_ms=_llm_ms,
                                status_code=200,
                            )
                        )

                    # Live-stream final-answer rounds (no tools this step).
                    # Buffer when tools are enabled — DeepSeek-class models
                    # often dump DSML tool markup as content if we stream live.
                    stream_live = step_tools is None

                    headers_map = getattr(resp, "headers", None) or {}
                    content_type = str(
                        headers_map.get("Content-Type")
                        or headers_map.get("content-type")
                        or ""
                    ).lower()
                    # Prefer real response type; DeepSeek/OpenRouter return event-stream.
                    is_event_stream = "event-stream" in content_type
                    if use_openai_sse or is_event_stream:
                        content_iter = resp.content.__aiter__()
                        # DeepSeek can pause a while mid-thought, but multi-minute
                        # dead air usually means a stuck provider — cut the round
                        # so the turn can recover (nudge / finish) instead of
                        # looking frozen for 15 minutes. Override with
                        # REMEDY_SSE_IDLE_SECONDS if needed.
                        import os as _os

                        try:
                            sse_idle_timeout = float(
                                _os.environ.get("REMEDY_SSE_IDLE_SECONDS", "180")
                            )
                        except ValueError:
                            sse_idle_timeout = 180.0
                        sse_idle_timeout = max(60.0, min(sse_idle_timeout, 900.0))
                        while True:
                            try:
                                line = await asyncio.wait_for(
                                    content_iter.__anext__(),
                                    timeout=sse_idle_timeout,
                                )
                            except StopAsyncIteration:
                                break
                            except TimeoutError:
                                logger.warning(
                                    "SSE stream idle >%.0fs; ending this model round "
                                    "(provider likely stuck; will continue/promote reasoning if any)",
                                    sse_idle_timeout,
                                )
                                # Surface a short note so the UI is not silent.
                                with suppress(Exception):
                                    if (
                                        not round_state.content_parts
                                        and not round_state.reasoning_parts
                                    ):
                                        yield (
                                            "\n\n_(Provider stream idle — "
                                            "ending this model round.)_\n"
                                        )
                                break
                            line_text = line.decode("utf-8").strip()
                            if line_text == "data: [DONE]":
                                break
                            chunk = parse_sse_data_line(line_text)
                            if chunk is None:
                                continue
                            # Provider usage — keep *last* snapshot per HTTP stream.
                            # (Do not ledger on every intermediate SSE chunk.)
                            try:
                                from remedy.core.usage import usage_from_provider_payload

                                u = usage_from_provider_payload(
                                    chunk,
                                    model=_bind.model,
                                    provider=_bind.provider,
                                )
                                if u:
                                    # Prefer later snapshot if multiple chunks carry usage.
                                    prev = round_state.last_usage
                                    if prev and prev.get("source") == "provider":
                                        from remedy.core.usage import merge_usage

                                        round_state.last_usage = merge_usage(prev, u)
                                    else:
                                        round_state.last_usage = u
                            except Exception:
                                pass
                            r_before = len(''.join(round_state.reasoning_parts))
                            live = apply_openai_sse_chunk(
                                round_state, chunk, stream_live=stream_live
                            )
                            r_after = ''.join(round_state.reasoning_parts)
                            if len(r_after) > r_before:
                                yield f'@@thinking:{r_after[r_before:]}'
                            if live:
                                produced_user_text = True
                                yield live
                    else:
                        data = await resp.json()
                        try:
                            from remedy.core.usage import usage_from_provider_payload

                            u = usage_from_provider_payload(
                                data,
                                model=_bind.model,
                                provider=_bind.provider,
                            )
                            if u:
                                round_state.last_usage = u
                        except Exception:
                            pass
                        parsed = _adapter.extract_response(data)
                        content = parsed.get("content")
                        if content:
                            round_state.content_parts.append(content)
                        # Capture provider reasoning for tool-turn replay.
                        reason = (
                            parsed.get("reasoning_content")
                            or parsed.get("reasoning")
                            or ""
                        )
                        if isinstance(reason, str) and reason.strip():
                            round_state.reasoning_parts.append(reason.strip())
                        raw_tcs = parsed.get("tool_calls")
                        if raw_tcs:
                            round_state.tool_call_acc = dict(enumerate(raw_tcs))
                        collected = {**collected, **parsed}

                    content_parts = round_state.content_parts
                    reasoning_parts = round_state.reasoning_parts

                # Ledger + stream usage once per LLM HTTP call (not per SSE chunk).
                _u_final = getattr(round_state, "last_usage", None)
                if isinstance(_u_final, dict) and (
                    int(_u_final.get("prompt_tokens") or 0)
                    or int(_u_final.get("completion_tokens") or 0)
                ):
                    try:
                        from remedy.core.usage import observe_provider_usage
                        from remedy.core.usage_ledger import record_usage_event
                        from remedy.nanoswarm.token_nanobot import get_token_nanobot

                        pt = int(_u_final.get("prompt_tokens") or 0)
                        ct = int(_u_final.get("completion_tokens") or 0)
                        est = int(get_token_nanobot().last_estimate or 0)
                        prov = _bind.provider
                        mod = _bind.model
                        if pt > 0 and est > 0:
                            observe_provider_usage(
                                est, pt, provider=prov, model=mod
                            )
                        with suppress(Exception):
                            from remedy.core.session_quality import get_session_quality

                            get_session_quality(
                                str(getattr(runtime, "_session_id", "") or "")
                            ).record_turn(prompt_tokens=pt, completion_tokens=ct)
                        with suppress(Exception):
                            record_usage_event(
                                session_id=str(
                                    getattr(runtime, "_session_id", "") or ""
                                )
                                or None,
                                provider=prov,
                                model=mod,
                                prompt_tokens=pt,
                                completion_tokens=ct,
                                total_tokens=int(
                                    _u_final.get("total_tokens") or (pt + ct)
                                ),
                                estimated_cost_usd=float(
                                    _u_final.get("estimated_cost_usd") or 0
                                ),
                                source=str(_u_final.get("source") or "provider"),
                                meta={
                                    "cache_hit_tokens": int(
                                        _u_final.get("cache_hit_tokens") or 0
                                    ),
                                    "cache_miss_tokens": _u_final.get(
                                        "cache_miss_tokens"
                                    ),
                                },
                            )
                        yield (
                            "@@usage:"
                            + json.dumps(_u_final, separators=(",", ":"))
                        )
                    except Exception:
                        pass

                tool_calls_list = round_state.tool_calls_list(collected)
                reasoning_out = round_state.reasoning_out

                # Finalize text. Live-stream already yielded tokens when tools off.
                text_out = finalize_round_text(round_state, tool_calls_list)
                if text_out and not tool_calls_list:
                    with suppress(Exception):
                        assistant_text_acc.append(str(text_out)[:8000])
                # Never treat DSML / text-tool dumps as user-visible answer text.
                if text_out and _looks_like_pseudo_tools(text_out):
                    recovered_preview = _parse_pseudo_tool_calls(text_out)
                    clean = strip_tool_markup(text_out)
                    # Keep only non-markup prose (if any) for the bubble.
                    text_out = clean if clean and not _looks_like_pseudo_tools(clean) else ""
                    if not tool_calls_list and recovered_preview:
                        # Force recovery path below even if tools were off this round.
                        pass
                if (
                    text_out
                    and stream_live
                    and not content_parts
                    and reasoning_parts
                    and not tool_calls_list
                    and not _looks_like_pseudo_tools(text_out)
                ):
                    yield text_out
                    produced_user_text = True
                if text_out:
                    collected["content"] = text_out

                # Recovery: model wrote tool calls as plain text / DSML → run them for real.
                raw_round = finalize_round_text(round_state, tool_calls_list)
                if (
                    not tool_calls_list
                    and raw_round
                    and _looks_like_pseudo_tools(raw_round)
                    and all_tools
                    and not pseudo_recovery_done
                    and not force_answer
                ):
                    recovered = _parse_pseudo_tool_calls(raw_round)
                    if recovered:
                        pseudo_recovery_done = True
                        tools = all_tools  # ensure schemas stay available
                        recovered = normalize_tool_calls(recovered)
                        yield "@@tool_calls"
                        messages.append(
                            build_assistant_api_message(
                                content=(
                                    "Using tools now (recovered from a non-native tool dump)."
                                ),
                                tool_calls=recovered,
                                reasoning_content=reasoning_out or None,
                            )
                        )
                        batch_tool_msgs: list[dict[str, Any]] = []
                        async for event, tool_msg in execute_tool_calls(runtime,
                            recovered,
                            seen_fps=seen_fps,
                            result_cache=result_cache,
                        ):
                            if event.startswith("@@"):
                                yield event
                            if tool_msg:
                                messages.append(tool_msg)
                                batch_tool_msgs.append(tool_msg)
                        if (
                            not recovery_nudge_done
                            and batch_has_tool_errors(batch_tool_msgs)
                        ):
                            recovery_nudge_done = True
                            empty = batch_has_empty_search(batch_tool_msgs)
                            need_appr = batch_has_approval_required(batch_tool_msgs)
                            messages.append(
                                recovery_nudge_message(
                                    empty_search=empty, approval=need_appr
                                )
                            )
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="recovery",
                                    title="After tool failure",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                        with suppress(Exception):
                            runtime._maybe_auto_checkpoint(reason="auto")
                        continue
                    # DSML/text tools detected but incomplete (truncated stream) —
                    # nudge for real function-calling instead of hanging on junk.
                    if pseudo_nudge_count < 2:
                        pseudo_nudge_count += 1
                        tools = all_tools
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your last reply leaked incomplete tool markup "
                                    "(DSML/XML cut off mid-call). Do **not** write "
                                    "tool_calls as text. Call tools via the "
                                    "function-calling API now "
                                    "(file_read / list_dir / bash_exec / "
                                    "repo_search / file_edit), or give a short "
                                    "status update from context."
                                ),
                            }
                        )
                        logger.info(
                            "Incomplete DSML/pseudo tools — recovery nudge "
                            "(count=%s)",
                            pseudo_nudge_count,
                        )
                        continue

                if text_out and (not tool_calls_list or force_answer):
                    # Don't ship faux tool syntax as the final answer.
                    if (
                        raw_round
                        and _looks_like_pseudo_tools(raw_round)
                        and all_tools
                        and not force_answer
                        and pseudo_nudge_count < 2
                        and not pseudo_recovery_done
                    ):
                        pseudo_nudge_count += 1
                        tools = all_tools
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Do not write tool calls as text or DSML/XML. "
                                    "Use the function-calling API now "
                                    "(comfyui / local_discover / file_read / "
                                    "list_dir / bash_exec), or answer from context."
                                ),
                            }
                        )
                        continue
                    # Narrating "I'm processing…" without tools looks stuck in the UI.
                    # Never accept a status-only line as the final answer while tools
                    # are available (session bug 2026-07-28: short snippet then stop).
                    if (
                        not tool_calls_list
                        and all_tools
                        and looks_like_false_progress(text_out)
                        and false_progress_nudge_count < 4
                    ):
                        false_progress_nudge_count += 1
                        tools = all_tools
                        force_answer_sticky = False
                        messages.append(
                            {
                                "role": "assistant",
                                "content": text_out,
                            }
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Stop narrating intent. Use native function-calling "
                                    "tools **now** (computer_navigate / list_dir / "
                                    "file_read / file_write / file_edit / bash_exec / "
                                    "comfyui / local_discover / mission_start) and keep "
                                    "going until the user request is finished. "
                                    "To open a website use **computer_navigate** with a "
                                    "full https URL (gmail → https://mail.google.com). "
                                    "Do **not** reply with only a status line or thinking "
                                    "— make real tool_calls."
                                ),
                            }
                        )
                        logger.info(
                            "False-progress nudge %d/4 after step %d (no tool_calls)",
                            false_progress_nudge_count,
                            step + 1,
                        )
                        continue
                    if stream_live and produced_user_text:
                        # Hit max_tokens mid-answer → seamless continuation.
                        if (
                            round_state.hit_length_limit
                            and length_continuations < max_length_continuations
                            and not tool_calls_list
                        ):
                            length_continuations += 1
                            logger.info(
                                "Stream hit length limit (finish_reason=%s); "
                                "auto-continuing (%d/%d)",
                                round_state.finish_reason,
                                length_continuations,
                                max_length_continuations,
                            )
                            messages.append(
                                {"role": "assistant", "content": text_out}
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Your previous message was cut off by the output "
                                        "token limit. Continue exactly where you stopped — "
                                        "do not restart, renumber from scratch, or summarize "
                                        "what you already wrote. Pick up mid-sentence if needed."
                                    ),
                                }
                            )
                            tools = []  # keep producing prose
                            continue
                        return
                    if not stream_live:
                        # Final safety: never yield markup-only blobs.
                        if text_out and not _looks_like_pseudo_tools(text_out):
                            yield text_out
                            produced_user_text = True
                        if (
                            round_state.hit_length_limit
                            and length_continuations < max_length_continuations
                            and not tool_calls_list
                        ):
                            length_continuations += 1
                            messages.append(
                                {"role": "assistant", "content": text_out}
                            )
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Continue exactly where you stopped — do not restart."
                                    ),
                                }
                            )
                            tools = []
                            continue
                    return

                if not tool_calls_list or force_answer:
                    # Empty content after tools/thinking: never soft-give-up while
                    # we still have budget. DeepSeek often leaves content blank
                    # after a long reasoning stream — promote reasoning first.
                    if not produced_user_text:
                        # 1) Reasoning-only answer (common for reasoner models).
                        if reasoning_out and not _looks_like_pseudo_tools(
                            reasoning_out
                        ):
                            yield reasoning_out
                            produced_user_text = True
                            if (
                                round_state.hit_length_limit
                                and length_continuations
                                < max_length_continuations
                            ):
                                length_continuations += 1
                                messages.append(
                                    {
                                        "role": "assistant",
                                        "content": reasoning_out,
                                        "reasoning_content": reasoning_out,
                                    }
                                )
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "Continue your final answer exactly where "
                                            "you stopped. Do not restart or summarize."
                                        ),
                                    }
                                )
                                tools = []
                                force_answer_sticky = True
                                continue
                            return
                        # 2) Retry synthesis — do not abandon the user mid-task.
                        if (
                            empty_answer_retries < max_empty_answer_retries
                            and not is_final_step
                        ):
                            empty_answer_retries += 1
                            force_answer_sticky = True
                            tools = []
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "You gathered context (tools and/or thinking) "
                                        "but returned no final answer text. "
                                        "Write the complete final answer now as plain "
                                        "chat content — full review, not a stub. "
                                        "Do not call tools."
                                    ),
                                }
                            )
                            logger.info(
                                "Empty answer retry %d/%d after step %d",
                                empty_answer_retries,
                                max_empty_answer_retries,
                                step + 1,
                            )
                            continue
                        # 3) Last resort only after retries exhausted.
                        yield (
                            "I gathered context but the model returned an empty "
                            "final message after several retries. Please resend "
                            "or ask me to continue from where I left off."
                        )
                    return

                # Filter out exact repeats of prior tool calls this turn.
                fresh_calls = normalize_tool_calls(
                    filter_fresh_tool_calls(tool_calls_list, seen_fps)
                )
                if not fresh_calls:
                    # Model is looping the same tools — feed cached results, nudge
                    # different actions. Only force-answer after repeated loops with
                    # no unfinished mission/open work.
                    looped = normalize_tool_calls(tool_calls_list)
                    messages.append(
                        build_assistant_api_message(
                            content=collected.get("content"),
                            tool_calls=looped,
                            reasoning_content=reasoning_out or "",
                        )
                    )
                    for tc in looped:
                        fp = _tool_call_fingerprint(tc)
                        cached = result_cache.get(fp, "(already retrieved)")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": cached,
                            }
                        )
                    loop_hits = int(getattr(runtime, "_fingerprint_loop_hits", 0) or 0) + 1
                    runtime._fingerprint_loop_hits = loop_hits
                    unfinished_loop = turn_has_unfinished_work(
                        runtime,
                        session_id=session_id,
                        tools_enabled=bool(all_tools),
                        tool_steps_this_turn=tool_batches_this_turn,
                        open_tasks=open_tasks_for_wall or None,
                    )
                    if unfinished_loop and loop_hits < 3 and all_tools:
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Those exact tool calls already ran (results above). "
                                    "Do not repeat them. Use different paths/args, "
                                    "mission_update / mission_verify, file_edit, or "
                                    "spread_run/job_run — and finish the remaining work."
                                ),
                            }
                        )
                        tools = all_tools
                        continue
                    # Jump toward final answer on next iteration.
                    tools = []
                    continue

                messages.append(
                    build_assistant_api_message(
                        content=collected.get("content"),
                        tool_calls=fresh_calls,
                        # DeepSeek thinking mode: MUST pass reasoning back on tool turns.
                        reasoning_content=reasoning_out or "",
                    )
                )

                batch_tool_msgs: list[dict[str, Any]] = []
                async for event, tool_msg in execute_tool_calls(runtime,
                    fresh_calls,
                    seen_fps=seen_fps,
                    result_cache=result_cache,
                ):
                    if event.startswith("@@"):
                        yield event
                    if tool_msg:
                        messages.append(tool_msg)
                        batch_tool_msgs.append(tool_msg)

                logger.debug(
                    "ReAct step %d executed %d tool call(s)",
                    step + 1,
                    len(fresh_calls),
                )
                tool_batches_this_turn += 1
                tool_batches_in_epoch += 1
                if is_productive_tool_batch(batch_tool_msgs):
                    productive_in_epoch += 1
                # CUA macro extraction from successful computer chains
                with suppress(Exception):
                    from remedy.core.metabolism.cua_macros import get_cua_macros
                    from remedy.core.turn_context import current_turn_tool_steps

                    steps_tc = current_turn_tool_steps(runtime)
                    if isinstance(steps_tc, list) and steps_tc:
                        get_cua_macros().observe_chain(
                            [
                                {
                                    "tool": s.get("tool"),
                                    "args": s.get("args") or {},
                                }
                                for s in steps_tc
                                if isinstance(s, dict)
                            ],
                            success=True,
                        )

                # Speed: denser parallel batches without reducing agency.
                if (
                    not force_answer
                    and all_tools
                    and is_serial_explore_batch(fresh_calls)
                ):
                    serial_explore_streak += 1
                else:
                    serial_explore_streak = 0
                if (
                    not speed_batch_nudge_done
                    and not force_answer
                    and all_tools
                    and serial_explore_streak >= 3
                ):
                    speed_batch_nudge_done = True
                    messages.append(speed_batch_nudge_message())
                    logger.info(
                        "Speed batch nudge after %d serial explore steps (step %d)",
                        serial_explore_streak,
                        step + 1,
                    )

                # Soft recovery: if tools failed or search empty, nudge once.
                if (
                    not recovery_nudge_done
                    and not force_answer
                    and batch_has_tool_errors(batch_tool_msgs)
                ):
                    recovery_nudge_done = True
                    empty = batch_has_empty_search(batch_tool_msgs)
                    need_appr = batch_has_approval_required(batch_tool_msgs)
                    messages.append(
                        recovery_nudge_message(empty_search=empty, approval=need_appr)
                    )
                    logger.info(
                        "Injected tool recovery nudge after step %d "
                        "(empty_search=%s approval=%s)",
                        step + 1,
                        empty,
                        need_appr,
                    )
                    with suppress(Exception):
                        runtime._maybe_auto_checkpoint(
                            reason="recovery",
                            title="After tool failure",
                            force=True,
                        )
                # Mission done-gate: steps done but verify not passed → force verify
                with suppress(Exception):
                    from remedy.core.mission import MissionStore

                    home = getattr(getattr(runtime, "config", None), "home_dir", None)
                    mid = str(getattr(runtime, "_session_id", "") or "") or None
                    m = MissionStore(home).latest(mid)
                    if (
                        m is not None
                        and m.status == "active"
                        and m.verify_command
                        and m.verify_status != "passed"
                        and m.steps
                        and all(s.status in ("done", "skipped") for s in m.steps)
                        and not force_answer
                        and not getattr(runtime, "_mission_gate_nudge_done", False)
                    ):
                        runtime._mission_gate_nudge_done = True
                        messages.append(
                            mission_verify_gate_message(m.verify_command)
                        )
                        logger.info("Injected mission verify gate nudge")
                with suppress(Exception):
                    runtime._maybe_auto_checkpoint(reason="auto")
                if is_final_step:
                    with suppress(Exception):
                        md = runtime._maybe_auto_checkpoint(
                            reason="step_wall",
                            title="Absolute safety step ceiling",
                            force=True,
                        )
                        if md:
                            yield "@@checkpoint"

        # Exhausted absolute safety steps without a streamed answer.
        if not produced_user_text:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Using all tool results and context above, write the "
                        "complete final answer now. Be thorough — do not give a "
                        "one-line stub or say you cannot answer if context exists."
                    ),
                }
            )
            messages[:] = ensure_tool_call_pairings(messages)
            _bind = get_llm_binding(runtime)
            _adapter = _bind.adapter()
            headers = _adapter.auth_headers(_bind.api_key)
            endpoint = _adapter.chat_endpoint(_bind.base_url)
            use_openai_sse = bool(
                getattr(_adapter, "uses_openai_sse", True)
            )
            body = _adapter.build_body(
                model=_bind.model,
                messages=messages,
                tools=None,
                stream=use_openai_sse,
                thinking_level=getattr(runtime, "_thinking_level", "high"),
            )
            try:
                body = sanitize_chat_body(body if isinstance(body, dict) else {})
            except Exception as sanitize_exc:
                logger.error(
                    "provider sanitize failed (aborting LLM call): %s",
                    sanitize_exc,
                )
                raise RuntimeError(
                    "Refusing to send chat to provider: sanitization failed."
                ) from sanitize_exc
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=900, sock_read=900)
                ) as http2, http2.post(
                    endpoint, headers=headers, json=body
                ) as resp:
                    if resp.status == 200:
                        headers_map = getattr(resp, "headers", None) or {}
                        content_type = str(
                            headers_map.get("Content-Type")
                            or headers_map.get("content-type")
                            or ""
                        ).lower()
                        if use_openai_sse or "event-stream" in content_type:
                            async for line in resp.content:
                                line_text = line.decode("utf-8").strip()
                                if not line_text or line_text.startswith(":"):
                                    continue
                                if line_text == "data: [DONE]":
                                    break
                                if line_text.startswith("data: "):
                                    line_text = line_text[6:]
                                try:
                                    chunk = json.loads(line_text)
                                except json.JSONDecodeError:
                                    continue
                                delta = (chunk.get("choices") or [{}])[0].get(
                                    "delta"
                                ) or {}
                                piece = delta.get("content")
                                if piece:
                                    produced_user_text = True
                                    yield piece
                                # Also surface trailing reasoning as answer if
                                # content stayed empty (DeepSeek reasoner).
                                reason = (
                                    delta.get("reasoning_content")
                                    or delta.get("reasoning")
                                )
                                if (
                                    not piece
                                    and isinstance(reason, str)
                                    and reason
                                ):
                                    produced_user_text = True
                                    yield reason
                        else:
                            data = await resp.json()
                            parsed = _adapter.extract_response(data)
                            piece = parsed.get("content") or parsed.get(
                                "reasoning_content"
                            )
                            if piece:
                                produced_user_text = True
                                yield str(piece)
            except Exception:
                logger.debug("final synthesis failed", exc_info=True)
        if not produced_user_text:
            yield (
                "I finished the tool loop but still have no final model text. "
                "Ask me to **continue** or restate the request and I will resume "
                "from the context already gathered."
            )
        # Compound learning + speculative warm for next turn
        from remedy.core.agent_post_turn import schedule_post_turn_prep

        with suppress(Exception):
            runtime._last_assistant_text = "".join(assistant_text_acc)[-12000:]
        schedule_post_turn_prep(
            runtime,
            message=message or "",
            session_id=session_id,
        )
    except Exception as e:
        logger.exception("LLM stream failed")
        # Never leave the user with only a stack-looking error — give a path forward.
        yield (
            f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]\n\n"
            "Something went wrong talking to the model mid-turn. "
            "Try again, switch model, or ask a narrower question. "
            "Your session history is intact."
        )


