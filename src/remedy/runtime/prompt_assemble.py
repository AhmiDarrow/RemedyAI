"""Assemble system/soul/skills/memory context for Go cognition turns.

Called over RMDY as ``prompt.assemble`` from ``CognitionTurnRunner`` before the
model sees the turn. Reuses the forever-Python prompt modules (system prompt,
``build_turn_context``, ``build_runtime_system_block``) so production Go turns
are equal-or-better than raw prompt-only.

Every handler that touches per-session state (brief, build state, memory,
skills) runs inside ``begin_turn(session_id) … end_turn`` so concurrent
sessions sharing one worker process never read each other's working memory.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import suppress
from typing import Any

logger = logging.getLogger("remedy.runtime.prompt_assemble")

_runtime_cache: dict[str, Any] = {}

# Recent-conversation block appended after the stable operational prompt.
HISTORY_BLOCK_CHAR_CAP = 12_000
HISTORY_ENTRY_CHAR_CAP = 4_000
HISTORY_MAX_ENTRIES = 12
_HISTORY_HEADER = (
    "Recent conversation (oldest first; the current message follows separately "
    "— do not repeat it):"
)

# Epoch material is append-only: everything after this marker is replaced on
# the next epoch, so the cached system prefix only ever grows at the end.
EPOCH_BRIEF_MARKER = "\n\n[Session Brief · epoch working memory]\n"
EPOCH_BRIEF_CHAR_CAP = 1_200

# Per-turn re-arm ceilings for the continue gate (keyed by session + goal).
_GATE_VERIFY_FAILED_MAX = 3
_GATE_MISSION_MAX = 2

_LEDGER_LINE_RE = re.compile(
    r"^\W*(?P<name>[a-z][a-z0-9_]*\.[a-z0-9_*]+)\s*"
    r"(?:\[(?P<status>ok|fail|failed|error|err|timeout)\])?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


def _home_key(home: str | None) -> str:
    from pathlib import Path

    raw = (home or "").strip() or ""
    if not raw:
        from remedy.home import default_home

        return str(default_home())
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return str(Path(raw).expanduser())


def _agent_config(
    *,
    home_dir: str | None,
    project_path: str | None,
    provider: str | None,
    model: str | None,
    base_url: str | None,
) -> Any:
    from remedy.interfaces.config import config_to_agent_config, load_config

    cfg = dict(load_config())
    if home_dir:
        cfg["home_dir"] = home_dir
    if project_path:
        cfg["project_path"] = project_path
    if provider:
        cfg["llm_provider"] = provider
    if model:
        cfg["llm_model"] = model
    if base_url:
        cfg["llm_base_url"] = base_url
    return config_to_agent_config(cfg)


async def _get_runtime(config: Any) -> Any:
    from remedy.core.agent import BasicRuntime

    key = _home_key(getattr(config, "home_dir", None))
    cached = _runtime_cache.get(key)
    if cached is not None:
        with suppress(Exception):
            cached.config = config
            cached._llm_provider = getattr(config, "llm_provider", cached._llm_provider)
            cached._llm_model = getattr(config, "llm_model", cached._llm_model)
            cached._llm_base_url = getattr(config, "llm_base_url", cached._llm_base_url)
            cached._llm_api_key = getattr(config, "llm_api_key", cached._llm_api_key)
        return cached

    # Tool ABI lives on Go; this harness only needs memory/soul/skills text.
    runtime = BasicRuntime(config, register_tools=False)
    with suppress(Exception):
        await runtime.memory.initialize()
    _runtime_cache[key] = runtime
    return runtime


def _binding_of(runtime: Any) -> tuple[str, str, str]:
    return (
        str(getattr(runtime, "_llm_provider", "") or "openai"),
        str(getattr(runtime, "_llm_model", "") or ""),
        str(getattr(runtime, "_llm_base_url", "") or ""),
    )


def _rebuild_system_prompt(runtime: Any) -> str:
    from remedy.core.local_agent_optimize import is_frontier_binding
    from remedy.core.react_policy import build_system_prompt

    cfg = runtime.config
    provider, model, base_url = _binding_of(runtime)
    prompt = build_system_prompt(
        getattr(cfg, "persona", None),
        name=getattr(cfg, "name", None),
        gender=getattr(cfg, "agent_gender", None),
        ui_language=getattr(cfg, "ui_language", None),
        compact=bool(is_frontier_binding(provider, model, base_url)),
    )
    runtime._system_prompt = prompt
    return prompt


def render_history_block(
    history: Any,
    *,
    cap: int = HISTORY_BLOCK_CHAR_CAP,
    entry_cap: int = HISTORY_ENTRY_CHAR_CAP,
    max_entries: int = HISTORY_MAX_ENTRIES,
) -> str:
    """Compact role-labelled transcript of the last session messages.

    Newest last; when the block would exceed *cap* the oldest entries are
    dropped first (the oldest kept entry is clipped if that buys a useful
    remainder). Returns ``""`` when there is nothing to show.
    """
    rows: list[tuple[str, str]] = []
    for item in history or []:
        if not isinstance(item, Mapping):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        if len(content) > entry_cap:
            content = content[: entry_cap - 1].rstrip() + "…"
        rows.append((role, content))
    rows = rows[-max(0, int(max_entries)) :] if max_entries else rows
    if not rows:
        return ""
    budget = max(0, int(cap) - len(_HISTORY_HEADER) - 1)
    kept: list[str] = []
    used = 0
    for role, content in reversed(rows):
        line = f"[{role}] {content}"
        need = len(line) + 1
        if used + need > budget:
            room = budget - used - 1
            if room >= 400:
                kept.append(line[: room - 1].rstrip() + "…")
            break
        kept.append(line)
        used += need
    if not kept:
        return ""
    kept.reverse()
    return _HISTORY_HEADER + "\n" + "\n".join(kept)


def _session_user_text_store(runtime: Any) -> dict[str, str]:
    store = getattr(runtime, "_rmdy_user_text_by_session", None)
    if not isinstance(store, dict):
        store = {}
        with suppress(Exception):
            runtime._rmdy_user_text_by_session = store
    return store


def _remember_user_text(runtime: Any, session_id: str | None, message: str) -> None:
    sid = str(session_id or "").strip()
    if not sid or not message.strip():
        return
    store = _session_user_text_store(runtime)
    store[sid] = message[:4000]
    if len(store) > 64:
        for k in list(store.keys())[: len(store) - 48]:
            if k != sid:
                store.pop(k, None)


@contextlib.contextmanager
def _turn_scope(
    runtime: Any,
    session_id: str | None,
    *,
    plan_mode: bool = False,
    chat_mode: bool = False,
    brief: Any = None,
) -> Iterator[None]:
    """Session identity for one RMDY call on the shared cached runtime."""
    from remedy.core.turn_context import begin_turn, end_turn

    tokens = begin_turn(
        session_id,
        project_raw=getattr(runtime, "_project_path_raw", None),
        active_path=str(getattr(runtime, "_active_project_path", "") or "") or None,
        plan_mode=bool(plan_mode) and not bool(chat_mode),
        chat_mode=bool(chat_mode),
        session_brief=brief,
    )
    try:
        yield
    finally:
        end_turn(session_id, *tokens)


async def _assemble_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    from remedy.core.agent_context import build_turn_context
    from remedy.core.agent_react_preamble import (
        _effective_max_steps,
        append_plan_and_computer_addenda,
    )
    from remedy.core.llm_binding import (
        LlmBinding,
        reset_llm_binding,
        set_llm_binding,
    )
    from remedy.core.react_stream import build_runtime_system_block
    from remedy.core.turn_context import set_turn_last_user_text
    from remedy.memory.harness.send_policy import _ensure_session_brief

    message = str(inp.get("message") or inp.get("prompt") or "")
    session_id = str(inp.get("session_id") or "").strip() or None
    plan_mode = bool(inp.get("plan_mode") or False)
    chat_mode = bool(inp.get("chat_mode") or False)
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    provider = str(inp.get("provider") or "").strip() or None
    model = str(inp.get("model") or "").strip() or None
    base_url = str(inp.get("base_url") or "").strip() or None
    history = inp.get("history")

    config = _agent_config(
        home_dir=home_dir,
        project_path=project_path,
        provider=provider,
        model=model,
        base_url=base_url,
    )
    runtime = await _get_runtime(config)
    system_prompt = _rebuild_system_prompt(runtime)
    _remember_user_text(runtime, session_id, message)

    if project_path:
        with suppress(Exception):
            runtime._project_path_raw = project_path
            from remedy.core.workspace import resolve_project_path

            runtime._active_project_path = resolve_project_path(project_path)

    prov, mdl, burl = _binding_of(runtime)
    bind = LlmBinding(
        provider=prov,
        model=mdl,
        base_url=burl,
        api_key=str(getattr(runtime, "_llm_api_key", "") or ""),
    )
    llm_tok = set_llm_binding(bind)
    brief = _ensure_session_brief(runtime, session_id or "")
    runtime._plan_mode = bool(plan_mode) and not bool(chat_mode)
    runtime._chat_mode = bool(chat_mode)
    try:
        with _turn_scope(
            runtime, session_id, plan_mode=plan_mode, chat_mode=chat_mode, brief=brief
        ):
            set_turn_last_user_text(message, runtime)
            context = await build_turn_context(runtime)
            context = append_plan_and_computer_addenda(
                context,
                session_id=session_id,
                plan_mode=bool(plan_mode) and not bool(chat_mode),
                runtime=runtime,
                message=message,
            )
            system = build_runtime_system_block(
                system_prompt=system_prompt,
                provider=bind.provider,
                model=bind.model,
                base_url=bind.base_url,
                max_steps=_effective_max_steps(runtime),
                context=context,
                user_message=message,
            )
            if not str(system or "").strip():
                raise RuntimeError("prompt.assemble produced an empty system block")
            # Append, never prepend: the operational prefix stays byte-stable
            # for provider prompt caches; only the tail changes per turn.
            history_block = render_history_block(history)
            if history_block:
                system = system.rstrip() + "\n\n" + history_block
            return {
                "system": system,
                "goal": message,
                "context_chars": len(context or ""),
                "system_chars": len(system),
                "history_chars": len(history_block),
            }
    finally:
        reset_llm_binding(llm_tok)
        runtime._plan_mode = False
        runtime._chat_mode = False


def assemble_prompt(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler entry — builds system + goal for one cognition turn."""
    return _run_coro(_assemble_async(inp))


