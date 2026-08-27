"""Build and sanitize chat completion request bodies for one ReAct step."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from remedy.core.provider_sanitize import sanitize_chat_body

logger = logging.getLogger(__name__)


def build_step_request_body(
    *,
    runtime: Any,
    bind: Any,
    adapter: Any,
    messages: list[dict[str, Any]],
    step_tools: list[Any] | None,
    step: int,
    user_message: str,
) -> tuple[dict[str, Any], dict[str, str], str, bool]:
    """Return (body, headers, endpoint, use_openai_sse).

    Applies local optimizers + fail-closed sanitization.
    """
    with suppress(Exception):
        adapter._local_step_index = int(step)
        runtime._local_step_index = int(step)

    tools = step_tools
    with suppress(Exception):
        from remedy.core.local_agent_optimize import (
            filter_tools_write_first,
            is_local_binding,
        )

        if (
            is_local_binding(bind.provider, bind.model, bind.base_url)
            and tools
        ):
            tools = filter_tools_write_first(
                tools,
                user_message=str(user_message or ""),
                step_index=int(step),
                history=messages if isinstance(messages, list) else None,
            )

    headers = adapter.auth_headers(bind.api_key)
    endpoint = adapter.chat_endpoint(bind.base_url)
    # Optional Sleev gateway: same keys/models, compressed context upstream.
    with suppress(Exception):
        from remedy.core.sleev import prepare_llm_http

        endpoint, headers = prepare_llm_http(
            provider=bind.provider,
            base_url=bind.base_url,
            api_key=bind.api_key,
            adapter=adapter,
            runtime=runtime,
        )
    use_openai_sse = bool(getattr(adapter, "uses_openai_sse", True))

    # Local/RMB: always low thinking — UI "Think High" burns the budget on
    # monologue and starves tool JSON (partner build failure mode).
    from remedy.core.turn_context import set_turn_thinking_level, turn_thinking_level

    think = turn_thinking_level(runtime)
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        if is_local_binding(bind.provider, bind.model, bind.base_url):
            think = "low"
            set_turn_thinking_level("low")

    # Local + xAI/Grok: never stream tool rounds. SSE + long thinking is
    # the WinError 64 / ClientPayloadError class. Final answers (no tools)
    # still stream. Other clouds keep SSE (their fakes/tests depend on it).
    _stream = use_openai_sse
    _prov = str(getattr(bind, "provider", "") or "").lower()
    _model = str(getattr(bind, "model", "") or "").lower()
    if tools and (_prov in {"xai", "grok"} or "grok" in _model):
        _stream = False
        use_openai_sse = False
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        if is_local_binding(bind.provider, bind.model, bind.base_url):
            _stream = False
            use_openai_sse = False

    # Right-size the completion budget (reasoning is never truncated
    # mid-stream): prefer the runtime's computed provider cap when set.
    _mt_override = getattr(runtime, "_llm_max_output_tokens", None)
    body = adapter.build_body(
        model=bind.model,
        messages=messages,
        tools=tools,
        stream=_stream,
        thinking_level=think,
        max_tokens=(int(_mt_override) if _mt_override else None),
    )
    with suppress(Exception):
        from remedy.core.local_agent_optimize import apply_local_body_optimize

        body = body if isinstance(body, dict) else {}
        with suppress(Exception):
            from remedy.core.turn_context import turn_write_budget

            sticky = int(turn_write_budget(runtime) or 0)
            if sticky > 0:
                body = dict(body)
                body["_remedy_write_budget"] = sticky
        body = apply_local_body_optimize(
            body,
            provider=bind.provider,
            model=bind.model,
            base_url=bind.base_url,
            user_message=str(user_message or ""),
            step_index=int(step),
            history=messages if isinstance(messages, list) else None,
        )
        # Monologue-loop breaker flag from ReAct (turn-local — not runtime singleton)
        from remedy.core.turn_context import (
            turn_force_tool_choice,
            turn_tool_choice_required_blocked,
        )

        if turn_force_tool_choice(runtime) and isinstance(body, dict):
            if body.get("tools"):
                body["temperature"] = 0.05
                # DeepSeek thinking models 400 on tool_choice=required
                # ("Thinking mode does not support this tool_choice").
                # Keep tools + auto; unfinished-work drive still refuses
                # zero-tool finals. Other hosts can take required.
                _prov = str(getattr(bind, "provider", "") or "").lower()
                _required_blocked = bool(
                    turn_tool_choice_required_blocked(runtime)
                )
                if _prov == "deepseek" or _required_blocked:
                    body["tool_choice"] = "auto"
                else:
                    body["tool_choice"] = "required"
                with suppress(Exception):
                    from remedy.core.local_agent_optimize import is_local_binding

                    if is_local_binding(bind.provider, bind.model, bind.base_url):
                        body["stream"] = False

    try:
        local_agent = False
        with suppress(Exception):
            from remedy.runtime.rmb.mode import is_rmb_provider

            local_agent = is_rmb_provider(
                bind.provider, getattr(bind, "base_url", None)
            ) or str(bind.provider or "").lower() in (
                "ollama",
                "llamacpp",
            )
        body = sanitize_chat_body(
            body if isinstance(body, dict) else {},
            local_agent=local_agent,
        )
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

    if not isinstance(body, dict):
        body = {}

    # Opt-in trace of the exact body the provider receives (REMEDY_LLM_TRACE_DIR).
    # Used by scripts/rig to score local models and to harvest teacher
    # trajectories for fine-tuning. No-op in normal runs.
    with suppress(Exception):
        from remedy.core.llm_trace import record_request

        record_request(
            body,
            provider=str(bind.provider or ""),
            model=str(bind.model or ""),
            step=int(step),
            session_id=str(getattr(runtime, "_session_id", "") or ""),
        )

    return body, headers, endpoint, use_openai_sse
