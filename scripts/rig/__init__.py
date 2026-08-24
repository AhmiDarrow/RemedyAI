"""rig — a harness that drives Remedy so models can be tested against it.

Boots a disposable Remedy (its own ``REMEDY_HOME``, its own workspace, its own
port), walks a graded ladder of scenarios through the real streaming API, and
scores each model on whether it can *operate* the product — emit tool calls,
edit files, run what it wrote, recover from a traceback, and stop cleanly.

With ``REMEDY_LLM_TRACE_DIR`` set (the default), every run also records the
exact request bodies the provider saw, so a strong model's run doubles as
fine-tuning data for a small one.
"""

from __future__ import annotations

from .client import RemedyClient, ToolCall, Turn
from .llama import LlamaServer, find_llama_server, has_cuda, launch
from .runner import run_suite
from .sandbox import Sandbox, make_sandbox
from .scenarios import SUITES, Scenario, get_suite
from .score import Outcome, RunReport, compare, grade

__all__ = [
    "LlamaServer",
    "Outcome",
    "RemedyClient",
    "RunReport",
    "SUITES",
    "Sandbox",
    "Scenario",
    "ToolCall",
    "Turn",
    "compare",
    "find_llama_server",
    "get_suite",
    "grade",
    "has_cuda",
    "launch",
    "make_sandbox",
    "run_suite",
]
