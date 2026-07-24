"""Remedy Memory Harness — elegant context compression for long sessions.

Three layers:
  L0 mechanical prune of the model *send-view* (never rewrites stored chat)
  L1 model-guided compress (compress_context tool + thresholds)
  L2 Session Brief — anchored structured working state

Product name: Memory Harness (not third-party branding).
"""

from __future__ import annotations

from remedy.memory.harness.brief import SessionBrief, brief_to_context_block
from remedy.memory.harness.compressor import (
    estimate_tokens,
    heuristic_merge_from_history,
    should_nudge_compress,
)
from remedy.memory.harness.pruner import prune_messages_for_send

__all__ = [
    "SessionBrief",
    "brief_to_context_block",
    "estimate_tokens",
    "heuristic_merge_from_history",
    "prune_messages_for_send",
    "should_nudge_compress",
]
