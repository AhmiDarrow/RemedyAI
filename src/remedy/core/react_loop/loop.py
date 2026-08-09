"""ReAct LLM stream loop (implementation).

Package layout: ``remedy.core.react_loop`` — this module holds ``call_llm_stream``.
Fatal-error helpers live in ``react_loop.errors``. Prefer importing from
``remedy.core.react_loop`` or the compatibility shim ``agent_react_loop``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

import aiohttp

from remedy.core.agent_tool_batch import execute_tool_calls
from remedy.core.provider_sanitize import sanitize_chat_body
from remedy.core.react_loop.binding import (
    provider_bits as _provider_bits_fn,
)
from remedy.core.react_loop.binding import (
    rearm_agency_tools as _rearm_agency_tools_fn,
)
from remedy.core.react_loop.binding import (
    resolve_and_apply_tools as _resolve_and_apply_tools_fn,
)
from remedy.core.react_loop.build_request import build_step_request_body
from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error as _is_fatal_llm_api_error,
)
from remedy.core.react_loop.recovery import (
    fatal_model_error_message,
    repeated_provider_error_message,
)
from remedy.core.react_loop.stream_consume import consume_llm_http_response
from remedy.core.react_loop.tool_batch import (
    apply_build_engine_after_batch,
    inject_phase_nudge,
    record_tool_batch_stats,
)
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _looks_like_pseudo_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    agency_rearm_nudge_message,
    agency_tool_promise_claim,
    batch_has_approval_required,
    batch_has_empty_or_spam_write,
    batch_has_empty_search,
    batch_has_tool_errors,
    epoch_continue_message,
    is_serial_explore_batch,
    looks_like_false_progress,
    looks_like_leaked_scratchpad,
    mission_verify_gate_message,
    post_tools_user_summary_nudge,
    recovery_nudge_message,
    speed_batch_nudge_message,
    strip_stream_status_noise,
    strip_tool_markup,
    turn_has_unfinished_work,
)
from remedy.core.react_stream import (
    StreamRoundState,
    build_assistant_api_message,
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    finalize_round_text,
    normalize_tool_calls,
    repair_reasoning_content_in_messages,
    repair_tool_arguments_in_messages,
    strip_broken_tool_call_turns,
)

logger = logging.getLogger(__name__)



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
        from remedy.core.agent_react_preamble import (
            prepare_turn_preamble,
            yield_preamble_events,
        )
        from remedy.core.llm_binding import LlmBinding, get_llm_binding, set_llm_binding

        prep = await prepare_turn_preamble(
            runtime,
            message,
            session_id,
            attachments,
            plan_mode=plan_mode,
        )
        if prep.early_reply:
            yield prep.early_reply
            return

        # Local agent bootstrap: finish clear create-jobs without waiting on
        # a 7B model that monologues instead of calling file_write.
        with suppress(Exception):
            from remedy.core.local_agent_optimize import maybe_bootstrap_local_create

            boot = await maybe_bootstrap_local_create(runtime, message or "")
            if boot:
                yield boot
                with suppress(Exception):
                    from remedy.core.agent_post_turn import schedule_post_turn_prep

                    schedule_post_turn_prep(
                        runtime, message=message or "", session_id=session_id
                    )
                return
        async for _pev in yield_preamble_events(prep):
            yield _pev
        messages = prep.messages
        history = prep.history
        all_tools = prep.all_tools
        tools = prep.tools
        browse_pre_url = prep.browse.browse_pre_url
        clear_goals_only = prep.clear_goals_only
        pure_action_kick = prep.pure_action_kick
        open_only_browse = prep.browse.open_only_browse
        page_interaction = prep.browse.page_interaction

        seen_fps: set[str] = set()
        result_cache: dict[str, str] = {}
        produced_user_text = False
        pseudo_recovery_done = False
        pseudo_nudge_count = 0
        # Nudge once when the model claims progress without native tool_calls.
        false_progress_nudge_count = 0
        # After false-progress cap: still refuse intent monologues on local build/continue.
        zero_tools_hard_block_count = 0
        # Identical monologue fingerprint loop (screenshot 2026-08-08 triple repeat).
        mono_fp_last = ""
        mono_fp_hits = 0
        mono_explore_injected = False
        # Local 7B tutorial essays (RPB markdown / pip install / fenced code) — not work.
        # Reject monologue finals after a tool-heavy turn (auth/DSML recovery bug).
        scratchpad_nudge_count = 0
        tools_executed_this_turn = 0
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
        # Cap length continuations — unbounded 10k burned huge token budgets.
        # Local hosts: length-continue loops turn short summaries into 30k+ char
        # "Done." spam (simple C e2e). Cap hard; green path never continues.
        max_length_continuations = 16
        with suppress(Exception):
            from remedy.core.llm_binding import get_llm_binding as _glb
            from remedy.core.local_agent_optimize import is_local_binding

            _b0 = _glb(runtime)
            if is_local_binding(_b0.provider, _b0.model, _b0.base_url):
                max_length_continuations = 2
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
        # Cap agency re-arms / green-gate re-opens (token burn safety).
        agency_rearm_count = 0
        max_agency_rearms = 6
        green_gate_reopen_count = 0
        max_green_gate_reopens = 6
        # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
        auth_refresh_done = False
        # Multi-epoch: soft walls compact; absolute total is safety only.
        max_total = max(1, int(getattr(runtime, "_max_react_steps", 10_000) or 10_000))
        epoch_size = max(
            16, int(getattr(runtime, "_epoch_react_steps", 256) or 256)
        )
        auto_continue = bool(getattr(runtime, "_react_auto_continue", True))
        # Single source of truth (REACT_MAX_STALE_EPOCHS default 8 — not 2)
        from remedy.core.react_turn import (
            TurnState,
            effective_stale_epochs,
            is_disconnect_error,
            mid_turn_fit_messages,
            synthesize_from_tools,
        )

        max_stale_epochs = effective_stale_epochs(runtime)
        epoch_index = 1
        productive_in_epoch = 0
        tool_batches_in_epoch = 0
        stale_epochs = 0
        tool_batches_this_turn = 0
        open_tasks_for_wall: list[str] = []
        with suppress(Exception):
            brief = getattr(runtime, "_session_brief", None)
            if brief is not None:
                open_tasks_for_wall = list(getattr(brief, "open_tasks", None) or [])
        # Pure action kicks must NOT resume older open_tasks from history
        if pure_action_kick or clear_goals_only or browse_pre_url or page_interaction:
            open_tasks_for_wall = []

        # Shared turn control plane (deep-dive #1)
        turn = TurnState(
            message=message or "",
            session_id=str(session_id or ""),
            plan_mode=bool(plan_mode),
            all_tools=list(all_tools or []),
            tools=tools,
            run_until_done=bool(tools),
        )

        # Machine build engine — supervise construction / task turns.
        build_state = None
        with suppress(Exception):
            from remedy.core.build_engine import begin_build_turn, build_protocol_block

            build_state = begin_build_turn(runtime, message or "")
            if build_state is not None and build_state.active:
                runtime._build_protocol_pending = build_protocol_block(build_state)
        # Frontier continue: brief + ledger inject (no local harness thrash)
        with suppress(Exception):
            from remedy.core.build_engine import frontier_continue_inject

            _fc = frontier_continue_inject(runtime, message or "")
            if _fc is not None:
                runtime._frontier_continue_pending = _fc  # type: ignore[attr-defined]

        def _provider_bits() -> tuple[str, str, str]:
            return _provider_bits_fn(runtime)

        def _resolve_and_apply(*, step_index: int = 0) -> None:
            """Single tool-arming path (deep-dive #2)."""
            nonlocal tools, run_until_done
            tools, run_until_done = _resolve_and_apply_tools_fn(
                runtime=runtime,
                turn=turn,
                message=message or "",
                plan_mode=plan_mode,
                history=history,
                pure_action_kick=bool(pure_action_kick),
                clear_goals_only=bool(clear_goals_only),
                browse_pre_url=browse_pre_url,
                page_interaction=bool(page_interaction),
                open_only_browse=bool(open_only_browse),
                build_state=build_state,
                open_tasks_for_wall=open_tasks_for_wall,
                step_index=step_index,
            )

        _resolve_and_apply(step_index=0)

        def _rearm_agency_tools() -> None:
            """Re-enable tool schemas *and* long-task epoch policy."""
            nonlocal tools, run_until_done
            tools, run_until_done = _rearm_agency_tools_fn(turn)

        # Accumulated assistant text for critical verify at end
        assistant_text_acc: list[str] = []

        # L0 system fast path: model/skills/whoami/version — no frontier tokens.
        # Early exit above usually handles this; keep as safety if tier was set
        # by metabolism without early-return (e.g. harness path edge).
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

                l0 = try_l0_system_reply(
                    runtime, message or "", preclassified=True
                )
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
                    # Active agency: schemas actually sent (not merely registered).
                    # all_tools alone made coding_in_flight always true (deep-dive #7).
                    tools_armed = turn.tools_armed()  # bool(tools) only
                    coding_in_flight = run_until_done and (
                        unfinished
                        or tool_batches_this_turn > 0
                        or tools_armed
                    )
                    if coding_in_flight:
                        # Tools armed but never called this turn — stronger nudge
                        # (was dead code: `elif run_until_done and all_tools` never
                        # ran because coding_in_flight was always true when armed).
                        never_used_tools = (
                            tool_batches_this_turn <= 0
                            and bool(all_tools)
                            and not unfinished
                        )
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
                                "— re-arming tools to finish the build\n"
                            )
                            # Partner: do not stop — re-arm and keep going
                            stale_epochs = 0
                            force_answer_sticky = False
                            if all_tools:
                                tools = all_tools
                                turn.tools = all_tools
                            with suppress(Exception):
                                md = runtime._maybe_auto_checkpoint(
                                    reason="step_wall",
                                    title="Idle re-arm checkpoint",
                                    force=True,
                                )
                                if md:
                                    yield "@@checkpoint"
                            continue
                        elif never_used_tools:
                            # Model is chatting without function calls — re-arm.
                            epoch_index += 1
                            _rearm_agency_tools()
                            logger.info(
                                "ReAct tools-now nudge → epoch %d at step %d "
                                "(no tool_calls yet this turn)",
                                epoch_index,
                                step,
                            )
                            yield (
                                f"@@status:Nudge — use tools to finish "
                                f"(epoch {epoch_index}, step {step})…\n"
                            )
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
                            # Mid-turn slim runs once below (step > 0) after the
                            # epoch continue message is appended — do not slim
                            # here (was double prune/offload on every roll).
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
                            # Keep tools + run_until_done if a prior loop cleared them.
                            _rearm_agency_tools()
                    elif step >= epoch_size and not run_until_done:
                        # Pure chat (tools never enabled) — wrap up.
                        force_answer_sticky = True

                is_final_step = step >= max_total - 1
                # Machine green verify → summary-only (no more tools / write budget)
                _verify_green = bool(
                    getattr(runtime, "_build_verify_green", False)
                )
                if _verify_green:
                    force_answer_sticky = True
                    tools = []
                    turn.tools = []
                    runtime._force_tool_choice = False
                # Absolute safety wall only — soft epochs never force-answer alone.
                force_answer = (
                    is_final_step or not tools or force_answer_sticky
                )
                # Re-resolve pack each step (write-first → full after writes)
                if not force_answer and turn.all_tools and not _verify_green:
                    _resolve_and_apply(step_index=int(step))
                step_tools = None if force_answer else tools
                # Mid-turn hard fit for local (deep-dive #6) before POST
                if step > 0 and step_tools is not None:
                    prov, mod, url = _provider_bits()
                    messages[:], step_tools = mid_turn_fit_messages(
                        messages,
                        step_tools,
                        provider=prov,
                        model=mod,
                        base_url=url,
                    )
                    tools = step_tools
                    turn.tools = step_tools

                # Session id: prefer ContextVar (concurrent tabs)
                sid_mm = str(
                    getattr(runtime, "_session_id", "") or session_id or ""
                )
                with suppress(Exception):
                    from remedy.core.turn_context import turn_session_id

                    sid_mm = str(turn_session_id(runtime, session_id) or sid_mm or "")

                if (
                    force_answer
                    and step > 0
                    and length_continuations == 0
                    and not force_answer_nudge_done
                ):
                    # Once only — repeating this every step bloated context and looked stuck.
                    force_answer_nudge_done = True
                    if _verify_green:
                        # Partner: machine already verified — never ask for "thorough"
                        # multi-section essays (that caused 8k–40k DONE loops).
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Verify is GREEN. Reply with at most 6 short lines "
                                    "(files + verify result). Then stop. "
                                    "Do not repeat yourself."
                                ),
                            }
                        )
                    else:
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

                # 1) Mid-turn slim first (can drop system notes)
                if step > 0 and getattr(runtime, "_harness_mode", "auto") != "off":
                    with suppress(Exception):
                        from remedy.memory.harness.send_policy import (
                            slim_messages_mid_turn,
                        )

                        messages[:] = slim_messages_mid_turn(
                            runtime,
                            messages,
                            session_id=sid_mm,
                            tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
                        )

                # 2) Evidence inject AFTER slim, then mark (mark clears delta)
                with suppress(Exception):
                    from remedy.core.metabolism.evidence import get_evidence_ledger
                    from remedy.core.metabolism.turn import mark_model_call

                    if (
                        int(getattr(runtime, "_turn_tier", 1) or 1) >= 2
                        and tool_batches_this_turn > 0
                    ):
                        led = get_evidence_ledger(sid_mm)
                        eblock = led.pointer_block(limit=8)
                        last_eu_i = int(getattr(turn, "evidence_inject_eu", -1) or -1)
                        if eblock and led.evidence_units > last_eu_i:
                            messages.append(
                                {"role": "system", "content": eblock}
                            )
                            turn.evidence_inject_eu = led.evidence_units
                    mark_model_call(sid_mm)

                # Never send incomplete tool_calls/tool pairings (HTTP 400).
                # Also force valid JSON on every tool-call arguments blob before
                # POST (guards length-truncated streams + legacy history).
                repair_tool_arguments_in_messages(messages)
                messages[:] = ensure_tool_call_pairings(messages)
                # OpenAI-compatible providers (openai, deepseek, ollama, …) stream SSE.
                # Anthropic currently uses a single JSON response (stream=False).
                _bind = get_llm_binding(runtime)
                _adapter = _bind.adapter()
                body, headers, endpoint, use_openai_sse = build_step_request_body(
                    runtime=runtime,
                    bind=_bind,
                    adapter=_adapter,
                    messages=messages,
                    step_tools=step_tools,
                    step=int(step),
                    user_message=str(message or ""),
                )

                collected: dict[str, Any] = {"content": None, "tool_calls": None}
                round_state = StreamRoundState()

                _llm_t0 = time.perf_counter()
                # Local hosts: more attempts (refit + RMB load + disconnect)
                _local_ctx_retried = False
                _http_round_ok = False
                while not _http_round_ok:
                 try:
                  for _local_http_attempt in range(8):
                   async with http.post(
                    endpoint, headers=headers, json=body
                   ) as resp:
                    _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
                    if resp.status != 200:
                        text = await resp.text()
                        # Provider bodies can echo auth headers / key fragments.
                        try:
                            from remedy.core.metabolism.redact import redact_text

                            safe_err = redact_text(text or "")
                        except Exception:
                            safe_err = "[redacted provider error]"
                        logger.error(
                            "LLM API error %d: %s", resp.status, safe_err[:500]
                        )
                        # Local: auto-shrink + retry once on exceed_context_size
                        if (
                            resp.status == 400
                            and not _local_ctx_retried
                            and (
                                "exceed_context" in (safe_err or "").lower()
                                or "context size" in (safe_err or "").lower()
                                or "n_prompt_tokens" in (safe_err or "").lower()
                            )
                        ):
                            _local_ctx_retried = True
                            refit_ok = False
                            with suppress(Exception):
                                from remedy.core.endless_context import (
                                    fit_local_request,
                                    resolve_local_window,
                                )

                                win = resolve_local_window(
                                    provider=_bind.provider,
                                    model=_bind.model,
                                    base_url=_bind.base_url,
                                )
                                hard_win = max(4096, int(win * 0.7))
                                msgs2, tools2, _meta = fit_local_request(
                                    body.get("messages")
                                    if isinstance(body.get("messages"), list)
                                    else messages,
                                    body.get("tools")
                                    if isinstance(body.get("tools"), list)
                                    else step_tools,
                                    window=hard_win,
                                    provider=_bind.provider,
                                    model=_bind.model,
                                    coding_bias=True,
                                )
                                body = dict(body)
                                body["messages"] = msgs2
                                if tools2:
                                    body["tools"] = tools2
                                    body["tool_choice"] = "required"
                                else:
                                    body.pop("tools", None)
                                    body.pop("tool_choice", None)
                                body["max_tokens"] = min(
                                    int(body.get("max_tokens") or 512), 512
                                )
                                refit_ok = True
                                logger.warning(
                                    "Local context overflow — auto-refit retry "
                                    "(window~%s est=%s)",
                                    hard_win,
                                    _meta.get("est_after"),
                                )
                            if refit_ok:
                                yield "@@status:Context full — compressing and retrying…\n"
                                continue  # next _local_http_attempt
                        with suppress(Exception):
                            from remedy.nanoswarm import get_swarm
                            from remedy.nanoswarm.events import SwarmEvent

                            get_swarm().dispatch(
                                SwarmEvent.provider_health(
                                    provider=_bind.provider,
                                    model=_bind.model,
                                    ok=False,
                                    latency_ms=_llm_ms,
                                    error=safe_err[:200],
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
                                    # Prefer turn binding; avoid racing other tabs on
                                    # runtime._llm_api_key. Persist only as fallback.
                                    with suppress(Exception):
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
                                    # Status channel only — never user answer tokens
                                    # (session 2026-07-31 leaked [auth] into final content).
                                    yield "@@status:Refreshed xAI session; retrying request…\n"
                                    continue
                            except Exception as auth_exc:
                                logger.debug("xAI re-auth failed: %s", auth_exc)
                            # Refresh failed → clear soft-continue noise with guidance.
                            yield (
                                "@@status:xAI session expired or rejected. "
                                "Sign in again in Settings (Sign in with xAI) or "
                                "update your API key.\n"
                            )
                            # User-visible hard stop (not a monologue).
                            yield (
                                "xAI session expired or was rejected. "
                                "Sign in again under Settings (Sign in with xAI) "
                                "or update your API key, then resend."
                            )
                            return
                        # DeepSeek thinking mode: tool turns require reasoning_content.
                        if (
                            resp.status == 400
                            and "reasoning_content" in (text or "").lower()
                            and not reasoning_repair_done
                        ):
                            reasoning_repair_done = True
                            if repair_reasoning_content_in_messages(messages):
                                logger.warning(
                                    "Repaired missing reasoning_content on tool "
                                    "turns; retrying request"
                                )
                                yield (
                                    "@@status:Restored thinking-mode reasoning "
                                    "for tool turns; continuing…\n"
                                )
                                body = dict(body) if isinstance(body, dict) else {}
                                body["messages"] = messages
                                continue
                        # Truncated / invalid tool-call JSON in history (often after
                        # a long plan_save or max_tokens mid-arguments stream).
                        _tool_arg_err = resp.status == 400 and (
                            "tool argument" in (text or "").lower()
                            or "eof while parsing" in (text or "").lower()
                            or "invalid-argument" in (text or "").lower()
                            or "unmodified tool arguments" in (text or "").lower()
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
                                body = dict(body) if isinstance(body, dict) else {}
                                body["messages"] = messages
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
                                step_tools = None
                                force_answer_sticky = True
                                body = dict(body) if isinstance(body, dict) else {}
                                body["messages"] = messages
                                body.pop("tools", None)
                                body.pop("tool_choice", None)
                                continue
                            except Exception as strip_exc:
                                logger.debug(
                                    "tool-args strip failed: %s", strip_exc
                                )
                        # Fatal: wrong/missing model — do not soft-retry 16× (looks stuck).
                        if _is_fatal_llm_api_error(resp.status, text):
                            model_name = str(_bind.model or "unknown")
                            prov = str(_bind.provider or "unknown")
                            yield fatal_model_error_message(
                                status=resp.status,
                                safe_err=safe_err,
                                model_name=model_name,
                                provider=prov,
                            )
                            return
                        # Local RMB: 503 "Loading model" while weights load after
                        # restart — wait for ready and retry same body (partner path).
                        _loading = (
                            resp.status in (503, 502)
                            and (
                                "loading model" in (safe_err or "").lower()
                                or "unavailable" in (safe_err or "").lower()
                                or "not ready" in (safe_err or "").lower()
                            )
                        )
                        if _loading:
                            _load_waits = int(
                                getattr(runtime, "_rmb_load_waits", 0) or 0
                            )
                            if _load_waits < 3:
                                runtime._rmb_load_waits = _load_waits + 1
                                yield (
                                    "@@status:Local model is loading — "
                                    "waiting for RMB to become ready…\n"
                                )
                                ready = False
                                with suppress(Exception):
                                    import asyncio as _aio

                                    from remedy.runtime.rmb.service import (
                                        wait_rmb_ready,
                                    )

                                    # Poll off the event loop so we don't block
                                    # other requests for 2 minutes.
                                    _wr = await _aio.to_thread(
                                        wait_rmb_ready, None, timeout_s=90.0
                                    )
                                    ready = bool(_wr.get("ok") and _wr.get("ready"))
                                if ready:
                                    yield "@@status:Model ready — retrying…\n"
                                    continue  # same body, next HTTP attempt
                                # Fall through to soft recovery if still down
                        from remedy.core.react_turn import soft_api_recovery_action

                        # Local build: never strip tools on API blips — wait + retry
                        _local_keep_tools = False
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                is_local_binding as _ilb2,
                            )
                            from remedy.core.local_agent_optimize import (
                                message_wants_build_work as _mwb2,
                            )

                            _local_keep_tools = _ilb2(
                                _bind.provider, _bind.model, _bind.base_url
                            ) and (
                                _mwb2(message or "", history)
                                or tools_executed_this_turn > 0
                                or bool(all_tools)
                            )
                        if _local_keep_tools and api_soft_failures < 6:
                            api_soft_failures += 1
                            yield (
                                f"@@status:Local host error HTTP {resp.status} — "
                                f"waiting and retrying with tools "
                                f"({api_soft_failures}/6)…\n"
                            )
                            with suppress(Exception):
                                import asyncio as _aio

                                from remedy.runtime.rmb.service import wait_rmb_ready

                                await _aio.to_thread(
                                    wait_rmb_ready, None, timeout_s=45.0
                                )
                            body = dict(body) if isinstance(body, dict) else {}
                            body["stream"] = False
                            continue

                        _soft_act = soft_api_recovery_action(
                            force_answer_api_fail_once=force_answer_api_fail_once,
                            force_answer_sticky=force_answer_sticky,
                            api_soft_failures=api_soft_failures,
                            max_api_soft_failures=max_api_soft_failures,
                        )
                        if _soft_act == "stop":
                            if force_answer_sticky and not force_answer_api_fail_once:
                                force_answer_api_fail_once = True
                            yield repeated_provider_error_message(
                                status=resp.status,
                                safe_err=safe_err,
                            )
                            return
                        api_soft_failures += 1
                        # Transient path: rebuild no-tool body and POST force-answer.
                        # Skip for local keep-tools (handled above).
                        if _soft_act == "force_answer_rebuild" and not _local_keep_tools:
                            yield (
                                f"\n[LLM notice — HTTP {resp.status}; "
                                f"trying to finish from context "
                                f"({api_soft_failures}/{max_api_soft_failures})]\n"
                                f"{safe_err[:200]}\n"
                            )
                            tools = []
                            step_tools = None
                            force_answer_sticky = True
                            # Do NOT set force_answer_api_fail_once until this attempt fails.
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
                            try:
                                _think_r = getattr(runtime, "_thinking_level", "high")
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        is_local_binding as _ilb,
                                    )
                                    if _ilb(
                                        _bind.provider, _bind.model, _bind.base_url
                                    ):
                                        _think_r = "low"
                                body = _adapter.build_body(
                                    model=_bind.model,
                                    messages=messages,
                                    tools=None,
                                    stream=use_openai_sse,
                                    thinking_level=_think_r,
                                )
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        apply_local_body_optimize as _alo,
                                    )
                                    _body_lo = body if isinstance(body, dict) else {}
                                    with suppress(Exception):
                                        _sticky = int(
                                            getattr(runtime, "_remedy_write_budget", 0)
                                            or 0
                                        )
                                        if _sticky > 0:
                                            _body_lo = dict(_body_lo)
                                            _body_lo["_remedy_write_budget"] = _sticky
                                    body = _alo(
                                        _body_lo,
                                        provider=_bind.provider,
                                        model=_bind.model,
                                        base_url=_bind.base_url,
                                        user_message=str(message or ""),
                                        step_index=int(step),
                                        history=messages
                                        if isinstance(messages, list)
                                        else None,
                                    )
                                _local_agent = False
                                with suppress(Exception):
                                    from remedy.runtime.rmb.mode import is_rmb_provider
                                    _local_agent = is_rmb_provider(
                                        _bind.provider,
                                        getattr(_bind, "base_url", None),
                                    ) or str(_bind.provider or "").lower() in (
                                        "ollama",
                                        "llamacpp",
                                    )
                                body = sanitize_chat_body(
                                    body if isinstance(body, dict) else {},
                                    local_agent=_local_agent,
                                )
                            except Exception as _rb_exc:
                                logger.warning(
                                    "force-answer body rebuild failed: %s", _rb_exc
                                )
                            continue
                        if _local_keep_tools:
                            # Still building locally — never abandon tools mid-host-error
                            body = dict(body) if isinstance(body, dict) else {}
                            body["stream"] = False
                            continue
                        yield (
                            f"\n[LLM ERROR — HTTP {resp.status}]\n"
                            f"{safe_err[:500]}\n[END LLM ERROR]\n"
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
                    async for _tok, _user_flag in consume_llm_http_response(
                        resp,
                        round_state=round_state,
                        collected=collected,
                        adapter=_adapter,
                        bind=_bind,
                        body=body if isinstance(body, dict) else None,
                        use_openai_sse=use_openai_sse,
                        stream_live=stream_live,
                    ):
                        if _user_flag:
                            produced_user_text = True
                        yield _tok

                    content_parts = round_state.content_parts
                    reasoning_parts = round_state.reasoning_parts

                   # Successful HTTP round — leave the local retry loop
                   _http_round_ok = True
                   break
                  else:
                    # for-loop exhausted without break (both attempts failed status)
                    yield (
                        "\n[LLM ERROR] Provider request failed after retries.\n"
                        "Check model/API key in Settings and try again.\n"
                    )
                    return
                 except Exception as _stream_exc:
                  if (
                    is_disconnect_error(_stream_exc)
                    and turn.allow_disconnect_retry()
                  ):
                    turn.note_disconnect_retry()
                    logger.warning(
                        "LLM stream disconnect — non-stream retry (%s)",
                        _stream_exc,
                    )
                    yield (
                        "@@status:Connection dropped — waiting for local model, "
                        "then retrying (no user action needed)…\n"
                    )
                    # Partner: wait for RMB if mid-load, do NOT crush max_tokens
                    with suppress(Exception):
                        import asyncio as _aio

                        from remedy.runtime.rmb.service import wait_rmb_ready

                        await _aio.to_thread(
                            wait_rmb_ready, None, timeout_s=60.0
                        )
                    body = dict(body)
                    body["stream"] = False
                    with suppress(Exception):
                        # Keep write-capable budget (old path forced ≤768 and
                        # caused follow-on TOOL_ARGS_TRUNCATED / empty turns).
                        sticky = int(
                            getattr(runtime, "_remedy_write_budget", 0) or 0
                        )
                        cur = int(body.get("max_tokens") or 2048)
                        body["max_tokens"] = max(cur, sticky, 2048)
                        body["max_tokens"] = min(int(body["max_tokens"]), 8192)
                    prov, mod, url = _provider_bits()
                    messages[:], _st = mid_turn_fit_messages(
                        messages,
                        body.get("tools")
                        if isinstance(body.get("tools"), list)
                        else step_tools,
                        provider=prov,
                        model=mod,
                        base_url=url,
                    )
                    if _st is not None:
                        body["tools"] = _st
                    step_tools = _st if _st is not None else step_tools
                    collected = {"content": None, "tool_calls": None}
                    round_state = StreamRoundState()
                    continue
                  raise

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
                                str(
                                    getattr(turn, "session_id", None)
                                    or getattr(runtime, "_session_id", "")
                                    or ""
                                )
                            ).record_turn(prompt_tokens=pt, completion_tokens=ct)
                        with suppress(Exception):
                            record_usage_event(
                                session_id=str(
                                    getattr(turn, "session_id", None)
                                    or getattr(runtime, "_session_id", "")
                                    or ""
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
                # Drop auth/provider status lines if they leaked into model content
                if text_out:
                    text_out = strip_stream_status_noise(str(text_out))
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
                    and turn.allow_pseudo_recovery()
                    and not force_answer
                ):
                    recovered = _parse_pseudo_tool_calls(raw_round)
                    if recovered:
                        turn.note_pseudo_recovery()
                        pseudo_recovery_done = turn.pseudo_recoveries >= 1
                        _rearm_agency_tools()  # schemas + long-task epoch policy
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
                            empty_wr = batch_has_empty_or_spam_write(batch_tool_msgs)
                            messages.append(
                                recovery_nudge_message(
                                    empty_search=empty,
                                    approval=need_appr,
                                    empty_write=empty_wr,
                                )
                            )
                            if empty_wr:
                                # Bump write budget + force write tools for the retry
                                _rearm_agency_tools()
                                runtime._force_tool_choice = True
                                with suppress(Exception):
                                    runtime._remedy_write_budget = max(
                                        int(
                                            getattr(
                                                runtime, "_remedy_write_budget", 0
                                            )
                                            or 0
                                        ),
                                        8192,
                                    )
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        filter_tools_write_first,
                                    )

                                    tools = (
                                        filter_tools_write_first(
                                            all_tools,
                                            user_message=str(message or "build"),
                                            step_index=0,
                                        )
                                        or all_tools
                                    )
                                    turn.tools = tools
                            with suppress(Exception):
                                from remedy.core.metrics import default_registry
                                from remedy.core.session_quality import (
                                    get_session_quality,
                                )
                                from remedy.core.turn_context import turn_session_id

                                kind = (
                                    "approval"
                                    if need_appr
                                    else (
                                        "empty_write"
                                        if empty_wr
                                        else (
                                            "empty_search" if empty else "tool_error"
                                        )
                                    )
                                )
                                default_registry.counter(
                                    "remedy_tool_recovery_nudge_total", kind=kind
                                ).inc()
                                default_registry.counter(
                                    "remedy_tool_batch_errors_total"
                                ).inc()
                                get_session_quality(
                                    str(turn_session_id(runtime) or "")
                                ).record_recovery_nudge(kind=kind)
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
                        _rearm_agency_tools()
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Your last reply leaked incomplete tool markup "
                                    "(DSML/XML cut off mid-call). Do **not** write "
                                    "tool_calls as text. Call tools via the "
                                    "function-calling API now "
                                    "(file_read / list_dir / bash_exec / "
                                    "repo_search / file_edit). If work is already "
                                    "done, write a **user-facing** summary of "
                                    "results — not internal monologue."
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
                    # Local muscle vs frontier: set once per finalization path
                    _harness_on = False
                    with suppress(Exception):
                        from remedy.core.local_agent_optimize import (
                            needs_agent_harness,
                        )

                        _harness_on = needs_agent_harness(
                            _bind.provider, _bind.model, _bind.base_url
                        )
                    # After tools: refuse monologue / recovery-echo as the answer
                    # (install dogfood: 122 tools then final = "The user wants…").
                    # Local/RMB partner: never disarm tools here — that ended builds
                    # after 22 tools with empty/spam writes still on disk (2026-08-09).
                    if (
                        tools_executed_this_turn > 0
                        and looks_like_leaked_scratchpad(text_out)
                        and scratchpad_nudge_count < 4
                    ):
                        scratchpad_nudge_count += 1
                        _local_build = False
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                continue_build_nudge,
                                filter_tools_write_first,
                                is_local_binding,
                                message_wants_build_work,
                            )

                            _local_build = is_local_binding(
                                _bind.provider, _bind.model, _bind.base_url
                            ) and message_wants_build_work(message or "", history)
                        if _local_build:
                            # Keep agency: re-arm write tools, do not force_answer
                            force_answer_sticky = False
                            _rearm_agency_tools()
                            with suppress(Exception):
                                tools = (
                                    filter_tools_write_first(
                                        all_tools,
                                        user_message=str(message or "build"),
                                        step_index=0,
                                    )
                                    or all_tools
                                )
                                turn.tools = tools
                            runtime._force_tool_choice = True
                            _pr = ""
                            with suppress(Exception):
                                _pr = str(runtime.effective_project_path() or "")
                            messages.append(
                                continue_build_nudge(project_path=_pr or None)
                                if scratchpad_nudge_count >= 2
                                else post_tools_user_summary_nudge()
                            )
                            # After summary nudge, still force more tools if empty files
                            if scratchpad_nudge_count >= 2:
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "[Partner · do not stop] "
                                            "Verify disk: list_dir + file_read any 0-byte "
                                            "or broken sources, then file_write/file_edit "
                                            "real content. Illegal: empty file_write, "
                                            "import-spam loops, or ending with scratchpad."
                                        ),
                                    }
                                )
                        else:
                            force_answer_sticky = True
                            tools = []
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": text_out[:2000],
                                }
                            )
                            messages.append(post_tools_user_summary_nudge())
                        logger.info(
                            "Scratchpad final rejected after %d tools "
                            "(nudge %d/4 local=%s)",
                            tools_executed_this_turn,
                            scratchpad_nudge_count,
                            _local_build,
                        )
                        continue
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
                        _rearm_agency_tools()
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
                    # Agency fail-open (content path): "Activating skill now" etc.
                    # Must run *before* build monologue / false-progress acceptance —
                    # skill/tool promise claims get a dedicated re-arm nudge.
                    if (
                        not tool_calls_list
                        and all_tools
                        and not force_answer_sticky
                        and not is_final_step
                        and agency_tool_promise_claim(text_out, reasoning_out)
                        and agency_rearm_count < max_agency_rearms
                    ):
                        agency_rearm_count += 1
                        _rearm_agency_tools()
                        force_answer_sticky = False
                        messages.append(agency_rearm_nudge_message())
                        logger.info(
                            "Agency re-arm after tool-promise prose (step %d, %d/%d)",
                            step + 1,
                            agency_rearm_count,
                            max_agency_rearms,
                        )
                        continue
                    # Build engine: monologue without tools is illegal mid-build —
                    # but after tools already ran, a plain-language summary is OK
                    # (do not re-block legitimate finals as monologue).
                    if (
                        not tool_calls_list
                        and all_tools
                        and not force_answer_sticky
                        and not is_final_step
                        and (
                            tools_executed_this_turn <= 0
                            or looks_like_false_progress(text_out)
                        )
                    ):
                        with suppress(Exception):
                            from remedy.core.build_engine import (
                                get_build_state,
                                monologue_block_nudge,
                            )

                            bn = monologue_block_nudge(get_build_state(runtime))
                            if bn is not None:
                                _rearm_agency_tools()
                                force_answer_sticky = False
                                messages.append(bn)
                                logger.info(
                                    "Build engine monologue block (step %d)",
                                    step + 1,
                                )
                                continue
                    # Build engine GREEN/SHIP GATE: refuse final without green (+ ship)
                    if (
                        not tool_calls_list
                        and text_out
                        and (force_answer or force_answer_sticky or is_final_step)
                    ):
                        with suppress(Exception):
                            from remedy.core.build_engine import (
                                build_blocks_final_answer,
                                format_ship_report_line,
                                get_build_state,
                                unfinished_green_gate_message,
                            )

                            bst_g = get_build_state(runtime)
                            if build_blocks_final_answer(bst_g) and bst_g is not None:
                                if green_gate_reopen_count >= max_green_gate_reopens:
                                    logger.warning(
                                        "Build green-gate cap (%d) — allowing final",
                                        max_green_gate_reopens,
                                    )
                                    # Still emit ship report for observability
                                    _sr = format_ship_report_line(bst_g)
                                    if _sr:
                                        yield _sr
                                else:
                                    green_gate_reopen_count += 1
                                    if "green_gate" not in bst_g.nudges_emitted:
                                        bst_g.nudges_emitted.append("green_gate")
                                    _rearm_agency_tools()
                                    force_answer_sticky = False
                                    messages.append(unfinished_green_gate_message(bst_g))
                                    logger.info(
                                        "Build green-gate blocked final "
                                        "(step %d phase=%s %d/%d)",
                                        step + 1,
                                        bst_g.phase,
                                        green_gate_reopen_count,
                                        max_green_gate_reopens,
                                    )
                                    continue
                            elif bst_g is not None and bst_g.active:
                                # End-of-turn ship report when final is allowed
                                _sr = format_ship_report_line(bst_g)
                                if _sr:
                                    yield _sr
                    # --- Partner monologue loop breaker ---
                    # ANY step with tools armed, zero tool_calls, and text/reasoning
                    # is a monologue. Do not fall through to False-progress (that
                    # path re-fed essays and then disconnected).
                    _reasoning_only = (reasoning_out or "").strip()
                    _mono_text = (text_out or "").strip() or _reasoning_only[:500]
                    # Local harness only — frontier models self-steer; don't thrash them
                    if (
                        _harness_on
                        and not tool_calls_list
                        and all_tools
                        and tools_executed_this_turn <= 0
                        and _mono_text
                        and not force_answer_sticky
                        and len(_mono_text) >= 12
                    ):
                        _mono_break = True  # local tool-armed no-call steps
                        _proj_m = ""
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                monologue_fingerprint,
                                text_has_internal_repetition,
                            )

                            fp = monologue_fingerprint(_mono_text)
                            if fp and fp == mono_fp_last:
                                mono_fp_hits += 1
                            else:
                                mono_fp_last = fp
                                mono_fp_hits = 1
                            if text_has_internal_repetition(_mono_text):
                                mono_fp_hits = max(mono_fp_hits, 2)
                        with suppress(Exception):
                            _proj_m = str(runtime.effective_project_path() or "")
                        if _mono_break:
                            false_progress_nudge_count += 1
                            zero_tools_hard_block_count += 1
                            _rearm_agency_tools()
                            force_answer_sticky = False
                            # Write-first tool pack so next step cannot wander
                            with suppress(Exception):
                                from remedy.core.local_agent_optimize import (
                                    filter_tools_write_first,
                                )

                                tools = filter_tools_write_first(
                                    all_tools,
                                    user_message=str(message or "build"),
                                    step_index=0,
                                ) or all_tools
                                turn.tools = tools
                            runtime._force_tool_choice = True
                            # Never feed the monologue back into history (loop fuel)
                            # Inject live listing on first hit, hard write nudge after
                            if not mono_explore_injected:
                                mono_explore_injected = True
                                listing = ""
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        project_listing_snapshot,
                                    )

                                    listing = project_listing_snapshot(_proj_m)
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "[Partner · AUTO EXPLORE DONE — do not re-plan]\n"
                                            "Live project listing:\n"
                                            f"{listing}\n\n"
                                            "Next message = native tool_calls only "
                                            "(file_read / file_write / file_edit). "
                                            "Illegal: plan essays or 'I'll build…'."
                                        ),
                                    }
                                )
                                logger.info(
                                    "Monologue loop: auto-injected project listing "
                                    "(hits=%d step=%d)",
                                    mono_fp_hits,
                                    step + 1,
                                )
                            else:
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        continue_build_nudge,
                                    )

                                    messages.append(
                                        continue_build_nudge(
                                            project_path=_proj_m or None
                                        )
                                    )
                            # Internal stutter in one blob counts as multi-hit
                            if text_out and mono_fp_hits == 1:
                                with suppress(Exception):
                                    from remedy.core.local_agent_optimize import (
                                        text_has_internal_repetition,
                                    )

                                    if text_has_internal_repetition(text_out):
                                        mono_fp_hits = 2
                            logger.info(
                                "Monologue loop break hits=%d fp_nudge=%d step=%d",
                                mono_fp_hits,
                                false_progress_nudge_count,
                                step + 1,
                            )
                            # Cap: after 4 mono hits, stop streaming monologue to user
                            if mono_fp_hits >= 4:
                                yield (
                                    "@@status:Breaking monologue loop — "
                                    "forcing tools on the project tree…\n"
                                )
                            continue
                    # Narrating without tools — local harness only (frontier: trust)
                    _fp_cap = 8 if _harness_on else 1
                    if (
                        not tool_calls_list
                        and all_tools
                        and looks_like_false_progress(text_out)
                        and false_progress_nudge_count < _fp_cap
                        and tools_executed_this_turn > 0  # only after some tools ran
                    ):
                        false_progress_nudge_count += 1
                        _rearm_agency_tools()
                        force_answer_sticky = False
                        # Force tool_choice only on local muscle
                        if _harness_on:
                            runtime._force_tool_choice = True
                            with suppress(Exception):
                                from remedy.core.local_agent_optimize import (
                                    filter_tools_write_first,
                                )

                                tools = (
                                    filter_tools_write_first(
                                        all_tools,
                                        user_message=str(message or "build"),
                                        step_index=0,
                                    )
                                    or all_tools
                                )
                                turn.tools = tools
                        # Do not dump long monologue into history every time
                        if false_progress_nudge_count <= 1:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": (text_out or "")[:600],
                                }
                            )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Stop narrating intent. Use native function-calling "
                                    "tools **now** (list_dir / file_read / file_write / "
                                    "file_edit / bash_exec). Do **not** repeat the same "
                                    "status paragraph — make real tool_calls. "
                                    "Never file_write empty content or looped imports."
                                ),
                            }
                        )
                        logger.info(
                            "False-progress nudge %d/%d after step %d (no tool_calls harness=%s)",
                            false_progress_nudge_count,
                            _fp_cap,
                            step + 1,
                            _harness_on,
                        )
                        continue
                    # Exhausted soft blocks: still do NOT dump on the user.
                    # Re-inject listing + force tools and keep the loop alive.
                    if (
                        not tool_calls_list
                        and tools_executed_this_turn <= 0
                        and (
                            zero_tools_hard_block_count >= 3
                            or mono_fp_hits >= 4
                        )
                        and text_out
                        and mono_fp_hits < 8
                    ):
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                continue_build_nudge,
                                filter_tools_write_first,
                                is_local_binding,
                                message_wants_build_work,
                                project_listing_snapshot,
                            )

                            if is_local_binding(
                                _bind.provider, _bind.model, _bind.base_url
                            ) and message_wants_build_work(message or "", history):
                                _rearm_agency_tools()
                                tools = (
                                    filter_tools_write_first(
                                        all_tools,
                                        user_message=str(message or "build"),
                                        step_index=0,
                                    )
                                    or all_tools
                                )
                                turn.tools = tools
                                runtime._force_tool_choice = True
                                _pr = ""
                                with suppress(Exception):
                                    _pr = str(
                                        runtime.effective_project_path() or ""
                                    )
                                if not mono_explore_injected:
                                    mono_explore_injected = True
                                    messages.append(
                                        {
                                            "role": "user",
                                            "content": (
                                                "[Partner · LIVE TREE]\n"
                                                f"{project_listing_snapshot(_pr)}\n\n"
                                                "Next reply = tool_calls only."
                                            ),
                                        }
                                    )
                                else:
                                    messages.append(
                                        continue_build_nudge(project_path=_pr or None)
                                    )
                                mono_fp_hits += 1
                                yield (
                                    "@@status:Still no tools from model — "
                                    "forcing write tools again…\n"
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
                        _verify_green = bool(
                            getattr(runtime, "_build_verify_green", False)
                        )
                        # Frontier: no local end-block thrash — yield and finish
                        if (
                            not _harness_on
                            and text_out
                            and not tool_calls_list
                            and not _verify_green
                        ):
                            if text_out and not _looks_like_pseudo_tools(text_out):
                                yield text_out
                                produced_user_text = True
                            if (
                                round_state.hit_length_limit
                                and length_continuations < max_length_continuations
                            ):
                                length_continuations += 1
                                messages.append(
                                    {"role": "assistant", "content": text_out}
                                )
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "Continue exactly where you stopped — "
                                            "do not restart."
                                        ),
                                    }
                                )
                                tools = []
                                continue
                            return
                        # After machine green: accept short summary; do not rearm build
                        if _verify_green and text_out and not tool_calls_list:
                            # Always collapse after green — never length-continue
                            # (partner: 41k char DONE loops after simple C verify).
                            slice_ = (text_out or "").strip()
                            # First non-empty paragraph / hard cap
                            if len(slice_) > 900:
                                # Prefer first block before repeated "Done." waves
                                low = slice_.lower()
                                cut = -1
                                for marker in (
                                    "\n\ndone.",
                                    "\n</think>",
                                    "\n**summary",
                                    "all requirements verified",
                                ):
                                    i = low.find(marker, 80)
                                    if i > 0:
                                        cut = i if cut < 0 else min(cut, i)
                                if cut > 60:
                                    slice_ = slice_[:cut].rstrip()
                                else:
                                    slice_ = slice_[:900].rstrip()
                            if slice_ and not _looks_like_pseudo_tools(slice_):
                                yield slice_
                                produced_user_text = True
                            logger.info(
                                "Green summary finalized (raw_len=%d out_len=%d)",
                                len(text_out or ""),
                                len(slice_),
                            )
                            return
                        # Local build: refuse to end the turn after tools if the
                        # "final" is still false-progress / scratchpad / pseudo tools.
                        _block_local_end = False
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                continue_build_nudge,
                                filter_tools_write_first,
                                is_local_binding,
                                message_wants_build_work,
                            )

                            if (
                                not _verify_green
                                and is_local_binding(
                                    _bind.provider, _bind.model, _bind.base_url
                                )
                                and message_wants_build_work(message or "", history)
                                and tools_executed_this_turn > 0
                                and not tool_calls_list
                                and (
                                    looks_like_leaked_scratchpad(text_out or "")
                                    or looks_like_false_progress(text_out or "")
                                    or (
                                        text_out
                                        and _looks_like_pseudo_tools(text_out)
                                    )
                                )
                                and scratchpad_nudge_count < 6
                            ):
                                _block_local_end = True
                                scratchpad_nudge_count += 1
                                _rearm_agency_tools()
                                force_answer_sticky = False
                                runtime._force_tool_choice = True
                                tools = (
                                    filter_tools_write_first(
                                        all_tools,
                                        user_message=str(message or "build"),
                                        step_index=0,
                                    )
                                    or all_tools
                                )
                                turn.tools = tools
                                _pr = ""
                                with suppress(Exception):
                                    _pr = str(
                                        runtime.effective_project_path() or ""
                                    )
                                messages.append(
                                    continue_build_nudge(project_path=_pr or None)
                                )
                                logger.info(
                                    "Local build end blocked — rearm tools "
                                    "(tools_run=%d nudge=%d)",
                                    tools_executed_this_turn,
                                    scratchpad_nudge_count,
                                )
                        if _block_local_end:
                            continue
                        # Final safety: never yield markup-only blobs.
                        if text_out and not _looks_like_pseudo_tools(text_out):
                            yield text_out
                            produced_user_text = True
                        if (
                            round_state.hit_length_limit
                            and length_continuations < max_length_continuations
                            and not tool_calls_list
                        ):
                            # Stop length-continue when the blob is already a loop
                            _skip_len = False
                            with suppress(Exception):
                                from remedy.core.local_agent_optimize import (
                                    text_has_internal_repetition,
                                )

                                _skip_len = text_has_internal_repetition(
                                    text_out or ""
                                )
                            if _skip_len:
                                logger.info(
                                    "Skip length-continue — repetitive monologue"
                                )
                                return
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

                # Agency fail-open (reasoning / empty-content path): model
                # narrated skill/tool intent only in reasoning_content, or
                # content was stripped — re-arm tools instead of ending.
                if (
                    not tool_calls_list
                    and all_tools
                    and not force_answer_sticky
                    and not is_final_step
                    and agency_tool_promise_claim(text_out, reasoning_out)
                    and agency_rearm_count < max_agency_rearms
                ):
                    agency_rearm_count += 1
                    _rearm_agency_tools()
                    force_answer_sticky = False
                    messages.append(agency_rearm_nudge_message())
                    logger.info(
                        "Agency re-arm after tool-promise prose (step %d, %d/%d)",
                        step + 1,
                        agency_rearm_count,
                        max_agency_rearms,
                    )
                    continue

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
                if fresh_calls:
                    tools_executed_this_turn += len(fresh_calls)
                    # Real tools landed — clear monologue force flag
                    with suppress(Exception):
                        runtime._force_tool_choice = False
                    mono_fp_hits = 0
                    mono_fp_last = ""
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
                    loop_hits = turn.note_fingerprint_loop()
                    unfinished_loop = turn_has_unfinished_work(
                        runtime,
                        session_id=session_id,
                        tools_enabled=bool(all_tools),
                        tool_steps_this_turn=tool_batches_this_turn,
                        open_tasks=open_tasks_for_wall or None,
                    )
                    # Long review/implement: allow more recovery loops before
                    # force-answer (was 3 — multi-step builds hit the wall early).
                    max_loop_hits = 8 if (run_until_done or unfinished_loop) else 3
                    if (
                        (unfinished_loop or run_until_done)
                        and loop_hits < max_loop_hits
                        and all_tools
                    ):
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
                        _rearm_agency_tools()
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

                _bdelta, _pdelta = record_tool_batch_stats(
                    turn=turn,
                    fresh_calls=fresh_calls,
                    batch_tool_msgs=batch_tool_msgs,
                    step=step,
                )
                tool_batches_this_turn += _bdelta
                tool_batches_in_epoch += _bdelta
                productive_in_epoch += _pdelta
                inject_phase_nudge(turn, messages)
                # Machine build engine: syntax gate + auto-verify + force nudges
                async for _bst_ev in apply_build_engine_after_batch(
                    runtime=runtime,
                    messages=messages,
                    fresh_calls=fresh_calls,
                    batch_tool_msgs=batch_tool_msgs,
                    rearm_agency=_rearm_agency_tools,
                ):
                    if _bst_ev:
                        yield _bst_ev
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
                    try:
                        from remedy.core.logging import hot_debug_enabled

                        if hot_debug_enabled():
                            logger.debug(
                                "Speed batch nudge after %d serial explore steps (step %d)",
                                serial_explore_streak,
                                step + 1,
                            )
                    except Exception:
                        pass

                # Soft recovery: if tools failed or search empty, nudge once.
                if (
                    not recovery_nudge_done
                    and not force_answer
                    and batch_has_tool_errors(batch_tool_msgs)
                ):
                    recovery_nudge_done = True
                    empty = batch_has_empty_search(batch_tool_msgs)
                    need_appr = batch_has_approval_required(batch_tool_msgs)
                    empty_wr = batch_has_empty_or_spam_write(batch_tool_msgs)
                    messages.append(
                        recovery_nudge_message(
                            empty_search=empty,
                            approval=need_appr,
                            empty_write=empty_wr,
                        )
                    )
                    if empty_wr:
                        _rearm_agency_tools()
                        runtime._force_tool_choice = True
                        with suppress(Exception):
                            runtime._remedy_write_budget = max(
                                int(getattr(runtime, "_remedy_write_budget", 0) or 0),
                                8192,
                            )
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                filter_tools_write_first,
                            )

                            tools = (
                                filter_tools_write_first(
                                    all_tools,
                                    user_message=str(message or "build"),
                                    step_index=0,
                                )
                                or all_tools
                            )
                            turn.tools = tools
                    # Trust/speed: surface batch recovery on quality + metrics so
                    # dashboards and continuity remedies see fail→recover loops.
                    with suppress(Exception):
                        from remedy.core.metrics import default_registry
                        from remedy.core.session_quality import get_session_quality
                        from remedy.core.turn_context import turn_session_id

                        kind = (
                            "approval"
                            if need_appr
                            else (
                                "empty_write"
                                if empty_wr
                                else ("empty_search" if empty else "tool_error")
                            )
                        )
                        default_registry.counter(
                            "remedy_tool_recovery_nudge_total", kind=kind
                        ).inc()
                        default_registry.counter(
                            "remedy_tool_batch_errors_total"
                        ).inc()
                        sid_r = str(turn_session_id(runtime) or "")
                        get_session_quality(sid_r).record_recovery_nudge(kind=kind)
                    try:
                        from remedy.core.logging import hot_debug_enabled

                        if hot_debug_enabled():
                            logger.debug(
                                "Injected tool recovery nudge after step %d "
                                "(empty_search=%s approval=%s empty_write=%s)",
                                step + 1,
                                empty,
                                need_appr,
                                empty_wr,
                            )
                    except Exception:
                        pass
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
                    mid = str(
                        getattr(turn, "session_id", None)
                        or getattr(runtime, "_session_id", "")
                        or ""
                    ) or None
                    m = MissionStore(home).latest(mid)
                    if (
                        m is not None
                        and m.status == "active"
                        and m.verify_command
                        and m.verify_status != "passed"
                        and m.steps
                        and all(s.status in ("done", "skipped") for s in m.steps)
                        and not force_answer
                        and not turn.mission_gate_nudge_done
                    ):
                        turn.mission_gate_nudge_done = True
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
                _la = False
                with suppress(Exception):
                    from remedy.runtime.rmb.mode import is_rmb_provider

                    _la = is_rmb_provider(
                        _bind.provider, getattr(_bind, "base_url", None)
                    ) or str(_bind.provider or "").lower() in (
                        "ollama",
                        "llamacpp",
                    )
                body = sanitize_chat_body(
                    body if isinstance(body, dict) else {},
                    local_agent=_la,
                )
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
            # Deterministic summary when tools ran but model text is empty (#8)
            if turn.tools_executed > 0 or tool_batches_this_turn > 0:
                summary = synthesize_from_tools(
                    messages,
                    paths_written=turn.paths_written,
                )
                produced_user_text = True
                yield summary
            else:
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
        # After tools: prefer synthesis over raw exception as the main answer (#4/#8)
        try:
            from remedy.core.react_turn import (
                is_disconnect_error as _is_disc,
            )
            from remedy.core.react_turn import (
                synthesize_from_tools as _synth,
            )

            _turn = turn  # type: ignore[name-defined]
            _msgs = messages  # type: ignore[name-defined]
            if _turn.tools_executed > 0 or _turn.tool_batches > 0:
                # Partner: never end the turn asking the user to resend.
                # Restart RMB if needed and complete a short status ourselves.
                yield "@@status:Host blip after tools — recovering RMB and finishing…\n"
                _finished = False
                if _is_disc(e) or True:
                    for _attempt in range(3):
                        try:
                            import asyncio as _aio

                            from remedy.runtime.rmb.service import wait_rmb_ready

                            _wr = await _aio.to_thread(
                                wait_rmb_ready, None, timeout_s=90.0
                            )
                            if not _wr.get("ok"):
                                continue
                            import aiohttp as _ah

                            from remedy.core.llm_binding import get_llm_binding

                            _b2 = get_llm_binding(runtime)
                            _ad2 = _b2.adapter()
                            _fin_msgs = list(_msgs) + [
                                {
                                    "role": "user",
                                    "content": (
                                        "Tools already ran. In ≤6 short bullets: "
                                        "what files exist / changed, and the single "
                                        "next file_write or file_edit to do. "
                                        "No essays. Prefer tool_calls if you can."
                                    ),
                                }
                            ]
                            _body2 = _ad2.build_body(
                                model=_b2.model,
                                messages=_fin_msgs,
                                tools=None,
                                stream=False,
                                thinking_level="low",
                            )
                            if isinstance(_body2, dict):
                                _body2["stream"] = False
                                _body2["max_tokens"] = min(
                                    int(_body2.get("max_tokens") or 512), 768
                                )
                            _ep = _ad2.chat_endpoint(_b2.base_url)
                            _hdr = _ad2.auth_headers(_b2.api_key)
                            async with (
                                _ah.ClientSession() as _sess,
                                _sess.post(
                                    _ep,
                                    headers=_hdr,
                                    json=_body2,
                                    timeout=_ah.ClientTimeout(total=180),
                                ) as _resp,
                            ):
                                    if _resp.status == 200:
                                        _data = await _resp.json()
                                        _txt = (
                                            (
                                                (
                                                    (_data.get("choices") or [{}])[0]
                                                    .get("message")
                                                    or {}
                                                ).get("content")
                                            )
                                            or ""
                                        )
                                        if _txt.strip():
                                            yield "\n" + _txt.strip() + "\n"
                                            _finished = True
                                            break
                                    if _resp.status in (502, 503):
                                        continue
                        except Exception as _rec_exc:
                            logger.warning(
                                "post-tool recovery attempt %s failed: %s",
                                _attempt + 1,
                                _rec_exc,
                            )
                            continue
                if not _finished:
                    # Last resort: silent synthesis (no "say continue")
                    yield _synth(
                        _msgs,
                        paths_written=_turn.paths_written,
                    )
                    yield (
                        "\n\n@@status:Host recovered tool progress above; "
                        "continuing on next model step is automatic if you send "
                        "any short ack — history is intact.\n"
                    )
                return
        except Exception:
            pass
        # Disconnect with zero tools: hard-restart RMB then stop cleanly
        if is_disconnect_error(e):
            with suppress(Exception):
                import asyncio as _aio

                from remedy.runtime.rmb.service import wait_rmb_ready

                yield "@@status:Model connection lost — restarting RMB…\n"
                await _aio.to_thread(wait_rmb_ready, None, timeout_s=90.0)
            yield (
                "\n@@status:Local host rechecked. "
                "History intact — next message continues the build.\n"
            )
            return
        yield (
            f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]\n\n"
            "History is intact; next message continues the build.\n"
        )