def slim_epoch(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Soft-epoch Memory Harness: prune/offload/brief for Go cognition.

    Fail-soft: on any error returns the input system/text unchanged with
    ``ok=false`` so the Go engine keeps its local compact.
    """
    try:
        return _run_coro(_slim_epoch_async(inp))
    except Exception as exc:  # noqa: BLE001 — RMDY must never crash the turn
        logger.warning("prompt.slim_epoch failed: %s", exc)
        return {
            "ok": False,
            "system": str(inp.get("system") or ""),
            "text": str(inp.get("text") or ""),
            "brief": "",
            "error": str(exc)[:240],
        }


def should_continue(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Decide whether a text-only model completion should re-arm tools."""
    try:
        return _run_coro(_should_continue_async(inp))
    except Exception as exc:  # noqa: BLE001
        logger.warning("prompt.should_continue failed: %s", exc)
        # Fail closed: an exception is not evidence of unfinished work.
        return {
            "ok": False,
            "continue": False,
            "nudge": "",
            "reason": "error",
            "error": str(exc)[:240],
        }


def split_epoch_material(system: str) -> tuple[str, str]:
    """``(primary, epoch_tail)`` — the tail is what a previous epoch appended."""
    idx = system.rfind(EPOCH_BRIEF_MARKER)
    if idx < 0:
        return system, ""
    return system[:idx], system[idx + len(EPOCH_BRIEF_MARKER) :]


def _coerce_lines(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items: list[Any] = raw.splitlines()
    elif isinstance(raw, list | tuple):
        items = list(raw)
    else:
        return []
    out: list[str] = []
    for item in items:
        line = str(item or "").strip()
        if line:
            out.append(line[:400])
    return out[-40:]


def ledger_lines_to_results(lines: list[str]) -> list[dict[str, Any]]:
    """Parse ``- tool.id [ok] detail`` ledger lines into evidence rows."""
    from remedy.core.react_policy import parse_exit_code

    rows: list[dict[str, Any]] = []
    for line in lines:
        m = _LEDGER_LINE_RE.match(line)
        if not m:
            continue
        status = str(m.group("status") or "").lower()
        rest = str(m.group("rest") or "")
        code = parse_exit_code(rest)
        ok = status in ("", "ok") and (code is None or code == 0)
        rows.append({"name": m.group("name"), "ok": ok, "tail": rest[:2000]})
    return rows


def _observe_build_evidence(
    runtime: Any,
    *,
    goal: str,
    session_id: str | None,
    evidence: Mapping[str, Any],
) -> Any:
    """Update + persist this session's build state from tool evidence."""
    from remedy.core.build_engine import (
        ensure_build_state_for_goal,
        get_build_state,
        observe_result_rows,
        persist_build_state,
    )

    has_rows = bool(evidence.get("names"))
    st = get_build_state(runtime)
    if st is None and has_rows:
        st = ensure_build_state_for_goal(runtime, goal, session_id=session_id)
    if st is None:
        return None
    if has_rows:
        observe_result_rows(st, evidence)
        persist_build_state(runtime, st)
    return st


async def _slim_epoch_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    from remedy.core.react_policy import (
        TOOL_RESULT_CHAR_CAP,
        summarize_tool_evidence,
    )
    from remedy.memory.harness.brief import brief_to_context_block
    from remedy.memory.harness.send_policy import (
        _ensure_session_brief,
        apply_auto_harness_send_policy,
        slim_messages_mid_turn,
    )

    system = str(inp.get("system") or "")
    goal = str(inp.get("goal") or inp.get("message") or "")
    text = str(inp.get("text") or "")
    checkpoint = str(inp.get("checkpoint") or "")
    session_id = str(inp.get("session_id") or "").strip()
    epoch = int(inp.get("epoch") or 0)
    total_steps = int(inp.get("total_steps") or 0)
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    provider = str(inp.get("provider") or "").strip() or None
    model = str(inp.get("model") or "").strip() or None
    base_url = str(inp.get("base_url") or "").strip() or None

    runtime = await get_cached_runtime(
        home_dir=home_dir,
        project_path=project_path,
        provider=provider,
        model=model,
        base_url=base_url,
    )
    primary, _prior_tail = split_epoch_material(system)
    working = text.strip()
    ledger_lines = _coerce_lines(inp.get("ledger"))
    # Older runners sent the assistant text twice under ``checkpoint``; accept
    # a distinct checkpoint as ledger material, never concatenate it to text.
    if not ledger_lines and checkpoint.strip() and checkpoint.strip() != working:
        ledger_lines = _coerce_lines(checkpoint)

    brief = _ensure_session_brief(runtime, session_id)
    with _turn_scope(runtime, session_id or None, brief=brief):
        # Outcome lines go into the history thread — the prose tail is not a
        # reliable record of what actually ran.
        summary = "; ".join(ledger_lines)[:500] if ledger_lines else working[-300:]
        if summary:
            with suppress(Exception):
                brief.append_history_thread(f"Epoch {epoch} @ step {total_steps}: {summary}")
                if goal.strip() and not (brief.intent or "").strip():
                    brief.intent = goal.strip()[:400]
                # A soft epoch is a compaction pass: it is what the owner sees
                # as "compress passes" in /memory and in the context-compressed
                # notice, so it has to count.
                brief.compress_count = int(getattr(brief, "compress_count", 0) or 0) + 1
                brief.touch()

        with suppress(Exception):
            evidence = summarize_tool_evidence(ledger_lines_to_results(ledger_lines))
            _observe_build_evidence(
                runtime, goal=goal, session_id=session_id or None, evidence=evidence
            )

        # Tagged slots: the harness may insert/replace messages, so the
        # primary system is found by tag afterwards — never by substring.
        messages: list[dict[str, Any]] = []
        if primary.strip():
            messages.append({"role": "system", "content": primary, "_slot": "primary"})
        if goal.strip():
            messages.append({"role": "user", "content": goal, "_slot": "goal"})
        if working:
            messages.append({"role": "assistant", "content": working, "_slot": "text"})

        tool_cap = int(TOOL_RESULT_CHAR_CAP or 128_000)
        meta: dict[str, Any] = {}
        with suppress(Exception):
            messages = slim_messages_mid_turn(
                runtime,
                messages,
                session_id=session_id,
                tool_result_char_cap=tool_cap,
            )
            messages, meta = apply_auto_harness_send_policy(
                runtime,
                messages,
                user_text=goal,
                session_id=session_id,
                tool_result_char_cap=128_000,
            )

        out_text = working
        for m in reversed(messages):
            if isinstance(m, dict) and m.get("role") == "assistant":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    out_text = c
                break

        brief_block = ""
        with suppress(Exception):
            brief_block = brief_to_context_block(brief, max_chars=EPOCH_BRIEF_CHAR_CAP) or ""

    # Append-only: the primary system is returned byte-for-byte, followed by
    # exactly one brief block. A previous epoch's tail was stripped above.
    out_system = primary.rstrip()
    if brief_block:
        out_system = out_system + EPOCH_BRIEF_MARKER + brief_block.strip()

    return {
        "ok": True,
        "system": out_system,
        "text": out_text,
        "brief": brief_block,
        "meta": {
            "epoch": epoch,
            "total_steps": total_steps,
            "level": (meta or {}).get("level"),
            "est": (meta or {}).get("est"),
            "compress_count": int(getattr(brief, "compress_count", 0) or 0),
            "ledger_lines": len(ledger_lines),
        },
    }


def _gate_tracker(runtime: Any, session_id: str | None, goal: str) -> dict[str, Any]:
    """Per-(session, goal) re-arm counters so a nudge fires at most N per turn."""
    bag = getattr(runtime, "_rmdy_gate_tracker", None)
    if not isinstance(bag, dict):
        bag = {}
        with suppress(Exception):
            runtime._rmdy_gate_tracker = bag
    digest = hashlib.sha1(goal.strip().encode("utf-8", "ignore")).hexdigest()[:12]
    key = f"{session_id or '_anon'}|{digest}"
    entry = bag.get(key)
    if not isinstance(entry, dict):
        entry = {}
        bag[key] = entry
        if len(bag) > 64:
            for k in list(bag.keys())[: len(bag) - 48]:
                if k != key:
                    bag.pop(k, None)
    return entry


async def _should_continue_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    from remedy.core.build_engine import looks_like_build_request
    from remedy.core.react_policy import (
        FORCE_IMPLEMENT_NUDGE,
        UNFINISHED_WORK_NUDGE,
        VERIFY_ONCE_NUDGE,
        agency_rearm_nudge_message,
        agency_tool_promise_claim,
        failed_verify_nudge,
        looks_like_pseudo_tools,
        message_wants_tools,
        summarize_tool_evidence,
        text_claims_completion,
        turn_has_unfinished_work,
    )
    from remedy.core.turn_context import set_turn_last_user_text
    from remedy.memory.harness.send_policy import _ensure_session_brief

    goal = str(inp.get("goal") or inp.get("message") or "")
    text = str(inp.get("text") or "")
    session_id = str(inp.get("session_id") or "").strip() or None
    tool_count = int(inp.get("tool_count") or 0)
    chat_mode = bool(inp.get("chat_mode") or False)
    plan_mode = bool(inp.get("plan_mode") or False)
    has_tool_calls = bool(inp.get("has_tool_calls") or False)
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    last_results = inp.get("last_results")

    if chat_mode:
        return {"ok": True, "continue": False, "nudge": "", "reason": "chat_mode"}

    runtime = await get_cached_runtime(home_dir=home_dir, project_path=project_path)
    brief = _ensure_session_brief(runtime, session_id or "")
    evidence = summarize_tool_evidence(last_results)
    build_goal = bool(looks_like_build_request(goal))

    def _out(cont: bool, nudge: str, reason: str) -> dict[str, Any]:
        return {
            "ok": True,
            "continue": bool(cont),
            "nudge": nudge if cont else "",
            "reason": reason,
            "tool_count": tool_count,
            "evidence": {
                "mutates": evidence["mutates"],
                "verify_seen": evidence["verify_seen"],
                "verify_failed": evidence["verify_failed"],
            },
        }

    with _turn_scope(runtime, session_id, plan_mode=plan_mode, brief=brief):
        set_turn_last_user_text(goal, runtime)
        tracker = _gate_tracker(runtime, session_id, goal)
        st = None
        with suppress(Exception):
            st = _observe_build_evidence(
                runtime, goal=goal, session_id=session_id, evidence=evidence
            )

        # 1. Evidence beats prose: a verify-class tool failed in the last batch
        #    and the text still claims completion.
        if evidence["verify_failed"] and text_claims_completion(text):
            n = int(tracker.get("verify_failed", 0) or 0)
            if n < _GATE_VERIFY_FAILED_MAX:
                tracker["verify_failed"] = n + 1
                return _out(
                    True,
                    failed_verify_nudge(evidence["failed_name"], evidence["failed_exit"]),
                    "verify_failed",
                )

        # 2. Writes with no verify afterwards on a build goal: ask once per turn.
        batch_unverified = evidence["mutate_after_verify"] or (
            evidence["mutates"] > 0 and not evidence["verify_seen"]
        )
        state_unverified = bool(
            st is not None
            and int(getattr(st, "write_steps", 0) or 0) > 0
            and getattr(st, "last_verify_ok", None) is not True
        )
        if build_goal and not plan_mode and (batch_unverified or state_unverified):
            if not tracker.get("verify_once"):
                tracker["verify_once"] = True
                return _out(True, VERIFY_ONCE_NUDGE, "verify_once")

        # 3. Explore streak with nothing written on a build goal: once per turn.
        if st is not None and build_goal and not plan_mode:
            streak = int(getattr(st, "serial_explore_streak", 0) or 0)
            cap = int(getattr(st, "max_serial_explore", 3) or 3)
            writes = int(getattr(st, "write_steps", 0) or 0)
            emitted = list(getattr(st, "nudges_emitted", None) or [])
            if streak >= cap and writes == 0 and "force_implement" not in emitted:
                emitted.append("force_implement")
                with suppress(Exception):
                    st.nudges_emitted = emitted
                    st.phase = "implement"
                return _out(True, FORCE_IMPLEMENT_NUDGE, "force_implement")

        # 4. Narrated tool promise with zero tools and a goal that wants them.
        promise = (
            tool_count == 0
            and not has_tool_calls
            and not looks_like_pseudo_tools(text)
            and bool(message_wants_tools(goal))
            and bool(agency_tool_promise_claim(text))
        )
        if promise and not plan_mode:
            msg = agency_rearm_nudge_message()
            nudge = str(msg.get("content") or msg.get("text") or "") or (
                "Do not only say you will use tools. Call tools now via the "
                "function-calling API."
            )
            return _out(True, nudge, "agency_promise")

        # 5. Mission / brief open work (build phases are judged above from
        #    evidence, never from "phase != done").
        unfinished = bool(
            turn_has_unfinished_work(
                runtime,
                session_id=session_id,
                tools_enabled=not plan_mode,
                tool_steps_this_turn=tool_count,
                include_build=False,
            )
        )
        if unfinished:
            n = int(tracker.get("mission", 0) or 0)
            if n < _GATE_MISSION_MAX:
                tracker["mission"] = n + 1
                return _out(True, UNFINISHED_WORK_NUDGE, "unfinished")

        # 6. A work request that produced no tools at all this turn.
        if tool_count == 0 and not plan_mode and bool(message_wants_tools(goal)):
            if not tracker.get("wants_tools"):
                tracker["wants_tools"] = True
                return _out(True, UNFINISHED_WORK_NUDGE, "wants_tools")

    return _out(False, "", "done")


async def get_cached_runtime(
    *,
    home_dir: str | None = None,
    project_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> Any:
    """Cached BasicRuntime for other forever-Python RMDY tools (memory/skills)."""
    config = _agent_config(
        home_dir=home_dir,
        project_path=project_path,
        provider=provider,
        model=model,
        base_url=base_url,
    )
    return await _get_runtime(config)


def _run_coro(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except RuntimeError as exc:
        if "asyncio.run()" not in str(exc) and "running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


async def _memory_search_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    from remedy.memory.authority import RETRIEVAL_NOT_AUTHORITY
    from remedy.memory.partner_memory import search_partner_and_entries

    query = str(inp.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    if len(query) > 400:
        query = query[:400]
    raw_limit = inp.get("limit", 8)
    try:
        limit = int(raw_limit) if raw_limit is not None else 8
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(20, limit))
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    session_id = str(inp.get("session_id") or "").strip() or None

    runtime = await get_cached_runtime(home_dir=home_dir, project_path=project_path)
    memory = getattr(runtime, "memory", None)
    if memory is None:
        raise RuntimeError("memory store not available")
    with _turn_scope(runtime, session_id):
        merged = await search_partner_and_entries(
            memory,
            query,
            limit=limit,
            project_path=project_path
            or str(getattr(runtime, "_project_path", None) or "")
            or None,
        )
    hits: list[dict[str, Any]] = []
    for hit in merged or []:
        item: dict[str, Any] = {
            "kind": str(hit.get("kind") or "entry"),
            "title": str(hit.get("title") or ""),
            "content": str(hit.get("content") or "")[:400],
            "score": float(hit.get("score") or 0.0),
        }
        auth = str(hit.get("authority") or "").strip()
        if auth:
            item["authority"] = auth
        if "inferred" in hit:
            item["inferred"] = bool(hit.get("inferred"))
        why = str(hit.get("why") or "").strip()
        if why:
            item["why"] = why[:240]
        hits.append(item)
    return {
        "query": query,
        "hits": hits,
        "total": len(hits),
        "notice": RETRIEVAL_NOT_AUTHORITY,
    }


def search_memory(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler — Partner Memory + FTS search (context, not a grant)."""
    return _run_coro(_memory_search_async(inp))


def _current_user_text(runtime: Any, inp: Mapping[str, Any], session_id: str | None) -> str:
    """Owner text for this turn: RMDY input first, then what assemble saw."""
    raw = inp.get("user_text")
    if isinstance(raw, str) and raw.strip():
        return raw[:4000]
    sid = str(session_id or "").strip()
    if sid:
        cached = _session_user_text_store(runtime).get(sid)
        if cached:
            return cached
    with suppress(Exception):
        from remedy.core.turn_context import current_last_user_text

        return str(current_last_user_text(runtime) or "")
    return ""


async def _memory_save_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a memory note with the same guards as agent memory_save.

    Authority follows the owner's words, not the model's: a save is stamped
    ``owner`` only when the current user message asked to remember. Any other
    model-initiated save is ``agent`` / inferred and never forced past the
    stability heuristics.
    """
    from remedy.memory.authority import (
        looks_like_instruction_launder,
        may_write_parent_memory,
        stamp_entry_metadata,
    )
    from remedy.memory.partner_memory import (
        is_explicit_remember_intent,
        looks_like_secret,
        upsert_profile_fact,
    )
    from remedy.models import MemoryEntry, MemoryEntryType

    content = str(inp.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    if len(content) > 8_000:
        content = content[:8_000]
    title = str(inp.get("title") or "Remembered").strip() or "Remembered"
    category = str(inp.get("category") or "general").strip() or "general"
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    session_id = str(inp.get("session_id") or "").strip() or None

    if looks_like_secret(content):
        raise PermissionError(
            "content looks like a secret (API key/password); "
            "do not store credentials in Partner Memory"
        )
    if looks_like_instruction_launder(content):
        raise PermissionError(
            "content looks like an instruction trying to become standing memory; "
            "memory is context, not a grant"
        )

    runtime = await get_cached_runtime(home_dir=home_dir, project_path=project_path)
    memory = getattr(runtime, "memory", None)
    if memory is None:
        raise RuntimeError("memory store not available")

    parent_ok = may_write_parent_memory(session_id)
    with _turn_scope(runtime, session_id):
        user_text = _current_user_text(runtime, inp, session_id)
        explicit = bool(parent_ok and is_explicit_remember_intent(user_text))
        if not parent_ok:
            source, authority, inferred, force = "hive", "hive", False, False
            why = "hive session note (not parent Partner Memory)"
        elif explicit:
            source, authority, inferred, force = "explicit", "owner", False, True
            why = "you asked to remember"
        else:
            source, authority, inferred, force = "agent", "agent", True, False
            why = "Remedy noted this during work"
        meta = stamp_entry_metadata(
            {},
            source=source,
            session_id=session_id,
            inferred=inferred,
            why=why,
        )
        await memory.upsert(
            MemoryEntry(
                title=title[:120],
                content=content,
                entry_type=MemoryEntryType.NOTE,
                importance=0.75 if explicit else 0.55,
                session_id=session_id,
                metadata=meta,
            )
        )
        if parent_ok:
            with suppress(Exception):
                from remedy.memory.middleman import get_session_middleman

                get_session_middleman(str(session_id or "")).put(
                    content,
                    kind="fact",
                    session_id=str(session_id or ""),
                    body_cap=1_000,
                )
        fact_action = "skipped"
        if parent_ok and len(content) < 400:
            with suppress(Exception):
                profile = await memory.get_or_create_profile()
                _fact, fact_action = upsert_profile_fact(
                    profile,
                    content,
                    category=category,
                    confidence=0.9 if explicit else 0.7,
                    source=source,
                    force=force,
                    inferred=inferred,
                    authority=authority,
                    why=why,
                    session_id=session_id,
                )
                await memory.save_user_profile(profile)

    return {
        "saved": True,
        "title": title[:120],
        "parent_memory": bool(parent_ok),
        "authority": authority,
        "inferred": inferred,
        "profile_fact": fact_action,
        "why": why,
    }


def save_memory(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler — Partner Memory write with secret/launder guards."""
    return _run_coro(_memory_save_async(inp))


async def _ensure_skills(runtime: Any) -> Any:
    reg = getattr(runtime, "skills", None)
    if reg is None:
        raise RuntimeError("skill registry not available")
    if int(getattr(reg, "count", 0) or 0) <= 0:
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
        with suppress(Exception):
            reg.discover_defaults(home_dir=home)
    return reg


async def _skill_search_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    query = str(inp.get("query") or "").strip()
    raw_limit = inp.get("limit", 8)
    try:
        limit = int(raw_limit) if raw_limit is not None else 8
    except (TypeError, ValueError):
        limit = 8
    limit = max(1, min(20, limit))
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    session_id = str(inp.get("session_id") or "").strip() or None

    runtime = await get_cached_runtime(home_dir=home_dir, project_path=project_path)
    reg = await _ensure_skills(runtime)
    with _turn_scope(runtime, session_id):
        hint = ""
        with suppress(Exception):
            hint = str(runtime.effective_project_path() or "")
        ranked = reg.match_skills(query, limit=limit, workspace_hint=hint or None)
    skills: list[dict[str, Any]] = []
    for skill, score in ranked or []:
        m = skill.manifest
        status = m.status.value if hasattr(m.status, "value") else str(m.status)
        skills.append(
            {
                "name": str(m.name),
                "score": float(score),
                "status": str(status),
                "description": str(m.description or "")[:200],
            }
        )
    return {"query": query, "skills": skills, "total": len(skills)}


def search_skills(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler — rank skill packs for the current task."""
    return _run_coro(_skill_search_async(inp))


async def _skill_activate_async(inp: Mapping[str, Any]) -> dict[str, Any]:
    name = str(inp.get("name") or inp.get("skill") or "").strip()
    include_references = bool(inp.get("include_references") or False)
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    session_id = str(inp.get("session_id") or "").strip() or None

    bulk = name.lower().replace("_", " ").replace("-", " ")
    if bulk in (
        "all",
        "*",
        "every",
        "everything",
        "reload",
        "reload all",
        "rescan",
        "refresh",
        "all skills",
        "every skill",
    ) or bulk.startswith("all "):
        raise PermissionError(
            "refusing bulk skill.activate; load one pack per task "
            "(use skill.search then skill.activate with an exact name)"
        )
    if not name:
        raise ValueError("name is required")

    runtime = await get_cached_runtime(home_dir=home_dir, project_path=project_path)
    reg = await _ensure_skills(runtime)
    with _turn_scope(runtime, session_id):
        sk_obj = reg.get(name)
        if sk_obj is not None:
            meta_q = sk_obj.manifest.metadata or {}
            if meta_q.get("quarantine"):
                raise PermissionError(
                    f"skill '{name}' is quarantined; Trust it in the Skills panel first"
                )
            st = getattr(sk_obj.manifest.status, "value", str(sk_obj.manifest.status))
            if str(st).lower() in ("disabled", "archived", "deprecated"):
                raise PermissionError(f"skill '{name}' is {st} (not active)")

        body = reg.skill_body(name, include_references=bool(include_references))
        if body is None:
            hits = reg.match_skills(name, limit=5)
            hint = ", ".join(s.manifest.name for s, _ in hits) or "none"
            raise FileNotFoundError(f"skill not found: {name}; closest: {hint}")

        with suppress(Exception):
            reg.mark_activated(name)
        related: list[str] = []
        with suppress(Exception):
            related = list(reg.related_skills(name) or [])
    return {
        "name": name,
        "body": str(body),
        "related": related,
        "chars": len(str(body)),
    }


def activate_skill(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler — load one skill procedure body into the turn."""
    return _run_coro(_skill_activate_async(inp))
