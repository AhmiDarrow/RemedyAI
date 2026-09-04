"""Assemble system/soul/skills/memory context for Go cognition turns.

Called over RMDY as ``prompt.assemble`` from ``CognitionTurnRunner`` before the
model sees the turn. Reuses the forever-Python prompt modules (system prompt,
``build_turn_context``, ``build_runtime_system_block``) so production Go turns
are equal-or-better than raw prompt-only.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

logger = logging.getLogger("remedy.runtime.prompt_assemble")

_runtime_cache: dict[str, Any] = {}


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

    runtime = BasicRuntime(config)
    with suppress(Exception):
        await runtime.memory.initialize()
    _runtime_cache[key] = runtime
    return runtime


def _rebuild_system_prompt(runtime: Any) -> str:
    from remedy.core.react_policy import build_system_prompt

    cfg = runtime.config
    prompt = build_system_prompt(
        getattr(cfg, "persona", None),
        name=getattr(cfg, "name", None),
        gender=getattr(cfg, "agent_gender", None),
        ui_language=getattr(cfg, "ui_language", None),
    )
    runtime._system_prompt = prompt
    return prompt


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
    from remedy.core.turn_context import (
        begin_turn,
        end_turn,
        set_turn_last_user_text,
    )

    message = str(inp.get("message") or inp.get("prompt") or "")
    session_id = str(inp.get("session_id") or "").strip() or None
    plan_mode = bool(inp.get("plan_mode") or False)
    chat_mode = bool(inp.get("chat_mode") or False)
    home_dir = str(inp.get("home_dir") or "").strip() or None
    project_path = str(inp.get("project_path") or "").strip() or None
    provider = str(inp.get("provider") or "").strip() or None
    model = str(inp.get("model") or "").strip() or None
    base_url = str(inp.get("base_url") or "").strip() or None

    config = _agent_config(
        home_dir=home_dir,
        project_path=project_path,
        provider=provider,
        model=model,
        base_url=base_url,
    )
    runtime = await _get_runtime(config)
    system_prompt = _rebuild_system_prompt(runtime)

    if project_path:
        with suppress(Exception):
            runtime._project_path_raw = project_path
            from remedy.core.workspace import resolve_project_path

            runtime._active_project_path = resolve_project_path(project_path)

    bind = LlmBinding(
        provider=str(getattr(runtime, "_llm_provider", "") or "openai"),
        model=str(getattr(runtime, "_llm_model", "") or ""),
        base_url=str(getattr(runtime, "_llm_base_url", "") or ""),
        api_key=str(getattr(runtime, "_llm_api_key", "") or ""),
    )
    llm_tok = set_llm_binding(bind)
    turn_tokens = begin_turn(
        session_id,
        project_raw=getattr(runtime, "_project_path_raw", None),
        active_path=str(getattr(runtime, "_active_project_path", "") or "") or None,
        plan_mode=bool(plan_mode) and not bool(chat_mode),
        chat_mode=bool(chat_mode),
    )
    runtime._plan_mode = bool(plan_mode) and not bool(chat_mode)
    runtime._chat_mode = bool(chat_mode)
    try:
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
        return {
            "system": system,
            "goal": message,
            "context_chars": len(context or ""),
            "system_chars": len(system),
        }
    finally:
        end_turn(turn_tokens)
        reset_llm_binding(llm_tok)
        runtime._plan_mode = False
        runtime._chat_mode = False


def assemble_prompt(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Sync RMDY handler entry — builds system + goal for one cognition turn."""
    try:
        return asyncio.run(_assemble_async(inp))
    except RuntimeError as exc:
        # Nested event loop (rare in worker) — fall back to a fresh loop.
        if "asyncio.run()" not in str(exc) and "running event loop" not in str(exc):
            raise
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_assemble_async(inp))
        finally:
            loop.close()
