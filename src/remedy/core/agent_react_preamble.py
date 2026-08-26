"""Turn preamble for ReAct — distill, context, vision, tools selection.

Extracted from ``call_llm_stream`` so the epoch loop stays readable and
preamble pieces can be unit-tested independently.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from remedy.core.provider_sanitize import sanitize_chat_body  # noqa: F401 — re-export habit
from remedy.core.react_policy import TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP
from remedy.core.react_stream import (
    build_runtime_system_block,
    should_enable_tools,
)
from remedy.home import default_home


def _effective_max_steps(runtime: Any) -> int:
    """This turn's step ceiling — a per-turn override wins over the shared runtime."""
    from remedy.core.turn_context import turn_max_react_steps

    return turn_max_react_steps(runtime) or int(
        getattr(runtime, "_max_react_steps", 0) or 0
    )


@dataclass
class BrowseIntentFlags:
    browse_pre_url: str | None = None
    clear_goals_only: bool = False
    pure_action_kick: bool = False
    open_only_browse: bool = False
    page_interaction: bool = False


@dataclass
class TurnPreamble:
    """Everything prepared before the multi-epoch LLM loop."""

    context: str
    history: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    all_tools: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    browse: BrowseIntentFlags = field(default_factory=BrowseIntentFlags)
    vision_mode: str = "native"
    decode_brief: str | None = None
    early_reply: str | None = None  # L0 instant — caller should yield and return
    status_events: list[str] = field(default_factory=list)
    library_suggest_json: str | None = None
    pure_action_kick: bool = False
    clear_goals_only: bool = False


async def distill_user_message(
    runtime: Any,
    message: str,
    session_id: str | None,
) -> None:
    """Partner-memory distill before tools/LLM (explicit remember is awaited)."""
    with suppress(Exception):
        from remedy.core.turn_context import set_turn_last_user_text

        set_turn_last_user_text((message or "")[:4000], runtime)
    with suppress(Exception):
        from remedy.core.agent_post_turn import distill_user_message_now_async
        from remedy.memory.partner_memory import distill_user_text, is_explicit_remember_intent

        msg0 = message or ""
        if is_explicit_remember_intent(msg0) and getattr(runtime, "memory", None) is not None:
            project_path = str(
                getattr(getattr(runtime, "config", None), "project_path", None)
                or getattr(runtime, "_project_path", None)
                or ""
            ) or None
            # Main distill awaited here; the now-mirror then only saves the
            # extracted facts (also awaited) — nothing blocks the event loop.
            await distill_user_text(
                runtime.memory,
                msg0,
                brief=getattr(runtime, "_session_brief", None),
                session_id=session_id,
                project_path=project_path,
            )
            await distill_user_message_now_async(
                runtime, msg0, session_id=session_id, already_distilled=True
            )
        else:
            await distill_user_message_now_async(runtime, msg0, session_id=session_id)


def parse_browse_intent(message: str) -> BrowseIntentFlags:
    flags = BrowseIntentFlags()
    with suppress(Exception):
        from remedy.core.computer.browse_intent import (
            is_clear_goals_intent,
            is_open_only_browse,
            is_pure_action_kick,
            parse_browse_navigate_url,
            wants_page_interaction,
        )

        flags.browse_pre_url = parse_browse_navigate_url(message or "")
        flags.clear_goals_only = is_clear_goals_intent(message or "")
        flags.pure_action_kick = is_pure_action_kick(message or "")
        flags.open_only_browse = is_open_only_browse(message or "")
        flags.page_interaction = wants_page_interaction(message or "")
    return flags


