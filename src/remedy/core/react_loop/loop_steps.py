"""ReAct step loop orchestrator — prelude / HTTP / round delegates.

Looks up patched names on ``remedy.core.react_loop.loop`` at call time so
existing tests that patch that module keep working. Nested drive helpers
live here; ``loop_prelude`` / ``loop_http`` / ``loop_round`` / ``loop_finals``
own the rest.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from typing import Any

from remedy.core.react_loop.loop_http import run_react_http
from remedy.core.react_loop.loop_prelude import run_react_prelude
from remedy.core.react_loop.loop_round import run_react_round


async def run_react_steps(s: Any) -> AsyncIterator[str]:
    """Run nested helpers + the HTTP for-loop for one stream turn."""
    loop_mod = sys.modules['remedy.core.react_loop.loop']
    aiohttp = loop_mod.aiohttp
    LlmBinding = loop_mod.LlmBinding
    get_llm_binding = loop_mod.get_llm_binding
    set_llm_binding = loop_mod.set_llm_binding
    build_step_request_body = loop_mod.build_step_request_body
    apply_build_engine_after_batch = loop_mod.apply_build_engine_after_batch
    _turn_tier_of = loop_mod._turn_tier_of
    _resolve_and_apply_tools_fn = loop_mod._resolve_and_apply_tools_fn
    _wait_rmb_ready_abortable = loop_mod._wait_rmb_ready_abortable
    consume_llm_http_response = loop_mod.consume_llm_http_response
    sanitize_chat_body = loop_mod.sanitize_chat_body
    repair_reasoning_content_in_messages = loop_mod.repair_reasoning_content_in_messages
    repair_tool_arguments_in_messages = loop_mod.repair_tool_arguments_in_messages
    strip_broken_tool_call_turns = loop_mod.strip_broken_tool_call_turns
    _sleep_abortable = loop_mod._sleep_abortable
    _http_session = loop_mod._http_session
    _log_llm = loop_mod._log_llm
    _stopped_note = loop_mod._stopped_note
    _take_nudges = loop_mod._take_nudges
    _steer_message = loop_mod._steer_message
    _browse_tool_ok = loop_mod._browse_tool_ok
    execute_tool_calls = loop_mod.execute_tool_calls
    _provider_bits_fn = loop_mod._provider_bits_fn
    _rearm_agency_tools_fn = loop_mod._rearm_agency_tools_fn
    _RATE_LIMIT_MAX_RETRIES = loop_mod._RATE_LIMIT_MAX_RETRIES
    _is_rate_limited = loop_mod._is_rate_limited
    _pace_before_request = loop_mod._pace_before_request
    _rate_limit_wait = loop_mod._rate_limit_wait
    _await_or_abort = loop_mod._await_or_abort
    inject_phase_nudge = loop_mod.inject_phase_nudge
    record_tool_batch_stats = loop_mod.record_tool_batch_stats
    _is_billing_llm_api_error = loop_mod._is_billing_llm_api_error
    _is_fatal_llm_api_error = loop_mod._is_fatal_llm_api_error
    _is_thinking_tool_choice_error = loop_mod._is_thinking_tool_choice_error
    fatal_billing_error_message = loop_mod.fatal_billing_error_message
    fatal_model_error_message = loop_mod.fatal_model_error_message
    repeated_provider_error_message = loop_mod.repeated_provider_error_message
    _TOOL_RESULT_CHAR_CAP = loop_mod._TOOL_RESULT_CHAR_CAP
    _looks_like_pseudo_tools = loop_mod._looks_like_pseudo_tools
    _parse_pseudo_tool_calls = loop_mod._parse_pseudo_tool_calls
    _tool_call_fingerprint = loop_mod._tool_call_fingerprint
    agency_rearm_nudge_message = loop_mod.agency_rearm_nudge_message
    agency_tool_promise_claim = loop_mod.agency_tool_promise_claim
    batch_has_approval_required = loop_mod.batch_has_approval_required
    batch_has_empty_or_spam_write = loop_mod.batch_has_empty_or_spam_write
    batch_has_empty_search = loop_mod.batch_has_empty_search
    batch_has_tool_errors = loop_mod.batch_has_tool_errors
    clip_appended_source_dump = loop_mod.clip_appended_source_dump
    collapse_repeated_sentences = loop_mod.collapse_repeated_sentences
    epoch_continue_message = loop_mod.epoch_continue_message
    is_serial_explore_batch = loop_mod.is_serial_explore_batch
    looks_like_false_progress = loop_mod.looks_like_false_progress
    looks_like_leaked_scratchpad = loop_mod.looks_like_leaked_scratchpad
    looks_like_safety_refusal = loop_mod.looks_like_safety_refusal
    message_asks_to_stop = loop_mod.message_asks_to_stop
    mission_verify_gate_message = loop_mod.mission_verify_gate_message
    post_tools_user_summary_nudge = loop_mod.post_tools_user_summary_nudge
    recovery_nudge_message = loop_mod.recovery_nudge_message
    speed_batch_nudge_message = loop_mod.speed_batch_nudge_message
    strip_stream_status_noise = loop_mod.strip_stream_status_noise
    strip_tool_markup = loop_mod.strip_tool_markup
    turn_has_unfinished_work = loop_mod.turn_has_unfinished_work
    unfinished_work_blocks_final = loop_mod.unfinished_work_blocks_final
    unfinished_work_hard_stop_message = loop_mod.unfinished_work_hard_stop_message
    unfinished_work_nudge_message = loop_mod.unfinished_work_nudge_message
    StreamRoundState = loop_mod.StreamRoundState
    build_assistant_api_message = loop_mod.build_assistant_api_message
    ensure_tool_call_pairings = loop_mod.ensure_tool_call_pairings
    filter_fresh_tool_calls = loop_mod.filter_fresh_tool_calls
    finalize_round_text = loop_mod.finalize_round_text
    normalize_tool_calls = loop_mod.normalize_tool_calls
    _current_abort_event = loop_mod._current_abort_event
    set_turn_force_tool_choice = loop_mod.set_turn_force_tool_choice
    set_turn_thinking_level = loop_mod.set_turn_thinking_level
    set_turn_tool_choice_required_blocked = loop_mod.set_turn_tool_choice_required_blocked
    turn_max_react_steps = loop_mod.turn_max_react_steps
    turn_sleev_force_direct = loop_mod.turn_sleev_force_direct
    turn_thinking_level = loop_mod.turn_thinking_level
    logger = loop_mod.logger
    json = loop_mod.json
    time = loop_mod.time
    asyncio = loop_mod.asyncio
    suppress = loop_mod.suppress
    run_until_done: Any = getattr(s, 'run_until_done', None)
    build_state: Any = getattr(s, 'build_state', None)
    turn: Any = getattr(s, 'turn', None)
    plan_mode: Any = getattr(s, 'plan_mode', None)
    attachments: Any = getattr(s, 'attachments', None)
    session_id: Any = getattr(s, 'session_id', None)
    message: Any = getattr(s, 'message', None)
    runtime: Any = getattr(s, 'runtime', None)
    prep: Any = getattr(s, 'prep', None)
    boot: Any = getattr(s, 'boot', None)
    messages: Any = getattr(s, 'messages', None)
    history: Any = getattr(s, 'history', None)
    all_tools: Any = getattr(s, 'all_tools', None)
    tools: Any = getattr(s, 'tools', None)
    browse_pre_url: Any = getattr(s, 'browse_pre_url', None)
    clear_goals_only: Any = getattr(s, 'clear_goals_only', None)
    pure_action_kick: Any = getattr(s, 'pure_action_kick', None)
    open_only_browse: Any = getattr(s, 'open_only_browse', None)
    page_interaction: Any = getattr(s, 'page_interaction', None)
    seen_fps: Any = getattr(s, 'seen_fps', None)
    result_cache: Any = getattr(s, 'result_cache', None)
    produced_user_text: Any = getattr(s, 'produced_user_text', None)
    pseudo_recovery_done: Any = getattr(s, 'pseudo_recovery_done', None)
    pseudo_nudge_count: Any = getattr(s, 'pseudo_nudge_count', None)
    false_progress_nudge_count: Any = getattr(s, 'false_progress_nudge_count', None)
    zero_tools_hard_block_count: Any = getattr(s, 'zero_tools_hard_block_count', None)
    mono_fp_last: Any = getattr(s, 'mono_fp_last', None)
    mono_fp_hits: Any = getattr(s, 'mono_fp_hits', None)
    mono_explore_injected: Any = getattr(s, 'mono_explore_injected', None)
    scratchpad_nudge_count: Any = getattr(s, 'scratchpad_nudge_count', None)
    tools_executed_this_turn: Any = getattr(s, 'tools_executed_this_turn', None)
    recovery_nudge_done: Any = getattr(s, 'recovery_nudge_done', None)
    speed_batch_nudge_done: Any = getattr(s, 'speed_batch_nudge_done', None)
    serial_explore_streak: Any = getattr(s, 'serial_explore_streak', None)
    _bind: Any = getattr(s, '_bind', None)
    _adapter: Any = getattr(s, '_adapter', None)
    headers: Any = getattr(s, 'headers', None)
    endpoint: Any = getattr(s, 'endpoint', None)
    _sleev_route: Any = getattr(s, '_sleev_route', None)
    _connect_s: Any = getattr(s, '_connect_s', None)
    timeout: Any = getattr(s, 'timeout', None)
    max_length_continuations: Any = getattr(s, 'max_length_continuations', None)
    _b0: Any = getattr(s, '_b0', None)
    length_continuations: Any = getattr(s, 'length_continuations', None)
    reasoning_repair_done: Any = getattr(s, 'reasoning_repair_done', None)
    tool_args_repair_done: Any = getattr(s, 'tool_args_repair_done', None)
    tool_args_strip_done: Any = getattr(s, 'tool_args_strip_done', None)
    api_soft_failures: Any = getattr(s, 'api_soft_failures', None)
    max_api_soft_failures: Any = getattr(s, 'max_api_soft_failures', None)
    force_answer_sticky: Any = getattr(s, 'force_answer_sticky', None)
    force_answer_api_fail_once: Any = getattr(s, 'force_answer_api_fail_once', None)
    force_answer_nudge_done: Any = getattr(s, 'force_answer_nudge_done', None)
    empty_answer_retries: Any = getattr(s, 'empty_answer_retries', None)
    max_empty_answer_retries: Any = getattr(s, 'max_empty_answer_retries', None)
    _b1: Any = getattr(s, '_b1', None)
    agency_rearm_count: Any = getattr(s, 'agency_rearm_count', None)
    max_agency_rearms: Any = getattr(s, 'max_agency_rearms', None)
    zero_tool_drive_count: Any = getattr(s, 'zero_tool_drive_count', None)
    max_zero_tool_drives: Any = getattr(s, 'max_zero_tool_drives', None)
    open_work_continues: Any = getattr(s, 'open_work_continues', None)
    max_open_work_continues: Any = getattr(s, 'max_open_work_continues', None)
    open_work_last_score: Any = getattr(s, 'open_work_last_score', None)
    open_work_last_batches: Any = getattr(s, 'open_work_last_batches', None)
    finish_everything_requested: Any = getattr(s, 'finish_everything_requested', None)
    _st_ow: Any = getattr(s, '_st_ow', None)
    step_wall_checkpointed: Any = getattr(s, 'step_wall_checkpointed', None)
    thinking_choice_repaired: Any = getattr(s, 'thinking_choice_repaired', None)
    green_gate_reopen_count: Any = getattr(s, 'green_gate_reopen_count', None)
    max_green_gate_reopens: Any = getattr(s, 'max_green_gate_reopens', None)
    open_drive_patience: Any = getattr(s, 'open_drive_patience', None)
    last_progress_score: Any = getattr(s, 'last_progress_score', None)
    last_progress_step: Any = getattr(s, 'last_progress_step', None)
    open_drive_stalled_notified: Any = getattr(s, 'open_drive_stalled_notified', None)
    try:
        def _open_drive_keeps_going() -> bool:
            with suppress(Exception):
                from remedy.core.build_engine import (
                    get_build_state,
                    open_drive_should_continue,
                )

                return open_drive_should_continue(
                    get_build_state(runtime),
                    steps_since_progress=step - last_progress_step,
                    patience=open_drive_patience,
                )
            return False

        def _build_active() -> bool:
            """Active build *of this session* — never a sibling tab's."""
            with suppress(Exception):
                from remedy.core.build_engine import (
                    build_state_owned_by,
                    get_build_state,
                )

                bst = get_build_state(runtime)
                if bst is None or not getattr(bst, "active", False):
                    return False
                return build_state_owned_by(bst, str(session_id or ""))
            return False

        def _work_unfinished() -> bool:
            return unfinished_work_blocks_final(
                message or "",
                tools_executed=tools_executed_this_turn,
                user_stopped=message_asks_to_stop(message or ""),
                build_active=_build_active(),
            )

        def _drive_zero_tool_work() -> bool:
            """Re-arm + force a native tool call. True → caller must continue."""
            nonlocal zero_tool_drive_count, force_answer_sticky
            if not _work_unfinished():
                return False
            if (
                zero_tool_drive_count >= max_zero_tool_drives
                and not _open_drive_keeps_going()
            ):
                return False
            _rearm_agency_tools()
            # Re-arm declines on chat/trivia turns, and demanding a native call
            # with no schema armed is a guaranteed loop: the model cannot
            # comply, so the same nudge re-fires every step. Seen live as 148
            # identical "you made zero tool_calls" injects on a turn whose
            # request was "reply READY and do not use any tools" — the open
            # drive kept re-firing past max_zero_tool_drives.
            if not tools:
                logger.info(
                    "skip zero-tool drive — no tools armed for this turn (step %d)",
                    step + 1,
                )
                return False
            zero_tool_drive_count += 1
            force_answer_sticky = False
            set_turn_force_tool_choice(True)
            messages.append(unfinished_work_nudge_message())
            logger.info(
                "Unfinished work — zero-tool drive %d/%d (step %d)",
                zero_tool_drive_count,
                max_zero_tool_drives,
                step + 1,
            )
            s.zero_tool_drive_count = zero_tool_drive_count
            s.force_answer_sticky = force_answer_sticky
            s.tools = tools
            s.run_until_done = run_until_done
            s.messages = messages
            return True

        # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
        auth_refresh_done = False
        # Multi-epoch: soft walls compact; absolute total is safety only.
        # A per-turn ceiling (hive daughter) must not read from — or leak onto —
        # the runtime the mother is still using.
        max_total = turn_max_react_steps(runtime) or max(
            1, int(getattr(runtime, "_max_react_steps", 10_000) or 10_000)
        )
        epoch_size = max(
            16, int(getattr(runtime, "_epoch_react_steps", 256) or 256)
        )
        auto_continue = bool(getattr(runtime, "_react_auto_continue", True))
        # Single source of truth (REACT_MAX_STALE_EPOCHS default 8 — not 2)
        from remedy.core.react_turn import (
            TurnState,
            effective_stale_epochs,
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
        # Hard stop: the user's latest message asks to stop → mission
        # continuity cannot override it. Cancel the active mission and clear
        # open tasks so no epoch wall / re-arm keeps the loop alive.
        user_wants_stop = message_asks_to_stop(message or "")
        if user_wants_stop:
            open_tasks_for_wall = []
            with suppress(Exception):
                from remedy.core.mission import MissionStore

                _home_m = getattr(
                    getattr(runtime, "config", None), "home_dir", None
                )
                _mid_m = str(
                    session_id
                    or getattr(runtime, "_session_id", "")
                    or ""
                ) or None
                _ms = MissionStore(_home_m)
                _m = _ms.latest(_mid_m)
                if _m is not None and _m.status == "active":
                    _m.status = "cancelled"
                    _ms.save(_m)
                    logger.info(
                        "User asked to stop — mission %s cancelled", _m.id
                    )

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
        # Inject protocol into THIS turn's messages (never a process-global leftover).
        build_state = None
        with suppress(Exception):
            from remedy.core.build_engine import begin_build_turn, build_protocol_block

            build_state = (
                None
                if plan_mode
                else begin_build_turn(runtime, message or "")
            )
            if build_state is not None and build_state.active:
                if getattr(build_state, "drive_to_done", False):
                    finish_everything_requested = True
                proto = build_protocol_block(build_state)
                if proto:
                    messages.append({"role": "system", "content": str(proto)})
                # Body coordination: stamp the beacon's muscle label from THIS
                # turn's actual LLM binding (the one the HTTP calls use) — the
                # register() inside begin_build_turn may only see the runtime
                # default when the per-turn ContextVar isn't visible there.
                with suppress(Exception):
                    from remedy.core.coordination import heartbeat as _coord_hb

                    _muscle_lbl = "/".join(
                        x
                        for x in (
                            (_bind.provider or "").strip(),
                            (_bind.model or "").strip(),
                        )
                        if x
                    )
                    if _muscle_lbl and session_id:
                        _coord_hb(str(session_id), muscle=_muscle_lbl)
                with suppress(Exception):
                    from remedy.core.build_engine import enable_build_host_drive

                    enable_build_host_drive(runtime, build_state)
        with suppress(Exception):
            from remedy.core.build_engine import enable_work_host_drive

            enable_work_host_drive(
                runtime,
                message or "",
                plan_mode=bool(plan_mode),
                build_state=build_state,
            )
        # Frontier continue: brief + ledger inject (no local harness thrash)
        with suppress(Exception):
            from remedy.core.build_engine import frontier_continue_inject

            _fc = frontier_continue_inject(runtime, message or "")
            if _fc is not None:
                messages.append(_fc)

        run_until_done = bool(tools)

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
            s.tools = tools
            s.run_until_done = run_until_done

        _resolve_and_apply(step_index=0)
        with suppress(Exception):
            from remedy.core.build_todos import load_todos, todos_event_token

            _existing_todos = load_todos(runtime)
            if _existing_todos:
                yield todos_event_token(_existing_todos)

        def _armed_tool_names() -> set[str]:
            """Names the model may legitimately call this step."""
            names: set[str] = set()
            for src in (tools, all_tools):
                for _t in src or []:
                    if not isinstance(_t, dict):
                        continue
                    _fn = _t.get("function")
                    if isinstance(_fn, dict) and _fn.get("name"):
                        names.add(str(_fn["name"]).strip().lower())
            return names

        def _rearm_agency_tools() -> None:
            """Re-enable tool schemas *and* long-task epoch policy."""
            nonlocal tools, run_until_done
            if turn.plan_mode or plan_mode:
                logger.info("skip rearm — plan mode")
                return
            # Only greetings / verbal trivia stay tool-free. Knowledge
            # follow-ups re-arm — do not stall a capable model.
            with suppress(Exception):
                from remedy.core.react_policy import is_chat_only_message, is_pure_trivia_message

                if is_chat_only_message(message or "") or is_pure_trivia_message(
                    message or ""
                ):
                    logger.info("skip rearm — user message is chat/trivia")
                    return
            tools, run_until_done = _rearm_agency_tools_fn(turn)
            s.tools = tools
            s.run_until_done = run_until_done

        # Accumulated assistant text for critical verify at end
        assistant_text_acc: list[str] = []

        # Brake / thrash counters — initialized before _pull_bag so nonlocal binds.
        no_progress_steps = 0
        last_progress_fingerprint: tuple[int, int] | None = None
        max_no_progress_steps = 25
        stalled_finalize = False
        all_error_batches = 0
        max_all_error_batches = 8
        failed_tools_this_turn = 0
        max_failed_tools = 60

        def _pack_bag(_loc: dict[str, Any]) -> None:
            """Snapshot caller locals onto bag s (pass locals() at the call site)."""
            for _k, _v in list(_loc.items()):
                if _k in ("s", "http", "helpers", "_k", "_v", "_loc") or _k.startswith("__"):
                    continue
                if callable(_v) and not isinstance(_v, type) and _k.startswith("_"):
                    continue
                setattr(s, _k, _v)

        def _pull_bag() -> None:
            nonlocal run_until_done, build_state, turn, messages, history, all_tools, tools
            nonlocal produced_user_text, tools_executed_this_turn, tool_batches_this_turn
            nonlocal _bind, _adapter, headers, endpoint
            nonlocal length_continuations, reasoning_repair_done, tool_args_repair_done
            nonlocal tool_args_strip_done, api_soft_failures, force_answer_sticky
            nonlocal force_answer_api_fail_once, force_answer_nudge_done
            nonlocal empty_answer_retries, agency_rearm_count, zero_tool_drive_count
            nonlocal open_work_continues, open_work_last_score, open_work_last_batches
            nonlocal finish_everything_requested, step_wall_checkpointed
            nonlocal thinking_choice_repaired, green_gate_reopen_count
            nonlocal last_progress_score, last_progress_step, open_drive_stalled_notified
            nonlocal auth_refresh_done, epoch_index, productive_in_epoch
            nonlocal tool_batches_in_epoch, stale_epochs, assistant_text_acc
            nonlocal no_progress_steps, last_progress_fingerprint, stalled_finalize
            nonlocal all_error_batches, failed_tools_this_turn
            nonlocal false_progress_nudge_count, zero_tools_hard_block_count
            nonlocal mono_fp_last, mono_fp_hits, mono_explore_injected
            nonlocal scratchpad_nudge_count, recovery_nudge_done, speed_batch_nudge_done
            nonlocal serial_explore_streak, pseudo_recovery_done, pseudo_nudge_count
            nonlocal seen_fps, result_cache, open_tasks_for_wall, run_until_done
            run_until_done = getattr(s, "run_until_done", run_until_done)
            build_state = getattr(s, "build_state", build_state)
            turn = getattr(s, "turn", turn)
            messages = getattr(s, "messages", messages)
            history = getattr(s, "history", history)
            all_tools = getattr(s, "all_tools", all_tools)
            tools = getattr(s, "tools", tools)
            produced_user_text = getattr(s, "produced_user_text", produced_user_text)
            tools_executed_this_turn = getattr(s, "tools_executed_this_turn", tools_executed_this_turn)
            tool_batches_this_turn = getattr(s, "tool_batches_this_turn", tool_batches_this_turn)
            _bind = getattr(s, "_bind", _bind)
            _adapter = getattr(s, "_adapter", _adapter)
            headers = getattr(s, "headers", headers)
            endpoint = getattr(s, "endpoint", endpoint)
            length_continuations = getattr(s, "length_continuations", length_continuations)
            reasoning_repair_done = getattr(s, "reasoning_repair_done", reasoning_repair_done)
            tool_args_repair_done = getattr(s, "tool_args_repair_done", tool_args_repair_done)
            tool_args_strip_done = getattr(s, "tool_args_strip_done", tool_args_strip_done)
            api_soft_failures = getattr(s, "api_soft_failures", api_soft_failures)
            force_answer_sticky = getattr(s, "force_answer_sticky", force_answer_sticky)
            force_answer_api_fail_once = getattr(s, "force_answer_api_fail_once", force_answer_api_fail_once)
            force_answer_nudge_done = getattr(s, "force_answer_nudge_done", force_answer_nudge_done)
            empty_answer_retries = getattr(s, "empty_answer_retries", empty_answer_retries)
            agency_rearm_count = getattr(s, "agency_rearm_count", agency_rearm_count)
            zero_tool_drive_count = getattr(s, "zero_tool_drive_count", zero_tool_drive_count)
            open_work_continues = getattr(s, "open_work_continues", open_work_continues)
            open_work_last_score = getattr(s, "open_work_last_score", open_work_last_score)
            open_work_last_batches = getattr(s, "open_work_last_batches", open_work_last_batches)
            finish_everything_requested = getattr(s, "finish_everything_requested", finish_everything_requested)
            step_wall_checkpointed = getattr(s, "step_wall_checkpointed", step_wall_checkpointed)
            thinking_choice_repaired = getattr(s, "thinking_choice_repaired", thinking_choice_repaired)
            green_gate_reopen_count = getattr(s, "green_gate_reopen_count", green_gate_reopen_count)
            last_progress_score = getattr(s, "last_progress_score", last_progress_score)
            last_progress_step = getattr(s, "last_progress_step", last_progress_step)
            open_drive_stalled_notified = getattr(s, "open_drive_stalled_notified", open_drive_stalled_notified)
            auth_refresh_done = getattr(s, "auth_refresh_done", auth_refresh_done)
            epoch_index = getattr(s, "epoch_index", epoch_index)
            productive_in_epoch = getattr(s, "productive_in_epoch", productive_in_epoch)
            tool_batches_in_epoch = getattr(s, "tool_batches_in_epoch", tool_batches_in_epoch)
            stale_epochs = getattr(s, "stale_epochs", stale_epochs)
            assistant_text_acc = getattr(s, "assistant_text_acc", assistant_text_acc)
            no_progress_steps = getattr(s, "no_progress_steps", no_progress_steps)
            last_progress_fingerprint = getattr(s, "last_progress_fingerprint", last_progress_fingerprint)
            stalled_finalize = getattr(s, "stalled_finalize", stalled_finalize)
            all_error_batches = getattr(s, "all_error_batches", all_error_batches)
            failed_tools_this_turn = getattr(s, "failed_tools_this_turn", failed_tools_this_turn)
            false_progress_nudge_count = getattr(s, "false_progress_nudge_count", false_progress_nudge_count)
            zero_tools_hard_block_count = getattr(s, "zero_tools_hard_block_count", zero_tools_hard_block_count)
            mono_fp_last = getattr(s, "mono_fp_last", mono_fp_last)
            mono_fp_hits = getattr(s, "mono_fp_hits", mono_fp_hits)
            mono_explore_injected = getattr(s, "mono_explore_injected", mono_explore_injected)
            scratchpad_nudge_count = getattr(s, "scratchpad_nudge_count", scratchpad_nudge_count)
            recovery_nudge_done = getattr(s, "recovery_nudge_done", recovery_nudge_done)
            speed_batch_nudge_done = getattr(s, "speed_batch_nudge_done", speed_batch_nudge_done)
            serial_explore_streak = getattr(s, "serial_explore_streak", serial_explore_streak)
            pseudo_recovery_done = getattr(s, "pseudo_recovery_done", pseudo_recovery_done)
            pseudo_nudge_count = getattr(s, "pseudo_nudge_count", pseudo_nudge_count)
            seen_fps = getattr(s, "seen_fps", seen_fps)
            result_cache = getattr(s, "result_cache", result_cache)
            open_tasks_for_wall = getattr(s, "open_tasks_for_wall", open_tasks_for_wall)


        # One process-wide session (agent_llm owns its lifetime) — a fresh
        # connector per turn leaked "Unclosed client session" on abort/reset.
        # Dead-loop brake. ``max_total`` defaults to 10_000 steps, which is a
        # safety net for long *productive* builds, not for a stuck turn. A turn
        # that runs no tools and adds no messages is re-sending an identical
        # request: 226 consecutive rounds were observed live on a local host
        # (minutes of GPU, and on a cloud provider the same count of billed
        # calls). Nothing downstream can break that cycle, because nothing about
        # the request changes between rounds.
        # Dead-loop brake (values already seeded above for _pull_bag nonlocal).
        # Well above every legitimate no-tool sequence: the zero-tool drive
        # allows 8 attempts, stale epochs another 8, and dead-air re-arm sits
        # between them. This is a runaway backstop, not a policy knob - it must
        # never fire on a turn the loop is still steering on purpose.
        no_progress_steps = 0
        last_progress_fingerprint = None
        max_no_progress_steps = 25
        stalled_finalize = False
        # Consecutive batches in which *every* tool call errored.
        all_error_batches = 0
        max_all_error_batches = 8
        # Cumulative wasted calls. Generous enough that a long legitimate build
        # with occasional misses is never touched, low enough that a thrashing
        # turn cannot reach four figures.
        failed_tools_this_turn = 0
        max_failed_tools = 60


        # --- Prelude: L0 / fast-path / clear-goals / browse / pure-action kick ---
        _pack_bag(locals())
        async for _tok in run_react_prelude(s):
            yield _tok
        _pull_bag()
        if getattr(s, "turn_complete", False):
            return

        async with _http_session(timeout) as http:
            for step in range(max_total):
                # Identical request as last round? Then the turn is spinning.
                # Tool activity only. Message growth is NOT progress: the
                # loop answers a tool-less round by appending a nudge, so
                # counting messages let a nudge-loop masquerade as work and
                # spin for hundreds of rounds (535s observed on one turn).
                _fp = (
                    int(tools_executed_this_turn),
                    int(tool_batches_this_turn),
                )
                if _fp == last_progress_fingerprint:
                    no_progress_steps += 1
                    if (
                        no_progress_steps >= max_no_progress_steps
                        and not stalled_finalize
                    ):
                        stalled_finalize = True
                        logger.warning(
                            "ReAct made no progress for %d steps (step %d) - "
                            "finalizing instead of re-sending the same request",
                            no_progress_steps,
                            step + 1,
                        )
                else:
                    no_progress_steps = 0
                    last_progress_fingerprint = _fp

                # Mid-turn steering: anything the owner said while the last
                # step ran goes in now, before she plans the next one.
                with suppress(Exception):
                    for _nudge in _take_nudges(session_id, runtime):
                        messages.append(_steer_message(_nudge))
                        yield "@@steered\n"

                # Cooperative abort between ReAct steps (Stop generation).
                with suppress(Exception):
                    from remedy.core.turn_context import is_turn_aborted

                    if is_turn_aborted():
                        # Durable note — @@aborted alone never lands in the chat bubble.
                        yield _stopped_note(
                            tools_executed_this_turn > 0 or tool_batches_this_turn > 0
                        )
                        yield "@@aborted\n"
                        return

                # Open-drive stall guard: record real build progress; if an open
                # drive stalls (no write/verify/ship/green advance) past patience,
                # nudge once for an honest status instead of retrying to the wall.
                with suppress(Exception):
                    from remedy.core.build_engine import (
                        build_has_open_drive,
                        build_progress_score,
                        get_build_state,
                    )

                    _bst_p = get_build_state(runtime)
                    _score = build_progress_score(_bst_p)
                    if _score > last_progress_score:
                        last_progress_score = _score
                        last_progress_step = step
                    elif (
                        not open_drive_stalled_notified
                        and build_has_open_drive(_bst_p)
                        and (step - last_progress_step) > open_drive_patience
                    ):
                        open_drive_stalled_notified = True
                        logger.info(
                            "Open-drive stall: no progress in %d steps (step %d)",
                            step - last_progress_step,
                            step,
                        )
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "[Progress check] The remaining checklist or "
                                    "publish steps haven't advanced in a while. If "
                                    "you're blocked, stop and tell me plainly: what "
                                    "you finished, what's left, and exactly what you "
                                    "need from me (a key, an install, a decision). "
                                    "Don't keep retrying the same thing silently."
                                ),
                            }
                        )

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

                # A stalled turn takes the normal last-step path so it
                # still produces a real answer instead of ending silently.
                is_final_step = stalled_finalize or step >= max_total - 1
                # Checkpoint on arrival at the absolute ceiling, once per turn.
                # This used to sit at the tail of the tool-batch section, which
                # is only reached when force_answer is False — and at the
                # ceiling force_answer is always True, while the one path that
                # clears it clears is_final_step in the same statement. So it
                # never ran. Firing here also covers the re-arm below, which
                # pushes the turn *past* the wall: that is more risk, not less.
                if is_final_step and not step_wall_checkpointed:
                    step_wall_checkpointed = True
                    with suppress(Exception):
                        md = runtime._maybe_auto_checkpoint(
                            reason="step_wall",
                            title="Absolute safety step ceiling",
                            force=True,
                        )
                        if md:
                            yield "@@checkpoint"
                # Machine green verify → summary-only unless ship/play still needs tools
                from remedy.core.turn_context import (
                    set_turn_build_verify_green,
                    turn_build_verify_green,
                )

                _verify_green = bool(turn_build_verify_green(runtime))
                _keep_after_green = False
                if _verify_green:
                    with suppress(Exception):
                        from remedy.core.build_engine import (
                            get_build_state,
                            keep_agency_after_green,
                        )
                        from remedy.core.local_agent_optimize import (
                            execution_already_ran,
                        )

                        _keep_after_green = bool(
                            keep_agency_after_green(
                                get_build_state(runtime),
                                message or "",
                                run_already_done=execution_already_ran(messages),
                            )
                        )
                    if _keep_after_green:
                        force_answer_sticky = False
                        set_turn_build_verify_green(False, runtime)
                        set_turn_force_tool_choice(False)
                        if all_tools:
                            tools = all_tools
                            turn.tools = all_tools
                    else:
                        force_answer_sticky = True
                        tools = []
                        turn.tools = []
                        set_turn_force_tool_choice(False)
                # Absolute safety wall only — soft epochs never force-answer alone.
                force_answer = (
                    is_final_step or not tools or force_answer_sticky
                )
                # Work request + zero tool evidence is never a successful final.
                # Keep tools on and require a native call instead of summarizing.
                if (
                    force_answer
                    and not stalled_finalize
                    and _work_unfinished()
                    and all_tools
                    and not message_asks_to_stop(message or "")
                    and (
                        zero_tool_drive_count < max_zero_tool_drives
                        or _open_drive_keeps_going()
                    )
                ):
                    force_answer = False
                    force_answer_sticky = False
                    is_final_step = False
                    _rearm_agency_tools()
                    if tools:
                        set_turn_force_tool_choice(True)
                    else:
                        # Re-arm declined (chat/trivia turn). Keeping tools off
                        # while refusing to finalize spins the step wall for
                        # nothing — let the turn answer in words instead.
                        force_answer = True
                        is_final_step = True
                # Re-resolve pack each step (write-first → full after writes)
                if not force_answer and turn.all_tools and not _verify_green:
                    _resolve_and_apply(step_index=int(step))
                step_tools = None if force_answer else tools
                # Why a step went out tool-less is the single hardest thing to
                # reconstruct from a trace: the request just shows n_tools=0
                # with no hint which of a dozen branches emptied it.
                if all_tools and not step_tools:
                    logger.info(
                        "step %d sent with no tools: force_answer=%s sticky=%s "
                        "final=%s verify_green=%s stalled=%s armed=%d "
                        "arm_reason=%s pack=%s run_until_done=%s",
                        step + 1,
                        force_answer,
                        force_answer_sticky,
                        is_final_step,
                        _verify_green,
                        stalled_finalize,
                        len(tools or []),
                        getattr(turn, "arm_reason", "?"),
                        getattr(turn, "pack", "?"),
                        run_until_done,
                    )
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
                        int(_turn_tier_of(runtime)) >= 2
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


                # --- HTTP POST + consume ---
                from types import SimpleNamespace as _SNS

                helpers = _SNS(
                    open_drive_keeps_going=_open_drive_keeps_going,
                    build_active=_build_active,
                    work_unfinished=_work_unfinished,
                    drive_zero_tool_work=_drive_zero_tool_work,
                    provider_bits=_provider_bits,
                    resolve_and_apply=_resolve_and_apply,
                    armed_tool_names=_armed_tool_names,
                    rearm_agency_tools=_rearm_agency_tools,
                )
                s.force_answer = force_answer
                s.is_final_step = is_final_step
                s.step_tools = step_tools
                s.sid_mm = sid_mm
                s._verify_green = _verify_green
                s._keep_after_green = _keep_after_green
                _pack_bag(locals())
                async for _tok in run_react_http(
                    s, http, step=step, helpers=helpers
                ):
                    yield _tok
                _pull_bag()
                if getattr(s, "turn_complete", False):
                    return
                force_answer = bool(getattr(s, "force_answer", force_answer))
                step_tools = getattr(s, "step_tools", step_tools)
                collected = getattr(s, "collected", None)
                round_state = getattr(s, "round_state", None)

                # --- Post-consume: tools / nudges / finals ---
                s.force_answer = force_answer
                s.is_final_step = is_final_step
                s.step_tools = step_tools
                s.collected = collected
                s.round_state = round_state
                _pack_bag(locals())
                async for _tok in run_react_round(s, step=step, helpers=helpers):
                    yield _tok
                _pull_bag()
                if getattr(s, "turn_complete", False):
                    return
                if getattr(s, "step_continue", False):
                    s.step_continue = False
                    continue

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
            with suppress(Exception):
                from remedy.core.sleev import prepare_llm_http

                endpoint, headers = prepare_llm_http(
                    provider=_bind.provider,
                    base_url=_bind.base_url,
                    api_key=_bind.api_key,
                    adapter=_adapter,
                    runtime=runtime,
                )
            use_openai_sse = bool(
                getattr(_adapter, "uses_openai_sse", True)
            )
            body = _adapter.build_body(
                model=_bind.model,
                messages=messages,
                tools=None,
                stream=use_openai_sse,
                thinking_level=turn_thinking_level(runtime),
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
                await _pace_before_request(
                    str(getattr(_bind, "provider", "") or ""),
                    _current_abort_event(),
                )
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=900, sock_read=900)
                ) as http2, http2.post(
                    endpoint, headers=headers, json=body
                ) as resp:
                    if resp.status == 200:
                        rs = StreamRoundState()
                        collected = {}
                        try:
                            async for _tok, _user_flag in consume_llm_http_response(
                                resp,
                                round_state=rs,
                                collected=collected,
                                adapter=_adapter,
                                bind=_bind,
                                body=body if isinstance(body, dict) else None,
                                use_openai_sse=use_openai_sse,
                                stream_live=True,
                            ):
                                if _user_flag:
                                    produced_user_text = True
                                yield _tok
                        except asyncio.CancelledError:
                            yield _stopped_note(
                                tools_executed_this_turn > 0
                                or tool_batches_this_turn > 0
                            )
                            yield "@@aborted\n"
                            return
                        if not produced_user_text:
                            leftover = rs.text_out or rs.reasoning_out
                            if leftover:
                                produced_user_text = True
                                yield leftover
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
    finally:
        for _k, _v in list(locals().items()):
            if _k in ('s', 'L', '_k', '_v') or _k.startswith('__'):
                continue
            setattr(s, _k, _v)
