"""Remedy Memory Harness — elegant context compression for long sessions.

Layers:
  L0 mechanical prune of the model *send-view* (never rewrites stored chat)
  L1 thresholds + optional local/background brief update
  L2 Session Brief — anchored structured working state (+ cumulative history)

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
from remedy.memory.harness.send_policy import (
    apply_auto_harness_send_policy,
    slim_messages_mid_turn,
)

__all__ = [
    "SessionBrief",
    "apply_auto_harness_send_policy",
    "brief_to_context_block",
    "estimate_tokens",
    "heuristic_merge_from_history",
    "prune_messages_for_send",
    "should_nudge_compress",
    "slim_messages_mid_turn",
]
