"""ReAct turn prelude — L0, assistant fast path, clear-goals, browse, kick.

Looks up patched names on ``remedy.core.react_loop.loop`` at call time.
Mutates bag ``s``; sets ``s.turn_complete`` when the turn finishes early.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


async def run_react_prelude(s: Any) -> AsyncIterator[str]:
    """Yield tokens for early short-circuits; may set ``s.turn_complete``."""
    from remedy.core.react_loop.loop_bindings import bind_loop_tuple
    (aiohttp, LlmBinding, get_llm_binding, set_llm_binding, build_step_request_body, apply_build_engine_after_batch, _turn_tier_of, _resolve_and_apply_tools_fn, _wait_rmb_ready_abortable, consume_llm_http_response, sanitize_chat_body, repair_reasoning_content_in_messages, repair_tool_arguments_in_messages, strip_broken_tool_call_turns, _sleep_abortable, _http_session, _log_llm, _stopped_note, _take_nudges, _steer_message, _browse_tool_ok, execute_tool_calls, _provider_bits_fn, _rearm_agency_tools_fn, _RATE_LIMIT_MAX_RETRIES, _is_rate_limited, _pace_before_request, _rate_limit_wait, _await_or_abort, inject_phase_nudge, record_tool_batch_stats, _is_billing_llm_api_error, _is_fatal_llm_api_error, _is_thinking_tool_choice_error, fatal_billing_error_message, fatal_model_error_message, repeated_provider_error_message, _TOOL_RESULT_CHAR_CAP, _looks_like_pseudo_tools, _parse_pseudo_tool_calls, _tool_call_fingerprint, agency_rearm_nudge_message, agency_tool_promise_claim, batch_has_approval_required, batch_has_empty_or_spam_write, batch_has_empty_search, batch_has_tool_errors, clip_appended_source_dump, collapse_repeated_sentences, epoch_continue_message, is_serial_explore_batch, looks_like_false_progress, looks_like_leaked_scratchpad, looks_like_safety_refusal, message_asks_to_stop, mission_verify_gate_message, post_tools_user_summary_nudge, recovery_nudge_message, speed_batch_nudge_message, strip_stream_status_noise, strip_tool_markup, turn_has_unfinished_work, unfinished_work_blocks_final, unfinished_work_hard_stop_message, unfinished_work_nudge_message, StreamRoundState, build_assistant_api_message, ensure_tool_call_pairings, filter_fresh_tool_calls, finalize_round_text, normalize_tool_calls, _current_abort_event, set_turn_force_tool_choice, set_turn_thinking_level, set_turn_tool_choice_required_blocked, turn_max_react_steps, turn_sleev_force_direct, turn_thinking_level, logger, json, time, asyncio, suppress) = bind_loop_tuple()  # noqa: N806
    from remedy.core.react_loop.loop_bindings import STATE_NAMES, unpack_state
    _st = unpack_state(s)
    (run_until_done, build_state, turn, plan_mode, attachments, session_id, message, runtime, prep, boot, messages, history, all_tools, tools, browse_pre_url, clear_goals_only, pure_action_kick, open_only_browse, page_interaction, seen_fps, result_cache, produced_user_text, pseudo_recovery_done, pseudo_nudge_count, false_progress_nudge_count, zero_tools_hard_block_count, mono_fp_last, mono_fp_hits, mono_explore_injected, scratchpad_nudge_count, tools_executed_this_turn, recovery_nudge_done, speed_batch_nudge_done, serial_explore_streak, _bind, _adapter, headers, endpoint, _sleev_route, _connect_s, timeout, max_length_continuations, _b0, length_continuations, reasoning_repair_done, tool_args_repair_done, tool_args_strip_done, api_soft_failures, max_api_soft_failures, force_answer_sticky, force_answer_api_fail_once, force_answer_nudge_done, empty_answer_retries, max_empty_answer_retries, _b1, agency_rearm_count, max_agency_rearms, zero_tool_drive_count, max_zero_tool_drives, open_work_continues, max_open_work_continues, open_work_last_score, open_work_last_batches, finish_everything_requested, _st_ow, step_wall_checkpointed, thinking_choice_repaired, green_gate_reopen_count, max_green_gate_reopens, open_drive_patience, last_progress_score, last_progress_step, open_drive_stalled_notified, auth_refresh_done, max_total, epoch_size, auto_continue, max_stale_epochs, epoch_index, productive_in_epoch, tool_batches_in_epoch, stale_epochs, tool_batches_this_turn, open_tasks_for_wall, user_wants_stop, assistant_text_acc, no_progress_steps, last_progress_fingerprint, max_no_progress_steps, stalled_finalize, all_error_batches, max_all_error_batches, failed_tools_this_turn, max_failed_tools, force_answer, is_final_step, step_tools, use_openai_sse, collected, round_state, content_parts, reasoning_parts, sid_mm, _verify_green, _keep_after_green, is_disconnect_error, mid_turn_fit_messages, synthesize_from_tools, TurnState, effective_stale_epochs, body, stream_live, text_out, tool_calls_list, reasoning_out, stutter_src) = tuple(_st[n] for n in STATE_NAMES)  # noqa: N806
    s.turn_complete = False
    try:
        # L0 system fast path: model/skills/whoami/version — no frontier tokens.
        # Early exit above usually handles this; keep as safety if tier was set
        # by metabolism without early-return (e.g. harness path edge).
        if (
            not plan_mode
            and not attachments
            and not browse_pre_url
            and not page_interaction
            and not clear_goals_only
            and int(_turn_tier_of(runtime)) == 0
        ):
            with suppress(Exception):
                from remedy.core.metabolism.l0 import try_l0_system_reply

                l0 = try_l0_system_reply(
                    runtime, message or "", preclassified=True
                )
                if l0:
                    yield l0
                    s.turn_complete = True
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
                        s.turn_complete = True
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
                            clear_body = str(tool_msg.get("content") or "").strip()
                            if clear_body:
                                clear_msg = clear_body
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
            s.turn_complete = True
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
                        nav_body = str(tool_msg.get("content") or "")
                        parsed_ok, parsed_fail = _browse_tool_ok(nav_body)
                        if parsed_ok:
                            browse_ok = True
                        elif parsed_fail:
                            browse_fail_snip = nav_body[:400]
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
                    s.turn_complete = True
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
    finally:
        from remedy.core.react_loop.loop_bindings import pack_state
        pack_state(s, locals())