def append_plan_and_computer_addenda(
    context: str,
    *,
    session_id: str | None,
    plan_mode: bool,
    runtime: Any,
    message: str = "",
) -> str:
    with suppress(Exception):

        from remedy.core.plan_store import PlanStore

        home = getattr(runtime.config, "home_dir", None) or default_home()
        store = PlanStore(home)
        plan = store.latest_for_session(session_id)
        if plan is not None:
            context = (context or "") + "\n\n## Active task plan\n" + plan.summary_markdown()
        if plan_mode:
            from remedy.core.plan_store import PLAN_MODE_SYSTEM_ADDENDUM

            context = (context or "") + "\n\n" + PLAN_MODE_SYSTEM_ADDENDUM
        else:
            chat_only = False
            with suppress(Exception):
                from remedy.core.react_policy import is_chat_only_message

                chat_only = is_chat_only_message(message or "")
            if not chat_only:
                with suppress(Exception):
                    from remedy.core.muscle_profile import muscle_from_runtime
                    from remedy.core.plan_store import (
                        BUILD_MODE_SYSTEM_ADDENDUM,
                        FRONTIER_BUILD_MODE_ADDENDUM,
                    )

                    muscle = muscle_from_runtime(runtime)
                    add = (
                        FRONTIER_BUILD_MODE_ADDENDUM
                        if muscle.is_frontier
                        else BUILD_MODE_SYSTEM_ADDENDUM
                    )
                    context = (context or "") + "\n\n" + add
        with suppress(Exception):
            from remedy.core.build_todos import format_todos_block, load_todos

            todo_block = format_todos_block(load_todos(runtime))
            if todo_block:
                context = (context or "") + "\n\n" + todo_block
        with suppress(Exception):
            from remedy.core.companion import (
                format_companion_block,
                gather_companion_snapshot,
                looks_like_companion_request,
            )
            from remedy.core.turn_context import current_last_user_text

            um = current_last_user_text(runtime)
            if looks_like_companion_request(um):
                snap = gather_companion_snapshot(runtime)
                block = format_companion_block(snap)
                if block:
                    context = (context or "") + "\n\n" + block
                with suppress(Exception):
                    from remedy.core.companion_taste import extract_taste, remember_taste

                    for fact in extract_taste(um):
                        remember_taste(fact, runtime)
        with suppress(Exception):
            from remedy.core.companion_taste import format_taste_block, load_taste

            tblock = format_taste_block(load_taste(runtime))
            if tblock:
                context = (context or "") + "\n\n" + tblock
        with suppress(Exception):
            from remedy.core.away_mode import format_away_block, looks_like_away_request
            from remedy.core.turn_context import current_last_user_text

            um_a = current_last_user_text(runtime)
            if looks_like_away_request(um_a):
                context = (context or "") + "\n\n" + format_away_block()
                with suppress(Exception):
                    from remedy.core.build_engine import looks_like_build_request
                    from remedy.memory.life_drive import take_step

                    if not looks_like_build_request(um_a):
                        home = getattr(getattr(runtime, "config", None), "home_dir", None)
                        take_step(home)
        with suppress(Exception):
            from remedy.core.companion import looks_like_companion_request
            from remedy.core.companion_inbox import format_inbox_block, poll_new_drops
            from remedy.core.turn_context import current_last_user_text

            um_i = current_last_user_text(runtime)
            if looks_like_companion_request(um_i):
                drops = poll_new_drops(runtime, mark_seen=True)
                ib = format_inbox_block(drops)
                if ib:
                    context = (context or "") + "\n\n" + ib
        with suppress(Exception):
            from remedy.core.computer.guidance import (
                COMPUTER_USE_SYSTEM_ADDENDUM,
                needs_computer_use_guidance,
            )
            from remedy.core.turn_context import current_last_user_text

            um = message or current_last_user_text(runtime)
            if needs_computer_use_guidance(um):
                context = (context or "") + "\n\n" + COMPUTER_USE_SYSTEM_ADDENDUM
        # Skill memory: steer toward the click approach that has worked on the
        # current site (learned from past actions, per host).
        with suppress(Exception):
            from remedy.core.computer.computer_skill import _skill_host_hint

            hint = _skill_host_hint(runtime)
            if hint:
                context = (context or "") + "\n\n[Computer skill] " + hint
    return context or ""


def try_early_l0(
    runtime: Any,
    message: str,
    *,
    session_id: str | None,
    plan_mode: bool,
    attachments: list[dict[str, Any]] | None,
) -> str | None:
    if plan_mode or attachments:
        return None
    with suppress(Exception):
        from remedy.core.metabolism.l0 import try_l0_system_reply
        from remedy.core.metabolism.tier import TurnTier, classify_turn_tier

        if classify_turn_tier(message or "", tools_enabled=False) == TurnTier.L0_INSTANT:
            l0_early = try_l0_system_reply(runtime, message or "", preclassified=True)
            if l0_early:
                with suppress(Exception):
                    from remedy.core.session_quality import get_session_quality

                    get_session_quality(
                        str(session_id or getattr(runtime, "_session_id", "") or "")
                    ).record_metabolism(tier=0)
                return l0_early
    return None


