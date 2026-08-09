"""Compatibility shim — prefer ``remedy.core.react_loop``.

Historically ``agent_react_loop`` held the full ReAct stream. Implementation
now lives under ``react_loop/``; this module re-exports the public surface.
"""

from __future__ import annotations

from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error,
)
from remedy.core.react_loop.errors import (
    is_fatal_llm_api_error as _is_fatal_llm_api_error,
)
from remedy.core.react_loop.loop import call_llm_stream

__all__ = [
    "call_llm_stream",
    "is_fatal_llm_api_error",
    "_is_fatal_llm_api_error",
]
