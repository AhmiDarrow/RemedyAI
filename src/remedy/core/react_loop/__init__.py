"""ReAct multi-epoch stream loop package.

Public API:
  * call_llm_stream — main agent LLM + tools loop
  * is_fatal_llm_api_error / _is_fatal_llm_api_error — hard-stop classifier
  * binding helpers — provider_bits, resolve_and_apply_tools, rearm_agency_tools
"""

from __future__ import annotations

from remedy.core.react_loop.binding import (
    provider_bits,
    rearm_agency_tools,
    resolve_and_apply_tools,
)
from remedy.core.react_loop.build_request import build_step_request_body
from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error,
)
from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error as _is_fatal_llm_api_error,
)
from remedy.core.react_loop.loop import call_llm_stream
from remedy.core.react_loop.recovery import (
    fatal_model_error_message,
    repeated_provider_error_message,
    soft_retry_notice,
)
from remedy.core.react_loop.stream_consume import consume_llm_http_response
from remedy.core.react_loop.tool_batch import (
    apply_build_engine_after_batch,
    inject_phase_nudge,
    record_tool_batch_stats,
)

__all__ = [
    "call_llm_stream",
    "is_fatal_llm_api_error",
    "_is_fatal_llm_api_error",
    "provider_bits",
    "resolve_and_apply_tools",
    "rearm_agency_tools",
    "fatal_model_error_message",
    "repeated_provider_error_message",
    "soft_retry_notice",
    "build_step_request_body",
    "consume_llm_http_response",
    "record_tool_batch_stats",
    "inject_phase_nudge",
    "apply_build_engine_after_batch",
]
