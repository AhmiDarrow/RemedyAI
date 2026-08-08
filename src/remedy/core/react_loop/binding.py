"""Per-turn LLM binding helpers for the ReAct stream loop."""

from __future__ import annotations

from typing import Any

from remedy.core.llm_binding import get_llm_binding
from remedy.core.react_turn import (
    apply_tools_decision,
    resolve_tools,
)


def provider_bits(runtime: Any) -> tuple[str, str, str]:
    """Return (provider, model, base_url) for the active binding."""
    try:
        b = get_llm_binding(runtime)
        return (
            str(b.provider or ""),
            str(b.model or ""),
            str(getattr(b, "base_url", None) or ""),
        )
    except Exception:
        return ("", "", "")


def resolve_and_apply_tools(
    *,
    runtime: Any,
    turn: Any,
    message: str,
    plan_mode: bool,
    history: list[Any],
    pure_action_kick: bool,
    clear_goals_only: bool,
    browse_pre_url: str | None,
    page_interaction: bool,
    open_only_browse: bool,
    build_state: Any,
    open_tasks_for_wall: Any,
    step_index: int = 0,
) -> tuple[Any, bool]:
    """Single tool-arming path. Returns (tools, run_until_done)."""
    prov, mod, url = provider_bits(runtime)
    decision = resolve_tools(
        message=message or "",
        all_tools=turn.all_tools,
        plan_mode=plan_mode,
        turn_tier=int(getattr(runtime, "_turn_tier", 1) or 1),
        open_tasks=open_tasks_for_wall or None,
        history=history,
        pure_action_kick=bool(pure_action_kick),
        clear_goals_only=bool(clear_goals_only),
        browse_pre_url=browse_pre_url,
        page_interaction=bool(page_interaction),
        open_only_browse=bool(open_only_browse),
        build_active=bool(
            build_state is not None and getattr(build_state, "active", False)
        ),
        step_index=step_index,
        provider=prov,
        model=mod,
        base_url=url,
        writes_done=turn.write_batches,
    )
    apply_tools_decision(turn, decision)
    return turn.tools, turn.run_until_done


def rearm_agency_tools(turn: Any) -> tuple[Any, bool]:
    """Re-enable tool schemas *and* long-task epoch policy."""
    turn.rearm(reason="rearm_agency")
    return turn.tools, turn.run_until_done
