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
            )

    headers = adapter.auth_headers(bind.api_key)
    endpoint = adapter.chat_endpoint(bind.base_url)
    use_openai_sse = bool(getattr(adapter, "uses_openai_sse", True))

    think = getattr(runtime, "_thinking_level", "high")
    with suppress(Exception):
        from remedy.core.local_agent_optimize import is_local_binding

        if is_local_binding(bind.provider, bind.model, bind.base_url):
            think = "low"

    body = adapter.build_body(
        model=bind.model,
        messages=messages,
        tools=tools,
        stream=use_openai_sse,
        thinking_level=think,
    )
    with suppress(Exception):
        from remedy.core.local_agent_optimize import apply_local_body_optimize

        body = apply_local_body_optimize(
            body if isinstance(body, dict) else {},
            provider=bind.provider,
            model=bind.model,
            base_url=bind.base_url,
            user_message=str(user_message or ""),
            step_index=int(step),
        )

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
    return body, headers, endpoint, use_openai_sse
