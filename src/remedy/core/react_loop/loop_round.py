"""ReAct post-consume round — tools, nudges, monologue breaker, finals.

Looks up patched names on ``remedy.core.react_loop.loop`` at call time.
Mutates bag ``s``. ``s.step_continue`` → next for-step; ``s.turn_complete`` → done.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any


async def run_react_round(
    s: Any,
    *,
    step: int,
    helpers: Any,
) -> AsyncIterator[str]:
    """Interpret round_state / tool calls for one step."""
    from remedy.core.react_loop.loop_bindings import bind_loop_tuple
    (aiohttp, LlmBinding, get_llm_binding, set_llm_binding, build_step_request_body, apply_build_engine_after_batch, _turn_tier_of, _resolve_and_apply_tools_fn, _wait_rmb_ready_abortable, consume_llm_http_response, sanitize_chat_body, repair_reasoning_content_in_messages, repair_tool_arguments_in_messages, strip_broken_tool_call_turns, _sleep_abortable, _http_session, _log_llm, _stopped_note, _take_nudges, _steer_message, _browse_tool_ok, execute_tool_calls, _provider_bits_fn, _rearm_agency_tools_fn, _RATE_LIMIT_MAX_RETRIES, _is_rate_limited, _pace_before_request, _rate_limit_wait, _await_or_abort, inject_phase_nudge, record_tool_batch_stats, _is_billing_llm_api_error, _is_fatal_llm_api_error, _is_thinking_tool_choice_error, fatal_billing_error_message, fatal_model_error_message, repeated_provider_error_message, _TOOL_RESULT_CHAR_CAP, _looks_like_pseudo_tools, _parse_pseudo_tool_calls, _tool_call_fingerprint, agency_rearm_nudge_message, agency_tool_promise_claim, batch_has_approval_required, batch_has_empty_or_spam_write, batch_has_empty_search, batch_has_tool_errors, clip_appended_source_dump, collapse_repeated_sentences, epoch_continue_message, is_serial_explore_batch, looks_like_false_progress, looks_like_leaked_scratchpad, looks_like_safety_refusal, message_asks_to_stop, mission_verify_gate_message, post_tools_user_summary_nudge, recovery_nudge_message, speed_batch_nudge_message, strip_stream_status_noise, strip_tool_markup, turn_has_unfinished_work, unfinished_work_blocks_final, unfinished_work_hard_stop_message, unfinished_work_nudge_message, StreamRoundState, build_assistant_api_message, ensure_tool_call_pairings, filter_fresh_tool_calls, finalize_round_text, normalize_tool_calls, _current_abort_event, set_turn_force_tool_choice, set_turn_thinking_level, set_turn_tool_choice_required_blocked, turn_max_react_steps, turn_sleev_force_direct, turn_thinking_level, logger, json, time, asyncio, suppress) = bind_loop_tuple()  # noqa: N806
    from remedy.core.react_loop.loop_bindings import STATE_NAMES, unpack_state
    _st = unpack_state(s)
    (run_until_done, build_state, turn, plan_mode, attachments, session_id, message, runtime, prep, boot, messages, history, all_tools, tools, browse_pre_url, clear_goals_only, pure_action_kick, open_only_browse, page_interaction, seen_fps, result_cache, produced_user_text, pseudo_recovery_done, pseudo_nudge_count, false_progress_nudge_count, zero_tools_hard_block_count, mono_fp_last, mono_fp_hits, mono_explore_injected, scratchpad_nudge_count, tools_executed_this_turn, recovery_nudge_done, speed_batch_nudge_done, serial_explore_streak, _bind, _adapter, headers, endpoint, _sleev_route, _connect_s, timeout, max_length_continuations, _b0, length_continuations, reasoning_repair_done, tool_args_repair_done, tool_args_strip_done, api_soft_failures, max_api_soft_failures, force_answer_sticky, force_answer_api_fail_once, force_answer_nudge_done, empty_answer_retries, max_empty_answer_retries, _b1, agency_rearm_count, max_agency_rearms, zero_tool_drive_count, max_zero_tool_drives, open_work_continues, max_open_work_continues, open_work_last_score, open_work_last_batches, finish_everything_requested, _st_ow, step_wall_checkpointed, thinking_choice_repaired, green_gate_reopen_count, max_green_gate_reopens, open_drive_patience, last_progress_score, last_progress_step, open_drive_stalled_notified, auth_refresh_done, max_total, epoch_size, auto_continue, max_stale_epochs, epoch_index, productive_in_epoch, tool_batches_in_epoch, stale_epochs, tool_batches_this_turn, open_tasks_for_wall, user_wants_stop, assistant_text_acc, no_progress_steps, last_progress_fingerprint, max_no_progress_steps, stalled_finalize, all_error_batches, max_all_error_batches, failed_tools_this_turn, max_failed_tools, force_answer, is_final_step, step_tools, use_openai_sse, collected, round_state, content_parts, reasoning_parts, sid_mm, _verify_green, _keep_after_green, is_disconnect_error, mid_turn_fit_messages, synthesize_from_tools, TurnState, effective_stale_epochs, body, stream_live, text_out, tool_calls_list, reasoning_out, stutter_src) = tuple(_st[n] for n in STATE_NAMES)  # noqa: N806
    s.turn_complete = False
    s.step_continue = False

    def _open_drive_keeps_going() -> bool:
        return bool(helpers.open_drive_keeps_going())

    def _build_active() -> bool:
        return bool(helpers.build_active())

    def _work_unfinished() -> bool:
        return bool(helpers.work_unfinished())

    def _drive_zero_tool_work() -> bool:
        # Helper mutates orchestrator locals via closure AND writes through to s
        # inside the orchestrator; refresh locals that the drive may change.
        ok = bool(helpers.drive_zero_tool_work())
        nonlocal zero_tool_drive_count, force_answer_sticky, tools, run_until_done, messages
        zero_tool_drive_count = getattr(s, "zero_tool_drive_count", zero_tool_drive_count)
        force_answer_sticky = getattr(s, "force_answer_sticky", force_answer_sticky)
        tools = getattr(s, "tools", tools)
        run_until_done = getattr(s, "run_until_done", run_until_done)
        messages = getattr(s, "messages", messages)
        return ok

    def _provider_bits() -> tuple[str, str, str]:
        return helpers.provider_bits()

    def _resolve_and_apply(*, step_index: int = 0) -> None:
        helpers.resolve_and_apply(step_index=step_index)
        nonlocal tools, run_until_done
        tools = getattr(s, "tools", tools)
        run_until_done = getattr(s, "run_until_done", run_until_done)

    def _armed_tool_names() -> set[str]:
        return helpers.armed_tool_names()

    def _rearm_agency_tools() -> None:
        helpers.rearm_agency_tools()
        nonlocal tools, run_until_done
        tools = getattr(s, "tools", tools)
        run_until_done = getattr(s, "run_until_done", run_until_done)

    try:
        tool_calls_list = round_state.tool_calls_list(collected)
        reasoning_out = round_state.reasoning_out

        # Finalize text. Live-stream already yielded tokens when tools off.
        text_out = finalize_round_text(round_state, tool_calls_list)
        # Stutter detectors must see the blob *before* collapse — otherwise
        # a looping mantra is polished into one sentence and never trips.
        stutter_src = str(text_out or "")
        # Drop auth/provider status lines if they leaked into model content
        if text_out:
            # Inline <think> belongs on the thinking channel, not in
            # the reply and not in the bin. Hosts started with
            # --reasoning-format deepseek already split it into
            # reasoning_content; this catches the ones that cannot.
            _thought = ""
            with suppress(Exception):
                from remedy.core.react_policy import (
                    split_reasoning_spans,
                )

                _body, _thought = split_reasoning_spans(str(text_out))
                if _thought:
                    text_out = _body
            if _thought:
                # This round already streamed reasoning_content as
                # @@thinking. Inline <think> is the same scratchpad
                # for hosts that cannot split it — don't append it
                # again onto the thinking panel.
                already = (reasoning_out or "").strip()
                extra = _thought.strip()
                if extra and extra not in already:
                    if already and extra.startswith(already):
                        tail = extra[len(already) :].lstrip()
                        if tail:
                            yield f"@@thinking:{tail}"
                    else:
                        yield f"@@thinking:{extra}"
            text_out = strip_stream_status_noise(str(text_out))
            stutter_src = str(text_out or "")
            with suppress(Exception):
                text_out = collapse_repeated_sentences(text_out)
                text_out = clip_appended_source_dump(text_out)
        if text_out and not tool_calls_list:
            with suppress(Exception):
                assistant_text_acc.append(str(text_out)[:8000])
        # Never treat DSML / text-tool dumps as user-visible answer text
        # *when tools are armed*. Trivia / disarmed turns keep the text
        # so "1 + 1" is not swallowed as fake tool markup.
        if text_out and _looks_like_pseudo_tools(text_out):
            recovered_preview = _parse_pseudo_tool_calls(text_out, _armed_tool_names())
            clean = strip_tool_markup(text_out)
            if tools:
                text_out = (
                    clean if clean and not _looks_like_pseudo_tools(clean) else ""
                )
            else:
                # Disarmed turns keep their text so "1 + 1" is not
                # swallowed as fake markup - but a *truncated*
                # fragment is never an answer. A local model replied
                # to "say READY" with a 12-character dangling json
                # fence, and it was shown to the user verbatim.
                from remedy.core.react_policy import (
                    is_pure_tool_call_blob as _blob,
                )
                from remedy.core.react_policy import (
                    looks_like_tool_markup_prefix as _trunc,
                )

                # Order matters: a *truncated* fence has no closing
                # ``` so strip_fenced_tool_calls cannot match it, and
                # `clean` comes back as the raw fragment. Checking
                # `clean` first would therefore hand that fragment
                # straight back as the answer.
                if _trunc(text_out) or _blob(text_out):
                    # The whole message was a tool call or a truncated
                    # fragment. There is no answer inside it, and
                    # showing the raw call to the owner is worse than
                    # showing nothing.
                    text_out = ""
                elif clean:
                    text_out = clean
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
        # Non-stream JSON + no tools: consume() did not live-yield.
        if (
            text_out
            and stream_live
            and not produced_user_text
            and not tool_calls_list
            and not tools
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
            and tools
            and turn.allow_pseudo_recovery()
        ):
            recovered = _parse_pseudo_tool_calls(raw_round, _armed_tool_names())
            if recovered:
                # Model already tried to use tools as text. Do not honor
                # force_answer / l1_pure_chat — that left "tool_c" in chat.
                force_answer = False
                force_answer_sticky = False
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
                # These tools really run, so they are real evidence.
                # Only the native batch below bumped this counter, so a
                # recovered call left _work_unfinished() reporting zero
                # tools: the zero-tool driver then spent its whole
                # budget of extra round-trips and the turn ended with
                # "no tools ran" about a tool that had just run.
                tools_executed_this_turn += len(recovered)
                batch_tool_msgs = []
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
                # Recovered calls are executed on this path too, so
                # their failures must count against the same ceiling.
                # Counting only the native batch let a recovery thrash
                # run to 262 failed calls untouched.
                failed_tools_this_turn += sum(
                    1
                    for _m in batch_tool_msgs
                    if "Error [" in str((_m or {}).get("content") or "")
                )
                if (
                    failed_tools_this_turn >= max_failed_tools
                    and not stalled_finalize
                ):
                    stalled_finalize = True
                    logger.warning(
                        "%d failed tool calls this turn (recovery path,"
                        " step %d) - finalizing instead of thrashing",
                        failed_tools_this_turn,
                        step + 1,
                    )
                # Same bookkeeping as the native batch below: the turn
                # state, the epoch's productivity and the batch count
                # all feed the stale-epoch / zero-tool drivers, which
                # otherwise still saw a turn with no tool batches.
                _rbdelta, _rpdelta = record_tool_batch_stats(
                    turn=turn,
                    fresh_calls=recovered,
                    batch_tool_msgs=batch_tool_msgs,
                    step=step,
                )
                tool_batches_this_turn += _rbdelta
                tool_batches_in_epoch += _rbdelta
                productive_in_epoch += _rpdelta
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
                        set_turn_force_tool_choice(True)
                        with suppress(Exception):
                            from remedy.core.turn_context import (
                                set_turn_write_budget,
                            )

                            set_turn_write_budget(8192, runtime)
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                filter_tools_write_first,
                            )

                            tools = (
                                filter_tools_write_first(
                                    all_tools,
                                    user_message=str(message or "build"),
                                    step_index=0,
                                    history=messages,
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
                s.step_continue = True
                return
            # DSML/text tools detected but incomplete (truncated stream) —
            # nudge for real function-calling instead of hanging on junk.
            # Trivia / disarmed turns: treat the text as the answer.
            if pseudo_nudge_count < 2 and tools:
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
                s.step_continue = True
                return

        if text_out and (not tool_calls_list or force_answer):
            # A nudge that arrived while this answer streamed must not
            # be lost: keep what she said, add the owner's words, and
            # let her continue instead of ending the turn.
            # (force_answer means "no more tools", not "ignore the owner".)
            _late = _take_nudges(session_id, runtime)
            if _late:
                # Buffered rounds (tools armed) have not shown this
                # text yet; live rounds already streamed it.
                if not stream_live and not _looks_like_pseudo_tools(text_out):
                    yield text_out
                    produced_user_text = True
                messages.append({"role": "assistant", "content": text_out})
                for _nudge in _late:
                    messages.append(_steer_message(_nudge))
                yield "\n\n"
                yield "@@steered\n"
                s.step_continue = True
                return
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
                                history=messages,
                            )
                            or all_tools
                        )
                        turn.tools = tools
                    set_turn_force_tool_choice(True)
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
                s.step_continue = True
                return
            # Don't ship faux tool syntax as the final answer.
            # Live 2026-08-13: L1 force_answer + DeepSeek <tool_invoke>
            # dump was skipped here, leaving the bubble as "tool_c".
            if (
                raw_round
                and _looks_like_pseudo_tools(raw_round)
                and tools
                and pseudo_nudge_count < 2
                and not pseudo_recovery_done
            ):
                pseudo_nudge_count += 1
                force_answer = False
                force_answer_sticky = False
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
                s.step_continue = True
                return
            # Agency fail-open (content path): unfinished work or a
            # tool-promise stub. Class rule — not a phrase/length gate.
            if (
                not tool_calls_list
                and all_tools
                and not force_answer_sticky
                and not is_final_step
            ):
                _refused = looks_like_safety_refusal(text_out) or (
                    looks_like_safety_refusal(reasoning_out)
                )
                if not _refused and _drive_zero_tool_work():
                    s.step_continue = True
                    return
                if _work_unfinished() and not _refused:
                    yield unfinished_work_hard_stop_message()
                    s.turn_complete = True
                    return
                if (
                    agency_tool_promise_claim(text_out, reasoning_out)
                    and (
                        agency_rearm_count < max_agency_rearms
                        or _open_drive_keeps_going()
                    )
                ):
                    agency_rearm_count += 1
                    _rearm_agency_tools()
                    force_answer_sticky = False
                    set_turn_force_tool_choice(True)
                    messages.append(agency_rearm_nudge_message())
                    logger.info(
                        "Agency re-arm after tool-promise prose (step %d, %d/%d)",
                        step + 1,
                        agency_rearm_count,
                        max_agency_rearms,
                    )
                    s.step_continue = True
                    return
            # Self-declared open work after real tool work: under a
            # finish-everything request OR an active build, "Still
            # open: X" is the next hop, not the end of the turn.
            # force_answer_sticky is ignored here — GREEN · stop
            # building used to set it and then accept the partial.
            if (
                not tool_calls_list
                and all_tools
                and text_out
                and not is_final_step
                and tools_executed_this_turn > 0
            ):
                _ow_items: list[str] = []
                with suppress(Exception):
                    from remedy.core.build_engine import (
                        build_progress_score,
                        get_build_state,
                    )
                    from remedy.core.react_open_work import (
                        final_declares_open_work,
                        open_work_continue_message,
                        seed_open_work_todos,
                    )

                    _bst_ow = get_build_state(runtime)
                    _read_only_review = bool(
                        getattr(_bst_ow, "read_only", False)
                        and int(getattr(_bst_ow, "write_steps", 0) or 0) == 0
                    )
                    if _read_only_review and text_out:
                        with suppress(Exception):
                            from remedy.core.build_todos import (
                                seed_review_finding_todos,
                                take_todos_event,
                            )
                            from remedy.core.react_open_work import (
                                extract_review_findings,
                            )

                            _rf = extract_review_findings(text_out)
                            if _rf:
                                seed_review_finding_todos(runtime, _rf)
                                _rf_td = take_todos_event(runtime)
                                if _rf_td:
                                    yield _rf_td
                    _ow_active = bool(
                        not _read_only_review
                        and (
                            finish_everything_requested
                            or _build_active()
                            or getattr(_bst_ow, "drive_to_done", False)
                        )
                    )
                    if (
                        _ow_active
                        and not message_asks_to_stop(message or "")
                        and not (
                            looks_like_safety_refusal(text_out)
                            or looks_like_safety_refusal(reasoning_out)
                        )
                    ):
                        _ow_items = final_declares_open_work(text_out)
                    if _ow_items:
                        _score_now = build_progress_score(_bst_ow)
                        # Progress since the last continuation: the build
                        # score climbed, or real tool batches ran. A model
                        # that just re-narrates the same list stops here.
                        _progressed = (
                            open_work_last_batches < 0
                            or _score_now > open_work_last_score
                            or tool_batches_this_turn > open_work_last_batches
                        )
                        if (
                            open_work_continues < max_open_work_continues
                            and _progressed
                        ):
                            open_work_continues += 1
                            open_work_last_score = _score_now
                            open_work_last_batches = tool_batches_this_turn
                            with suppress(Exception):
                                seed_open_work_todos(runtime, _ow_items)
                            _rearm_agency_tools()
                            force_answer_sticky = False
                            set_turn_force_tool_choice(True)
                            messages.append(
                                {"role": "assistant", "content": text_out}
                            )
                            messages.append(open_work_continue_message(_ow_items))
                            with suppress(Exception):
                                from remedy.core.build_todos import (
                                    take_todos_event,
                                )

                                _ow_td = take_todos_event(runtime)
                                if _ow_td:
                                    yield _ow_td
                            yield (
                                "@@status:Open work remains — continuing "
                                f"({open_work_continues}/{max_open_work_continues})…\n"
                            )
                            logger.info(
                                "Open-work continue %d/%d (step %d): %s",
                                open_work_continues,
                                max_open_work_continues,
                                step + 1,
                                "; ".join(_ow_items)[:200],
                            )
                            _ow_items = ["__continue__"]
                if _ow_items == ["__continue__"]:
                    s.step_continue = True
                    return
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
                        s.step_continue = True
                        return
            # Build engine GREEN/SHIP/TODO GATE: refuse final without
            # green (+ ship) or with an open Build list. Must also run
            # when tools are still armed (keep_agency after green) —
            # otherwise "**Done**" with pending todos is accepted.
            if (
                not tool_calls_list
                and text_out
                and not message_asks_to_stop(message or "")
                and (
                    force_answer
                    or force_answer_sticky
                    or is_final_step
                    or tools_executed_this_turn > 0
                )
            ):
                with suppress(Exception):
                    from remedy.core.build_engine import (
                        build_blocks_done_summary,
                        build_blocks_final_answer,
                        format_ship_report_line,
                        get_build_state,
                        green_gate_cap_allows_final,
                        unfinished_green_gate_message,
                    )

                    bst_g = get_build_state(runtime)
                    _force_path = bool(
                        force_answer or force_answer_sticky or is_final_step
                    )
                    _blocks = (
                        build_blocks_final_answer(bst_g)
                        if _force_path
                        else build_blocks_done_summary(bst_g)
                    )
                    if _blocks and bst_g is not None:
                        from remedy.core.build_engine import build_progress_score

                        _score_g = build_progress_score(bst_g)
                        _progressed_g = (
                            open_work_last_batches < 0
                            or _score_g > open_work_last_score
                            or tool_batches_this_turn > open_work_last_batches
                        )
                        if green_gate_cap_allows_final(
                            bst_g,
                            reopen_count=green_gate_reopen_count,
                            max_reopens=max_green_gate_reopens,
                        ):
                            logger.warning(
                                "Build green-gate cap (%d) — allowing final",
                                max_green_gate_reopens,
                            )
                            # Still emit ship report for observability
                            _sr = format_ship_report_line(bst_g)
                            if _sr:
                                yield _sr
                        elif _force_path or _progressed_g:
                            if _progressed_g:
                                open_work_last_score = _score_g
                                open_work_last_batches = tool_batches_this_turn
                            green_gate_reopen_count += 1
                            if "green_gate" not in bst_g.nudges_emitted:
                                bst_g.nudges_emitted.append("green_gate")
                            _rearm_agency_tools()
                            force_answer_sticky = False
                            messages.append(unfinished_green_gate_message(bst_g))
                            yield (
                                "@@status:Build list still open — keep going\n"
                            )
                            logger.info(
                                "Build green-gate blocked final "
                                "(step %d phase=%s %d/%d)",
                                step + 1,
                                bst_g.phase,
                                green_gate_reopen_count,
                                max_green_gate_reopens,
                            )
                            s.step_continue = True
                            return
                    elif bst_g is not None and bst_g.active:
                        # End-of-turn ship report when final is allowed
                        _sr = format_ship_report_line(bst_g)
                        if _sr:
                            yield _sr
            # Scout-only final on a real build: do not ask the owner
            # to say go. Force the first write once.
            if (
                not tool_calls_list
                and text_out
                and tools_executed_this_turn > 0
                and not is_final_step
                and not force_answer_sticky
                and not message_asks_to_stop(message or "")
            ):
                with suppress(Exception):
                    from remedy.core.build_engine import get_build_state as _gbs_sc

                    bst_sc = _gbs_sc(runtime)
                    if (
                        bst_sc is not None
                        and bst_sc.active
                        and not getattr(bst_sc, "read_only", False)
                        and int(bst_sc.write_steps or 0) == 0
                        and "force_implement" not in (bst_sc.nudges_emitted or [])
                    ):
                        bst_sc.nudges_emitted.append("force_implement")
                        bst_sc.phase = "implement"
                        _rearm_agency_tools()
                        force_answer_sticky = False
                        set_turn_force_tool_choice(True)
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[Build engine · FORCE IMPLEMENT] "
                                    "Scout is enough. Do not ask the owner "
                                    "to say go. NEXT step = file_write / "
                                    "file_edit that changes the tree. "
                                    "No plan monologue."
                                ),
                            }
                        )
                        yield "@@status:Build — implement now\n"
                        s.step_continue = True
                        return
            # --- Partner monologue loop breaker ---
            # ANY step with tools armed, zero tool_calls, and text/reasoning
            # is a monologue. Do not fall through to False-progress (that
            # path re-fed essays and then disconnected).
            _reasoning_only = (reasoning_out or "").strip()
            _mono_text = (
                (stutter_src or text_out or "").strip() or _reasoning_only[:500]
            )
            # Local harness only — frontier models self-steer; don't thrash them.
            # Use all_tools (agency this turn), not the possibly-disarmed
            # per-round `tools` list — last/final steps used to skip the
            # breaker after verify-green emptied the pack.
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
                            history=messages,
                        ) or all_tools
                        turn.tools = tools
                    set_turn_force_tool_choice(True)
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
                    if stutter_src and mono_fp_hits == 1:
                        with suppress(Exception):
                            from remedy.core.local_agent_optimize import (
                                text_has_internal_repetition,
                            )

                            if text_has_internal_repetition(stutter_src):
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
                    s.step_continue = True
                    return
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
                    set_turn_force_tool_choice(True)
                    with suppress(Exception):
                        from remedy.core.local_agent_optimize import (
                            filter_tools_write_first,
                        )

                        tools = (
                            filter_tools_write_first(
                                all_tools,
                                user_message=str(message or "build"),
                                step_index=0,
                                history=messages,
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
                s.step_continue = True
                return
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
                                history=messages,
                            )
                            or all_tools
                        )
                        turn.tools = tools
                        set_turn_force_tool_choice(True)
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
                        s.step_continue = True
                        return
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
                    s.step_continue = True
                    return
                s.turn_complete = True
                return
            if not stream_live:
                from remedy.core.turn_context import turn_build_verify_green as _tvg

                _verify_green = bool(_tvg(runtime))
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
                        s.step_continue = True
                        return
                    s.turn_complete = True
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
                        slice_ = (
                            slice_[:cut].rstrip()
                            if cut > 60
                            else slice_[:900].rstrip()
                        )
                    if slice_ and not _looks_like_pseudo_tools(slice_):
                        yield slice_
                        produced_user_text = True
                    logger.info(
                        "Green summary finalized (raw_len=%d out_len=%d)",
                        len(text_out or ""),
                        len(slice_),
                    )
                    s.turn_complete = True
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
                        set_turn_force_tool_choice(True)
                        tools = (
                            filter_tools_write_first(
                                all_tools,
                                user_message=str(message or "build"),
                                step_index=0,
                                history=messages,
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
                    s.step_continue = True
                    return
                # Final safety: never yield markup-only blobs *on work
                # turns*. Trivia / disarmed turns must still show the reply.
                if text_out and (
                    not _looks_like_pseudo_tools(text_out) or not tools
                ):
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
                            stutter_src or text_out or ""
                        )
                    if _skip_len:
                        logger.info(
                            "Skip length-continue — repetitive monologue"
                        )
                        # Session 765c 20:54: stutter then Stop. If the
                        # owner's job is still open, force tools — do
                        # not end the turn on a looped status line.
                        if _work_unfinished() and all_tools:
                            _rearm_agency_tools()
                            force_answer_sticky = False
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "You started repeating yourself. "
                                        "Stop restating status. Call tools "
                                        "and do the next open item."
                                    ),
                                }
                            )
                            s.step_continue = True
                            return
                        s.turn_complete = True
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
                    s.step_continue = True
                    return
            s.turn_complete = True
            return

        # Agency fail-open (reasoning / empty-content path): unfinished
        # work or a tool-promise stub — never end with zero evidence.
        if (
            not tool_calls_list
            and all_tools
            and not force_answer_sticky
            and not is_final_step
        ):
            _refused = looks_like_safety_refusal(text_out) or (
                looks_like_safety_refusal(reasoning_out)
            )
            if not _refused and _drive_zero_tool_work():
                s.step_continue = True
                return
            if _work_unfinished() and not _refused:
                yield unfinished_work_hard_stop_message()
                s.turn_complete = True
                return
            if (
                agency_tool_promise_claim(text_out, reasoning_out)
                and (
                    agency_rearm_count < max_agency_rearms
                    or _open_drive_keeps_going()
                )
            ):
                agency_rearm_count += 1
                _rearm_agency_tools()
                force_answer_sticky = False
                set_turn_force_tool_choice(True)
                messages.append(agency_rearm_nudge_message())
                logger.info(
                    "Agency re-arm after tool-promise prose (step %d, %d/%d)",
                    step + 1,
                    agency_rearm_count,
                    max_agency_rearms,
                )
                s.step_continue = True
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
                        s.step_continue = True
                        return
                    s.turn_complete = True
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
                    s.step_continue = True
                    return
                # 3) Last resort only after retries exhausted.
                yield (
                    "I gathered context but the model returned an empty "
                    "final message after several retries. Please resend "
                    "or ask me to continue from where I left off."
                )
            s.turn_complete = True
            return

        # Filter out exact repeats of prior tool calls this turn.
        fresh_calls = normalize_tool_calls(
            filter_fresh_tool_calls(tool_calls_list, seen_fps)
        )
        if fresh_calls:
            tools_executed_this_turn += len(fresh_calls)
            # Real tools landed — clear monologue force flag
            with suppress(Exception):
                set_turn_force_tool_choice(False)
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
                s.step_continue = True
                return
            # Jump toward final answer on next iteration.
            tools = []
            s.step_continue = True
            return

        messages.append(
            build_assistant_api_message(
                content=collected.get("content"),
                tool_calls=fresh_calls,
                # DeepSeek thinking mode: MUST pass reasoning back on tool turns.
                reasoning_content=reasoning_out or "",
            )
        )

        batch_tool_msgs = []
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

        # Native-vision models (Grok/Claude/GPT): attach the screenshot
        # so the next step can see pygame / custom-drawn pixels.
        with suppress(Exception):
            from remedy.core.computer.vision_observe import (
                flush_native_screenshots,
            )

            vis_msg = flush_native_screenshots(runtime)
            if vis_msg:
                messages.append(vis_msg)
                yield "@@status:Computer vision — screenshot attached\n"

        # Thrash brake: a batch where every tool errored is not
        # progress, but it *is* tool activity, so the no-progress
        # fingerprint above cannot see it. A model that varies its args
        # slightly each round also slips fingerprint dedup — observed as
        # 44 consecutive file_edit calls all failing "old_string and
        # new_string are identical", and 42 all failing
        # TOOL_ARGS_TRUNCATED. Nothing was changing; the turn was stuck.
        if batch_tool_msgs:
            _errs = sum(
                1
                for _m in batch_tool_msgs
                if "Error [" in str((_m or {}).get("content") or "")
            )
            failed_tools_this_turn += _errs
            # Absolute ceiling on wasted calls. Requiring a batch to be
            # *entirely* errors was too lenient: a turn ran 1205 tool
            # calls with 1062 failures (88%) because the occasional
            # success reset the consecutive counter every few rounds.
            if (
                failed_tools_this_turn >= max_failed_tools
                and not stalled_finalize
            ):
                stalled_finalize = True
                logger.warning(
                    "%d failed tool calls this turn (step %d) - "
                    "finalizing instead of thrashing",
                    failed_tools_this_turn,
                    step + 1,
                )
            if _errs and _errs == len(batch_tool_msgs):
                all_error_batches += 1
                if (
                    all_error_batches >= max_all_error_batches
                    and not stalled_finalize
                ):
                    stalled_finalize = True
                    logger.warning(
                        "%d consecutive all-error tool batches (step %d) - "
                        "finalizing instead of thrashing",
                        all_error_batches,
                        step + 1,
                    )
            else:
                all_error_batches = 0

        _bdelta, _pdelta = record_tool_batch_stats(
            turn=turn,
            fresh_calls=fresh_calls,
            batch_tool_msgs=batch_tool_msgs,
            step=step,
        )
        tool_batches_this_turn += _bdelta
        tool_batches_in_epoch += _bdelta
        productive_in_epoch += _pdelta
        inject_phase_nudge(turn, messages, runtime=runtime)
        with suppress(Exception):
            from remedy.core.hive.mother import inject_spawn_continue

            inject_spawn_continue(messages, fresh_calls)
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
                mutating = [
                    s
                    for s in steps_tc
                    if isinstance(s, dict)
                    and str(s.get("tool") or "").startswith("computer_")
                ]
                ok = bool(mutating) and all(
                    s.get("success")
                    and "APPROVAL_REQUIRED" not in str(s.get("result") or "")
                    and "UNVERIFIED" not in str(s.get("result") or "")
                    for s in mutating
                )
                get_cua_macros().observe_chain(
                    [
                        {
                            "tool": s.get("tool"),
                            "args": s.get("args") or {},
                        }
                        for s in mutating
                    ],
                    success=ok,
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
                set_turn_force_tool_choice(True)
                with suppress(Exception):
                    from remedy.core.turn_context import set_turn_write_budget

                    set_turn_write_budget(8192, runtime)
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
            # Missions past their retry limit auto-fail — no more
            # verify nudges or unfinished-work blocking.
            if (
                m is not None
                and m.status == "active"
                and int(m.retries or 0) >= int(m.max_retries or 5)
            ):
                m.status = "failed"
                MissionStore(home).save(m)
                logger.info(
                    "Mission %s auto-failed (retries %d/%d)",
                    m.id,
                    m.retries,
                    m.max_retries,
                )
            if (
                m is not None
                and m.status == "active"
                and not user_wants_stop
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
        # The step-wall checkpoint used to sit here. It could never run:
        # this section is only reached when force_answer is False, and
        # at the ceiling force_answer is always True — while the one
        # path that clears it clears is_final_step in the same
        # statement. It now fires where is_final_step settles, above.

    finally:
        from remedy.core.react_loop.loop_bindings import pack_state
        pack_state(s, locals())
