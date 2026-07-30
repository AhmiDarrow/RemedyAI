"""Partner State Machine — subgoals, tool txns, epistemic graph, prospective memory.

Remedy's agentic continuity layer above the Memory Harness send-view:

  Phase A  Subgoal-scoped working set (collapse only closed spans)
  Phase B  Tool transaction ledger + write set + tool_recall
  Phase C  Epistemic graph (Session Brief is a projection)
  Phase D  Prospective memory + dual-stream inject (partner vs project)
  Phase E  Continuity Core (async local maintenance)

Stored transcript is never rewritten; this is operational state for agency.
"""

from __future__ import annotations

from remedy.memory.partner_state.state import (
    PartnerState,
    ensure_partner_state,
    partner_context_blocks,
    record_tool_from_runtime,
)

__all__ = [
    "PartnerState",
    "ensure_partner_state",
    "partner_context_blocks",
    "record_tool_from_runtime",
]