def run_vision_decode(
    runtime: Any,
    attachments: list[dict[str, Any]] | None,
) -> tuple[str, str | None, list[str]]:
    """Returns (vision_mode, decode_brief, status_events)."""
    from remedy.core.llm_binding import get_llm_binding

    vision_mode = "native"
    decode_brief: str | None = None
    events: list[str] = []
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
        if mode in ("decode", "text_only") and vres.get("combined"):
            vision_mode = "decode"
            decode_brief = str(vres.get("combined") or "")
            for ev in vres.get("events") or []:
                events.append(f"@@status:{ev}\n")
        elif mode == "text_only" and vres.get("briefs"):
            vision_mode = "decode"
            briefs = vres.get("briefs") or []
            decode_brief = "\n".join(str(b) for b in briefs if b)
            for ev in vres.get("events") or []:
                events.append(f"@@status:{ev}\n")
        elif mode == "unavailable" and vres.get("hint"):
            vision_mode = "decode"
            decode_brief = (
                f"[Visual decoder unavailable] {vres.get('hint')}\n"
                "Image files are attached by path only."
            )
            events.append(
                "@@status:Visual decoder unavailable — "
                "enable in Settings for local image understanding\n"
            )
    return vision_mode, decode_brief, events


def select_tools_for_turn(
    runtime: Any,
    message: str,
    *,
    plan_mode: bool,
    attachments: list[dict[str, Any]] | None,
    history: list[dict[str, Any]],
    all_tools: list[dict[str, Any]],
    browse: BrowseIntentFlags,
) -> list[dict[str, Any]] | None:
    if plan_mode:
        from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

        return [
            t
            for t in all_tools
            if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
        ]
    open_tasks: list[str] = []
    with suppress(Exception):
        brief = getattr(runtime, "_session_brief", None)
        if brief is not None:
            open_tasks = list(getattr(brief, "open_tasks", None) or [])
    tools: list[dict[str, Any]] | None = (
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
    if (browse.browse_pre_url or browse.page_interaction) and all_tools and not plan_mode:
        tools = all_tools
    return tools


def apply_metabolism_injects(
    runtime: Any,
    messages: list[dict[str, Any]],
    message: str,
    *,
    session_id: str | None,
    plan_mode: bool,
    attachments: list[dict[str, Any]] | None,
    all_tools: list[dict[str, Any]],
    browse: BrowseIntentFlags,
    pure_action_kick: bool,
) -> None:
    with suppress(Exception):
        from remedy.core.metabolism.turn import begin_turn_metabolism

        sid_m = str(getattr(runtime, "_session_id", "") or session_id or "")
        intent_m = "chat"
        with suppress(Exception):
            from remedy.core.turn_context import turn_context_snapshot

            snap = turn_context_snapshot(runtime)
            if snap is not None:
                intent_m = str(getattr(snap, "intent", None) or "chat")
        roots_m: list[str] = []
        with suppress(Exception):
            roots_m = list(getattr(runtime, "_work_roots", None) or [])
        pre_tier_m = None
        with suppress(Exception):
            from remedy.core.turn_context import turn_tier as _tt_pre

            raw_pt = getattr(runtime, "_turn_tier_preclassified", None)
            if raw_pt is None:
                raw_pt = _tt_pre(runtime, default=0) or None
            if raw_pt is not None:
                pre_tier_m = int(raw_pt)
        home_m = None
        with suppress(Exception):
            home_m = getattr(getattr(runtime, "config", None), "home_dir", None)
        meta = begin_turn_metabolism(
            session_id=sid_m,
            user_text=message or "",
            intent=intent_m,
            plan_mode=bool(plan_mode),
            has_attachments=bool(attachments),
            tools_enabled=bool(all_tools),
            pure_action=bool(pure_action_kick),
            browse=bool(browse.browse_pre_url or browse.page_interaction),
            project_path=str(getattr(runtime, "_project_path", "") or ""),
            work_roots=roots_m or None,
            brief_head=(message or "")[:200],
            pre_tier=pre_tier_m,
            runtime=runtime,
            home=home_m,
        )
        from remedy.core.turn_context import (
            set_turn_action_ir,
            set_turn_force_spread,
            set_turn_metabolism_allow_verify,
            set_turn_shadow_strict,
            set_turn_tier,
            take_pending_verify_remedy,
        )

        # `or 1` here turned a classified L0_INSTANT (tier 0) into L1 at the
        # moment it was stored, so the fast path could never fire.
        _raw_tier = meta.get("tier")
        set_turn_tier(
            int(_raw_tier) if _raw_tier is not None else 1,
            runtime,
            label=str(meta.get("tier_label") or ""),
        )
        set_turn_force_spread(bool(meta.get("force_spread")), runtime)
        set_turn_action_ir(meta.get("action_ir"), runtime)
        set_turn_metabolism_allow_verify(
            bool(meta.get("allow_critical_verify")), runtime
        )
        set_turn_shadow_strict(
            bool(
                (meta.get("policy") or {}).get("shadow_high_blast")
                and int(meta.get("tier") or 0) >= 2
            ),
            runtime,
        )
        with suppress(Exception):
            from remedy.core.metabolism.governor import get_governor

            if get_governor(sid_m).shadow_strict:
                set_turn_shadow_strict(True, runtime)
        injects = list(meta.get("injects") or [])
        with suppress(Exception):
            pending = take_pending_verify_remedy(sid_m)
            if pending is None:
                pending = getattr(runtime, "_pending_verify_remedy", None)
                if pending:
                    runtime._pending_verify_remedy = None
            if pending:
                injects.append(str(pending))
        if injects:
            messages.insert(
                -1,
                {
                    "role": "system",
                    "content": "\n\n".join(str(x) for x in injects if x),
                },
            )


async def prepare_turn_preamble(
    runtime: Any,
    message: str,
    session_id: str | None,
    attachments: list[dict[str, Any]] | None,
    *,
    plan_mode: bool = False,
) -> TurnPreamble:
    """Build context, history, messages, tools; may set early_reply for L0."""
    from remedy.core.llm_binding import get_llm_binding
    from remedy.interfaces.attachments import build_multimodal_user_content

    await distill_user_message(runtime, message, session_id)
    context = await runtime._build_context()
    context = append_plan_and_computer_addenda(
        context,
        session_id=session_id,
        plan_mode=plan_mode,
        runtime=runtime,
        message=message or "",
    )
    history = await runtime._load_session_history(session_id, message)

    early = try_early_l0(
        runtime,
        message,
        session_id=session_id,
        plan_mode=plan_mode,
        attachments=attachments,
    )
    if early:
        return TurnPreamble(
            context=context,
            history=history,
            messages=[],
            all_tools=[],
            tools=None,
            early_reply=early,
        )

    browse = parse_browse_intent(message or "")
    with suppress(Exception):
        from remedy.core.turn_context import (
            set_turn_browse,
            set_turn_has_attachments,
            set_turn_pure_action,
        )

        set_turn_browse(bool(browse.browse_pre_url or browse.page_interaction), runtime)
        set_turn_pure_action(bool(browse.pure_action_kick), runtime)
        set_turn_has_attachments(bool(attachments), runtime)

    with suppress(Exception):
        from remedy.memory.harness.pruner import prune_messages_for_send

        mode = str(getattr(runtime, "_harness_mode", "auto") or "auto").lower()
        if mode == "manual":
            history = prune_messages_for_send(
                history,
                max_tool_chars=_TOOL_RESULT_CHAR_CAP,
                dedupe_tools=True,
            )

    vision_mode, decode_brief, status_events = run_vision_decode(runtime, attachments)

    home_att = None
    with suppress(Exception):
        home_att = getattr(getattr(runtime, "config", None), "home_dir", None)
    sid_att = str(session_id or getattr(runtime, "_session_id", None) or "") or None
    user_content = build_multimodal_user_content(
        message,
        attachments,
        vision_mode=vision_mode,
        decode_brief=decode_brief,
        home_dir=home_att,
        session_id=sid_att,
    )
    _bind0 = get_llm_binding(runtime)
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_runtime_system_block(
                system_prompt=runtime._system_prompt,
                provider=_bind0.provider,
                model=_bind0.model,
                base_url=_bind0.base_url,
                max_steps=_effective_max_steps(runtime),
                context=context,
                user_message=str(message or ""),
            ),
        },
        *history,
        {"role": "user", "content": user_content},
    ]
    # Local create/build: tool-first contract + full power approvals
    with suppress(Exception):
        from remedy.core.local_agent_optimize import (
            ensure_local_power_approvals,
            inject_local_messages,
            is_local_binding,
            message_wants_implement,
        )

        if (not plan_mode) and is_local_binding(_bind0.provider, _bind0.model, _bind0.base_url):
            # Always auto-approve + low thinking for local builds — never stall
            # mid-file_write on thumbs-down mode or High thinking monologues.
            ensure_local_power_approvals()
            with suppress(Exception):
                from remedy.core.turn_context import set_turn_thinking_level

                set_turn_thinking_level("low")
            _proj = ""
            with suppress(Exception):
                _proj = str(runtime.effective_project_path() or "")
            # Continue/empty "continue" still gets full implement contract
            _um = str(message or "")
            if not message_wants_implement(_um):
                _um = f"{_um} build implement".strip()
            messages = inject_local_messages(
                messages,
                user_message=_um,
                provider=_bind0.provider,
                model=_bind0.model,
                base_url=_bind0.base_url,
                project_path=_proj,
            )
            # First-step explore inject so the model cannot pure-monologue
            with suppress(Exception):
                from remedy.core.local_agent_optimize import project_listing_snapshot

                listing = project_listing_snapshot(_proj)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[Partner · PROJECT TREE — tools required]\n"
                            f"{listing}\n\n"
                            "Reply with native tool_calls only "
                            "(file_read / file_write / file_edit / bash_exec). "
                            "Do not restate the plan."
                        ),
                    }
                )
    with suppress(Exception):
        from remedy.core.turn_context import take_build_protocol, take_frontier_continue

        proto = take_build_protocol(session_id)
        if proto is None:
            proto = getattr(runtime, "_build_protocol_pending", None)
            if proto:
                runtime._build_protocol_pending = None
        if proto:
            messages.append({"role": "system", "content": str(proto)})
        fc = take_frontier_continue(session_id)
        if fc is None:
            fc = getattr(runtime, "_frontier_continue_pending", None)
            if isinstance(fc, dict) and fc.get("content"):
                runtime._frontier_continue_pending = None
            else:
                fc = None
        if isinstance(fc, dict) and fc.get("content"):
            messages.append(fc)

    library_suggest_json: str | None = None
    with suppress(Exception):
        if runtime._harness_mode == "auto":
            from remedy.memory.harness.send_policy import apply_auto_harness_send_policy

            sid = str(getattr(runtime, "_session_id", "") or session_id or "")
            messages, _hmeta = apply_auto_harness_send_policy(
                runtime,
                messages,
                user_text=message or "",
                session_id=sid,
                tool_result_char_cap=int(_TOOL_RESULT_CHAR_CAP or 0),
            )
            with suppress(Exception):
                from remedy.core.turn_context import turn_context_snapshot

                snap = turn_context_snapshot(runtime)
                lib = (getattr(snap, "signals", None) or {}).get("library_suggest")
                if isinstance(lib, dict) and lib.get("id"):
                    import json as _json

                    library_suggest_json = _json.dumps(
                        lib, default=str, separators=(",", ":")
                    )

    all_tools = runtime._openai_tools()
    with suppress(Exception):
        from remedy.core.agent_llm import tools_for_binding

        all_tools = tools_for_binding(all_tools, runtime) or all_tools
    with suppress(Exception):
        from remedy.core.hive.policy import filter_daughter_tools, hive_depth

        if hive_depth() >= 1:
            all_tools = filter_daughter_tools(all_tools)

    tools = select_tools_for_turn(
        runtime,
        message,
        plan_mode=plan_mode,
        attachments=attachments,
        history=history,
        all_tools=all_tools,
        browse=browse,
    )
    # Local implement: first-turn tools are write-first (no bash skip-write)
    with suppress(Exception):
        from remedy.core.local_agent_optimize import (
            filter_tools_write_first,
            is_local_binding,
        )

        if is_local_binding(_bind0.provider, _bind0.model, _bind0.base_url) and tools:
            tools = filter_tools_write_first(
                tools,
                user_message=str(message or ""),
                step_index=0,
                history=messages if isinstance(messages, list) else None,
            )
            # Keep all_tools full for later re-arm; only arm write-first now
            # (rearm still uses all_tools when needed after a write succeeds)

    apply_metabolism_injects(
        runtime,
        messages,
        message,
        session_id=session_id,
        plan_mode=plan_mode,
        attachments=attachments,
        all_tools=all_tools,
        browse=browse,
        pure_action_kick=browse.pure_action_kick,
    )
    if (browse.browse_pre_url or browse.page_interaction) and all_tools and not plan_mode:
        tools = all_tools

    return TurnPreamble(
        context=context,
        history=history,
        messages=messages,
        all_tools=all_tools,
        tools=tools,
        browse=browse,
        vision_mode=vision_mode,
        decode_brief=decode_brief,
        status_events=status_events,
        library_suggest_json=library_suggest_json,
        pure_action_kick=browse.pure_action_kick,
        clear_goals_only=browse.clear_goals_only,
    )


async def yield_preamble_events(prep: TurnPreamble) -> AsyncIterator[str]:
    """Yield status / library_suggest events from preamble."""
    for ev in prep.status_events:
        yield ev
    if prep.library_suggest_json:
        yield "@@library_suggest:" + prep.library_suggest_json + "\n"
