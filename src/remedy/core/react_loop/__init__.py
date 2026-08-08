"""ReAct multi-epoch stream loop package.

Public API:
  * call_llm_stream — main agent LLM + tools loop
  * is_fatal_llm_api_error / _is_fatal_llm_api_error — hard-stop classifier
"""

from __future__ import annotations

from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error,
    is_fatal_llm_api_error as _is_fatal_llm_api_error,
)
from remedy.core.react_loop.loop import call_llm_stream

__all__ = [
    "call_llm_stream",
    "is_fatal_llm_api_error",
    "_is_fatal_llm_api_error",
]
