"""ReAct step loop orchestrator — prelude / HTTP / round delegates.

Looks up patched names on ``remedy.core.react_loop.loop`` at call time so
existing tests that patch that module keep working. Nested drive helpers
live here; ``loop_prelude`` / ``loop_http`` / ``loop_round`` / ``loop_finals``
own the rest.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from remedy.core.react_loop.loop_http import run_react_http
from remedy.core.react_loop.loop_prelude import run_react_prelude
from remedy.core.react_loop.loop_round import run_react_round


async def run_react_steps(s: Any) -> AsyncIterator[str]:
    """Run nested helpers + the HTTP for-loop for one stream turn."""
    from remedy.core.react_loop.loop_bindings import bind_loop_module
    _lb = bind_loop_module()
    aiohttp = _lb["aiohttp"]
    LlmBinding = _lb["LlmBinding"]  # noqa: N806
    get_llm_binding = _lb["get_llm_binding"]
    set_llm_binding = _lb["set_llm_binding"]
    build_step_request_body = _lb["build_step_request_body"]
    apply_build_engine_after_batch = _lb["apply_build_engine_after_batch"]
    _turn_tier_of = _lb["_turn_tier_of"]
    _resolve_and_apply_tools_fn = _lb["_resolve_and_apply_tools_fn"]
    _wait_rmb_ready_abortable = _lb["_wait_rmb_ready_abortable"]
    consume_llm_http_response = _lb["consume_llm_http_response"]
    sanitize_chat_body = _lb["sanitize_chat_body"]
    repair_reasoning_content_in_messages = _lb["repair_reasoning_content_in_messages"]
    repair_tool_arguments_in_messages = _lb["repair_tool_arguments_in_messages"]
    strip_broken_tool_call_turns = _lb["strip_broken_tool_call_turns"]
    _sleep_abortable = _lb["_sleep_abortable"]
    _http_session = _lb["_http_session"]
    _log_llm = _lb["_log_llm"]
    _stopped_note = _lb["_stopped_note"]
    _take_nudges = _lb["_take_nudges"]
    _steer_message = _lb["_steer_message"]
    _browse_tool_ok = _lb["_browse_tool_ok"]
    execute_tool_calls = _lb["execute_tool_calls"]
    _provider_bits_fn = _lb["_provider_bits_fn"]
    _rearm_agency_tools_fn = _lb["_rearm_agency_tools_fn"]
    _RATE_LIMIT_MAX_RETRIES = _lb["_RATE_LIMIT_MAX_RETRIES"]  # noqa: N806
    _is_rate_limited = _lb["_is_rate_limited"]
    _pace_before_request = _lb["_pace_before_request"]
    _rate_limit_wait = _lb["_rate_limit_wait"]
    _await_or_abort = _lb["_await_or_abort"]
    inject_phase_nudge = _lb["inject_phase_nudge"]
    record_tool_batch_stats = _lb["record_tool_batch_stats"]
    _is_billing_llm_api_error = _lb["_is_billing_llm_api_error"]
    _is_fatal_llm_api_error = _lb["_is_fatal_llm_api_error"]
    _is_thinking_tool_choice_error = _lb["_is_thinking_tool_choice_error"]
    fatal_billing_error_message = _lb["fatal_billing_error_message"]
    fatal_model_error_message = _lb["fatal_model_error_message"]
    repeated_provider_error_message = _lb["repeated_provider_error_message"]
    _TOOL_RESULT_CHAR_CAP = _lb["_TOOL_RESULT_CHAR_CAP"]  # noqa: N806
    _looks_like_pseudo_tools = _lb["_looks_like_pseudo_tools"]
    _parse_pseudo_tool_calls = _lb["_parse_pseudo_tool_calls"]
    _tool_call_fingerprint = _lb["_tool_call_fingerprint"]
    agency_rearm_nudge_message = _lb["agency_rearm_nudge_message"]
    agency_tool_promise_claim = _lb["agency_tool_promise_claim"]
    batch_has_approval_required = _lb["batch_has_approval_required"]
    batch_has_empty_or_spam_write = _lb["batch_has_empty_or_spam_write"]
    batch_has_empty_search = _lb["batch_has_empty_search"]
    batch_has_tool_errors = _lb["batch_has_tool_errors"]
    clip_appended_source_dump = _lb["clip_appended_source_dump"]
    collapse_repeated_sentences = _lb["collapse_repeated_sentences"]
    epoch_continue_message = _lb["epoch_continue_message"]
    is_serial_explore_batch = _lb["is_serial_explore_batch"]
    looks_like_false_progress = _lb["looks_like_false_progress"]
    looks_like_leaked_scratchpad = _lb["looks_like_leaked_scratchpad"]
    looks_like_safety_refusal = _lb["looks_like_safety_refusal"]
    message_asks_to_stop = _lb["message_asks_to_stop"]
    mission_verify_gate_message = _lb["mission_verify_gate_message"]
    post_tools_user_summary_nudge = _lb["post_tools_user_summary_nudge"]
    recovery_nudge_message = _lb["recovery_nudge_message"]
    speed_batch_nudge_message = _lb["speed_batch_nudge_message"]
    strip_stream_status_noise = _lb["strip_stream_status_noise"]
    strip_tool_markup = _lb["strip_tool_markup"]
    turn_has_unfinished_work = _lb["turn_has_unfinished_work"]
    unfinished_work_blocks_final = _lb["unfinished_work_blocks_final"]
    unfinished_work_hard_stop_message = _lb["unfinished_work_hard_stop_message"]
    unfinished_work_nudge_message = _lb["unfinished_work_nudge_message"]
    StreamRoundState = _lb["StreamRoundState"]  # noqa: N806
    build_assistant_api_message = _lb["build_assistant_api_message"]
    ensure_tool_call_pairings = _lb["ensure_tool_call_pairings"]
    filter_fresh_tool_calls = _lb["filter_fresh_tool_calls"]
    finalize_round_text = _lb["finalize_round_text"]
    normalize_tool_calls = _lb["normalize_tool_calls"]
    _current_abort_event = _lb["_current_abort_event"]
    set_turn_force_tool_choice = _lb["set_turn_force_tool_choice"]
    set_turn_thinking_level = _lb["set_turn_thinking_level"]
    set_turn_tool_choice_required_blocked = _lb["set_turn_tool_choice_required_blocked"]
    turn_max_react_steps = _lb["turn_max_react_steps"]
    turn_sleev_force_direct = _lb["turn_sleev_force_direct"]
    turn_thinking_level = _lb["turn_thinking_level"]
    logger = _lb["logger"]
    json = _lb["json"]
    time = _lb["time"]
    asyncio = _lb["asyncio"]
    suppress = _lb["suppress"]
    from remedy.core.react_loop.loop_bindings import unpack_state
    _st = unpack_state(s)
    run_until_done = _st["run_until_done"]
    build_state = _st["build_state"]
    turn = _st["turn"]
    plan_mode = _st["plan_mode"]
    attachments = _st["attachments"]
    session_id = _st["session_id"]
    message = _st["message"]
    runtime = _st["runtime"]
    prep = _st["prep"]
    boot = _st["boot"]
    messages = _st["messages"]
    history = _st["history"]
    all_tools = _st["all_tools"]
    tools = _st["tools"]
    browse_pre_url = _st["browse_pre_url"]
    clear_goals_only = _st["clear_goals_only"]
    pure_action_kick = _st["pure_action_kick"]
    open_only_browse = _st["open_only_browse"]
    page_interaction = _st["page_interaction"]
    seen_fps = _st["seen_fps"]
    result_cache = _st["result_cache"]
    produced_user_text = _st["produced_user_text"]
    pseudo_recovery_done = _st["pseudo_recovery_done"]
    pseudo_nudge_count = _st["pseudo_nudge_count"]
    false_progress_nudge_count = _st["false_progress_nudge_count"]
    zero_tools_hard_block_count = _st["zero_tools_hard_block_count"]
    mono_fp_last = _st["mono_fp_last"]
    mono_fp_hits = _st["mono_fp_hits"]
    mono_explore_injected = _st["mono_explore_injected"]
    scratchpad_nudge_count = _st["scratchpad_nudge_count"]
    tools_executed_this_turn = _st["tools_executed_this_turn"]
    recovery_nudge_done = _st["recovery_nudge_done"]
    speed_batch_nudge_done = _st["speed_batch_nudge_done"]
    serial_explore_streak = _st["serial_explore_streak"]
    _bind = _st["_bind"]
    _adapter = _st["_adapter"]
    headers = _st["headers"]
    endpoint = _st["endpoint"]
    _sleev_route = _st["_sleev_route"]
    _connect_s = _st["_connect_s"]
    timeout = _st["timeout"]
    max_length_continuations = _st["max_length_continuations"]
    _b0 = _st["_b0"]
    length_continuations = _st["length_continuations"]
    reasoning_repair_done = _st["reasoning_repair_done"]
    tool_args_repair_done = _st["tool_args_repair_done"]
    tool_args_strip_done = _st["tool_args_strip_done"]
    api_soft_failures = _st["api_soft_failures"]
    max_api_soft_failures = _st["max_api_soft_failures"]
    force_answer_sticky = _st["force_answer_sticky"]
    force_answer_api_fail_once = _st["force_answer_api_fail_once"]
    force_answer_nudge_done = _st["force_answer_nudge_done"]
    empty_answer_retries = _st["empty_answer_retries"]
    max_empty_answer_retries = _st["max_empty_answer_retries"]
    _b1 = _st["_b1"]
    agency_rearm_count = _st["agency_rearm_count"]
    max_agency_rearms = _st["max_agency_rearms"]
    zero_tool_drive_count = _st["zero_tool_drive_count"]
    max_zero_tool_drives = _st["max_zero_tool_drives"]
    open_work_continues = _st["open_work_continues"]
    max_open_work_continues = _st["max_open_work_continues"]
    open_work_last_score = _st["open_work_last_score"]
    open_work_last_batches = _st["open_work_last_batches"]
    finish_everything_requested = _st["finish_everything_requested"]
    _st_ow = _st["_st_ow"]
    step_wall_checkpointed = _st["step_wall_checkpointed"]
    thinking_choice_repaired = _st["thinking_choice_repaired"]
    green_gate_reopen_count = _st["green_gate_reopen_count"]
    max_green_gate_reopens = _st["max_green_gate_reopens"]
    open_drive_patience = _st["open_drive_patience"]
    last_progress_score = _st["last_progress_score"]
    last_progress_step = _st["last_progress_step"]
    open_drive_stalled_notified = _st["open_drive_stalled_notified"]
    auth_refresh_done = _st["auth_refresh_done"]
    max_total = _st["max_total"]
    epoch_size = _st["epoch_size"]
    auto_continue = _st["auto_continue"]
    max_stale_epochs = _st["max_stale_epochs"]
    epoch_index = _st["epoch_index"]
    productive_in_epoch = _st["productive_in_epoch"]
    tool_batches_in_epoch = _st["tool_batches_in_epoch"]
    stale_epochs = _st["stale_epochs"]
    tool_batches_this_turn = _st["tool_batches_this_turn"]
    open_tasks_for_wall = _st["open_tasks_for_wall"]
    user_wants_stop = _st["user_wants_stop"]
    assistant_text_acc = _st["assistant_text_acc"]
    no_progress_steps = _st["no_progress_steps"]
    last_progress_fingerprint = _st["last_progress_fingerprint"]
    max_no_progress_steps = _st["max_no_progress_steps"]
    stalled_finalize = _st["stalled_finalize"]
    all_error_batches = _st["all_error_batches"]
    max_all_error_batches = _st["max_all_error_batches"]
    failed_tools_this_turn = _st["failed_tools_this_turn"]
    max_failed_tools = _st["max_failed_tools"]
    force_answer = _st["force_answer"]
    is_final_step = _st["is_final_step"]
    step_tools = _st["step_tools"]
    use_openai_sse = _st["use_openai_sse"]
    collected = _st["collected"]
    round_state = _st["round_state"]
    content_parts = _st["content_parts"]
    reasoning_parts = _st["reasoning_parts"]
    sid_mm = _st["sid_mm"]
    _verify_green = _st["_verify_green"]
    _keep_after_green = _st["_keep_after_green"]
    is_disconnect_error = _st["is_disconnect_error"]
    mid_turn_fit_messages = _st["mid_turn_fit_messages"]
    synthesize_from_tools = _st["synthesize_from_tools"]
    TurnState = _st["TurnState"]  # noqa: N806
    effective_stale_epochs = _st["effective_stale_epochs"]
    body = _st["body"]
    stream_live = _st["stream_live"]
    text_out = _st["text_out"]
    tool_calls_list = _st["tool_calls_list"]
    reasoning_out = _st["reasoning_out"]
    stutter_src = _st["stutter_src"]
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
        open_tasks_for_wall = []
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
        if str(getattr(turn, "arm_reason", "") or "") == "ask_first":
            with suppress(Exception):
                from remedy.core.react_policy import ask_first_nudge_message

                messages.append(ask_first_nudge_message())
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
            with suppress(Exception):
                from remedy.core.turn_context import current_chat_mode, turn_has_attachments

                if current_chat_mode() and not turn_has_attachments():
                    logger.info("skip rearm — chat pin")
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
            if str(getattr(turn, "arm_reason", "") or "") in (
                "ask_first",
                "chat_mode",
                "no_work_request",
                "non_work",
                "l1_pure_chat",
            ):
                logger.info("skip rearm — %s", turn.arm_reason)
                return
            tools, run_until_done = _rearm_agency_tools_fn(turn)
            s.tools = tools
            s.run_until_done = run_until_done

        # Accumulated assistant text for critical verify at end
        assistant_text_acc = []

        # Brake / thrash counters — initialized before _pull_bag so nonlocal binds.
        no_progress_steps = 0
        last_progress_fingerprint = None
        max_no_progress_steps = 25
        stalled_finalize = False
        all_error_batches = 0
        max_all_error_batches = 8
        failed_tools_this_turn = 0
        max_failed_tools = 60

        def _pack_bag(_loc: dict[str, Any]) -> None:
            """Snapshot caller locals onto bag s (pass locals() at the call site)."""
            for _k, _v in list(_loc.items()):
                if _k in ("s", "_lb", "http", "helpers", "_k", "_v", "_loc") or _k.startswith("__"):
                    continue
                if callable(_v) and not isinstance(_v, type) and _k.startswith("_"):
                    continue
                setattr(s, _k, _v)

        def _pull_bag() -> None:
            nonlocal run_until_done, build_state, turn, plan_mode, attachments, session_id, message, runtime, prep, boot, messages, history, all_tools, tools, browse_pre_url, clear_goals_only, pure_action_kick, open_only_browse, page_interaction, seen_fps, result_cache, produced_user_text, pseudo_recovery_done, pseudo_nudge_count, false_progress_nudge_count, zero_tools_hard_block_count, mono_fp_last, mono_fp_hits, mono_explore_injected, scratchpad_nudge_count, tools_executed_this_turn, recovery_nudge_done, speed_batch_nudge_done, serial_explore_streak, _bind, _adapter, headers, endpoint, _sleev_route, _connect_s, timeout, max_length_continuations, _b0, length_continuations, reasoning_repair_done, tool_args_repair_done, tool_args_strip_done, api_soft_failures, max_api_soft_failures, force_answer_sticky, force_answer_api_fail_once, force_answer_nudge_done, empty_answer_retries, max_empty_answer_retries, _b1, agency_rearm_count, max_agency_rearms, zero_tool_drive_count, max_zero_tool_drives, open_work_continues, max_open_work_continues, open_work_last_score, open_work_last_batches, finish_everything_requested, _st_ow, step_wall_checkpointed, thinking_choice_repaired, green_gate_reopen_count, max_green_gate_reopens, open_drive_patience, last_progress_score, last_progress_step, open_drive_stalled_notified, auth_refresh_done, max_total, epoch_size, auto_continue, max_stale_epochs, epoch_index, productive_in_epoch, tool_batches_in_epoch, stale_epochs, tool_batches_this_turn, open_tasks_for_wall, user_wants_stop, assistant_text_acc, no_progress_steps, last_progress_fingerprint, max_no_progress_steps, stalled_finalize, all_error_batches, max_all_error_batches, failed_tools_this_turn, max_failed_tools, force_answer, is_final_step, step_tools, use_openai_sse, collected, round_state, content_parts, reasoning_parts, sid_mm, _verify_green, _keep_after_green, is_disconnect_error, mid_turn_fit_messages, synthesize_from_tools, TurnState, effective_stale_epochs, body, stream_live, text_out, tool_calls_list, reasoning_out, stutter_src
            _st2 = unpack_state(s)
            run_until_done = _st2["run_until_done"]
            build_state = _st2["build_state"]
            turn = _st2["turn"]
            plan_mode = _st2["plan_mode"]
            attachments = _st2["attachments"]
            session_id = _st2["session_id"]
            message = _st2["message"]
            runtime = _st2["runtime"]
            prep = _st2["prep"]
            boot = _st2["boot"]
            messages = _st2["messages"]
            history = _st2["history"]
            all_tools = _st2["all_tools"]
            tools = _st2["tools"]
            browse_pre_url = _st2["browse_pre_url"]
            clear_goals_only = _st2["clear_goals_only"]
            pure_action_kick = _st2["pure_action_kick"]
            open_only_browse = _st2["open_only_browse"]
            page_interaction = _st2["page_interaction"]
            seen_fps = _st2["seen_fps"]
            result_cache = _st2["result_cache"]
            produced_user_text = _st2["produced_user_text"]
            pseudo_recovery_done = _st2["pseudo_recovery_done"]
            pseudo_nudge_count = _st2["pseudo_nudge_count"]
            false_progress_nudge_count = _st2["false_progress_nudge_count"]
            zero_tools_hard_block_count = _st2["zero_tools_hard_block_count"]
            mono_fp_last = _st2["mono_fp_last"]
            mono_fp_hits = _st2["mono_fp_hits"]
            mono_explore_injected = _st2["mono_explore_injected"]
            scratchpad_nudge_count = _st2["scratchpad_nudge_count"]
            tools_executed_this_turn = _st2["tools_executed_this_turn"]
            recovery_nudge_done = _st2["recovery_nudge_done"]
            speed_batch_nudge_done = _st2["speed_batch_nudge_done"]
            serial_explore_streak = _st2["serial_explore_streak"]
            _bind = _st2["_bind"]
            _adapter = _st2["_adapter"]
            headers = _st2["headers"]
            endpoint = _st2["endpoint"]
            _sleev_route = _st2["_sleev_route"]
            _connect_s = _st2["_connect_s"]
            timeout = _st2["timeout"]
            max_length_continuations = _st2["max_length_continuations"]
            _b0 = _st2["_b0"]
            length_continuations = _st2["length_continuations"]
            reasoning_repair_done = _st2["reasoning_repair_done"]
            tool_args_repair_done = _st2["tool_args_repair_done"]
            tool_args_strip_done = _st2["tool_args_strip_done"]
            api_soft_failures = _st2["api_soft_failures"]
            max_api_soft_failures = _st2["max_api_soft_failures"]
            force_answer_sticky = _st2["force_answer_sticky"]
            force_answer_api_fail_once = _st2["force_answer_api_fail_once"]
            force_answer_nudge_done = _st2["force_answer_nudge_done"]
            empty_answer_retries = _st2["empty_answer_retries"]
            max_empty_answer_retries = _st2["max_empty_answer_retries"]
            _b1 = _st2["_b1"]
            agency_rearm_count = _st2["agency_rearm_count"]
            max_agency_rearms = _st2["max_agency_rearms"]
            zero_tool_drive_count = _st2["zero_tool_drive_count"]
            max_zero_tool_drives = _st2["max_zero_tool_drives"]
            open_work_continues = _st2["open_work_continues"]
            max_open_work_continues = _st2["max_open_work_continues"]
            open_work_last_score = _st2["open_work_last_score"]
            open_work_last_batches = _st2["open_work_last_batches"]
            finish_everything_requested = _st2["finish_everything_requested"]
            _st_ow = _st2["_st_ow"]
            step_wall_checkpointed = _st2["step_wall_checkpointed"]
            thinking_choice_repaired = _st2["thinking_choice_repaired"]
            green_gate_reopen_count = _st2["green_gate_reopen_count"]
            max_green_gate_reopens = _st2["max_green_gate_reopens"]
            open_drive_patience = _st2["open_drive_patience"]
            last_progress_score = _st2["last_progress_score"]
            last_progress_step = _st2["last_progress_step"]
            open_drive_stalled_notified = _st2["open_drive_stalled_notified"]
            auth_refresh_done = _st2["auth_refresh_done"]
            max_total = _st2["max_total"]
            epoch_size = _st2["epoch_size"]
            auto_continue = _st2["auto_continue"]
            max_stale_epochs = _st2["max_stale_epochs"]
            epoch_index = _st2["epoch_index"]
            productive_in_epoch = _st2["productive_in_epoch"]
            tool_batches_in_epoch = _st2["tool_batches_in_epoch"]
            stale_epochs = _st2["stale_epochs"]
            tool_batches_this_turn = _st2["tool_batches_this_turn"]
            open_tasks_for_wall = _st2["open_tasks_for_wall"]
            user_wants_stop = _st2["user_wants_stop"]
            assistant_text_acc = _st2["assistant_text_acc"]
            no_progress_steps = _st2["no_progress_steps"]
            last_progress_fingerprint = _st2["last_progress_fingerprint"]
            max_no_progress_steps = _st2["max_no_progress_steps"]
            stalled_finalize = _st2["stalled_finalize"]
            all_error_batches = _st2["all_error_batches"]
            max_all_error_batches = _st2["max_all_error_batches"]
            failed_tools_this_turn = _st2["failed_tools_this_turn"]
            max_failed_tools = _st2["max_failed_tools"]
            force_answer = _st2["force_answer"]
            is_final_step = _st2["is_final_step"]
            step_tools = _st2["step_tools"]
            use_openai_sse = _st2["use_openai_sse"]
            collected = _st2["collected"]
            round_state = _st2["round_state"]
            content_parts = _st2["content_parts"]
            reasoning_parts = _st2["reasoning_parts"]
            sid_mm = _st2["sid_mm"]
            _verify_green = _st2["_verify_green"]
            _keep_after_green = _st2["_keep_after_green"]
            is_disconnect_error = _st2["is_disconnect_error"]
            mid_turn_fit_messages = _st2["mid_turn_fit_messages"]
            synthesize_from_tools = _st2["synthesize_from_tools"]
            TurnState = _st2["TurnState"]  # noqa: N806
            effective_stale_epochs = _st2["effective_stale_epochs"]
            body = _st2["body"]
            stream_live = _st2["stream_live"]
            text_out = _st2["text_out"]
            tool_calls_list = _st2["tool_calls_list"]
            reasoning_out = _st2["reasoning_out"]
            stutter_src = _st2["stutter_src"]


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
                # Hitting the ceiling on step 0 with an armed pack still owes
                # a tool round; last-ditch (no tools) is the round AFTER tools
                # ran. Without this, max_react_steps=1 stripped tools before
                # the first POST and last-ditch tests never saw a tool call.
                _opinion = False
                with suppress(Exception):
                    from remedy.core.react_policy import is_knowledge_question

                    _opinion = bool(is_knowledge_question(message or ""))
                # Fires at most once per turn, and never for the ambiguous
                # read-only peek pack — a model that answers in words twice
                # gets to answer, not a third forced round.
                armed_ceiling_needs_a_tool_round = (
                    bool(is_final_step)
                    and int(tools_executed_this_turn or 0) == 0
                    and bool(tools)
                    and not _verify_green
                    and not _opinion
                    and not bool(getattr(turn, "armed_ceiling_fired", False))
                    and str(getattr(turn, "arm_reason", ""))
                    not in ("ask_first", "no_work_request")
                )
                # Work request + zero tool evidence is never a successful final.
                # Keep tools on and require a native call instead of summarizing.
                if (
                    force_answer
                    and not stalled_finalize
                    and (_work_unfinished() or armed_ceiling_needs_a_tool_round)
                    and all_tools
                    and not message_asks_to_stop(message or "")
                    and (
                        zero_tool_drive_count < max_zero_tool_drives
                        or _open_drive_keeps_going()
                        or armed_ceiling_needs_a_tool_round
                    )
                ):
                    force_answer = False
                    force_answer_sticky = False
                    is_final_step = False
                    if armed_ceiling_needs_a_tool_round:
                        turn.armed_ceiling_fired = True
                    _rearm_agency_tools()
                    if tools:
                        set_turn_force_tool_choice(True)
                    else:
                        # Re-arm declined (chat/trivia turn). Keeping tools off
                        # while refusing to finalize spins the step wall for
                        # nothing — let the turn answer in words instead.
                        force_answer = True
                        is_final_step = True
                # Offered tools (incl. one forced round) and still answered in
                # words: strong per-partner not-work signal for intent_learn.
                if (
                    force_answer
                    and bool(getattr(turn, "armed_ceiling_fired", False))
                    and int(tools_executed_this_turn or 0) == 0
                    and not bool(getattr(turn, "intent_declined_recorded", False))
                ):
                    turn.intent_declined_recorded = True
                    with suppress(Exception):
                        from remedy.core.intent_learn import record_tools_declined

                        record_tools_declined(message or "")
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
