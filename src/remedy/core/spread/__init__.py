"""Silent fan-out workers — cover more ground without multi-agent theater.

Heuristics (and optional local Qwen) decide *when* to spread; workers are mostly
deterministic jobs (explore/search/diff/verify) that return digests to the parent.
"""

from __future__ import annotations

from remedy.core.spread.planner import SpreadPlan, plan_spread
from remedy.core.spread.runner import run_spread
from remedy.core.spread.types import SpreadResult, SpreadTask, WorkerResult

__all__ = [
    "SpreadPlan",
    "SpreadResult",
    "SpreadTask",
    "WorkerResult",
    "plan_spread",
    "run_spread",
]
