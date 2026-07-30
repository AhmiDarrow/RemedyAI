"""Turn Cost Compiler — L0–L3 spend tiers (heuristics first, never block)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class TurnTier(IntEnum):
    """Spend class for a user turn. Higher = more agency / cost allowed."""

    L0_INSTANT = 0  # no frontier / tool-only local
    L1_LEAN = 1  # frontier, minimal tools
    L2_AGENCY = 2  # full tools, ledger, mid-turn slim
    L3_DEEP = 3  # work-alone / partitionable / long mission


# High-confidence L0: answer without frontier model when tools/local suffice.
_L0_WHOAMI = re.compile(
    r"(?is)^\s*("
    r"/whoami|who am i|what do you know about me|"
    r"what('?s| is) my name|my (name|profile)"
    r")\s*[.?!]?\s*$"
)
_L0_MODEL = re.compile(
    r"(?is)^\s*("
    r"what (model|provider|llm) (am i|are we|is) (using|on|active)|"
    r"which (model|provider)|current (model|provider)|"
    r"what model is this"
    r")\s*[.?!]?\s*$"
)
_L0_SKILLS = re.compile(
    r"(?is)^\s*("
    r"(list |show |what )?(my |your |the )?(skills?|skill list)|"
    r"what skills? (do you|can you) (have|use)|"
    r"/skills?"
    r")\s*[.?!]?\s*$"
)
_L0_STATUS = re.compile(
    r"(?is)^\s*("
    r"(system |server )?status|are you (online|ready|there)|"
    r"health check|/status"
    r")\s*[.?!]?\s*$"
)
_L0_VERSION = re.compile(
    r"(?is)^\s*("
    r"(what('?s| is) )?(your |remedy )?version|"
    r"/version"
    r")\s*[.?!]?\s*$"
)

_L3_AUTONOMOUS = re.compile(
    r"(?is)\b("
    r"work alone|on your own|handle this on your own|"
    r"don'?t wait for me|do not wait for me|unattended|"
    r"fully autonomous|finish without me|take it from here|"
    r"i need to go|step away|be with my kids"
    r")\b"
)
_L3_PARTITION = re.compile(
    r"(?is)\b("
    r"in parallel|fan.?out|spread out|across (the )?(modules?|codebase|packages?)|"
    r"multiple (areas?|modules?|trees?|packages?|urls?|sites?)|"
    r"review all|whole (repo|codebase|project)|entire (repo|codebase)|"
    r"all tests|full suite|codebase.?wide|repo.?wide|"
    r"compare .+ (and|vs|versus) "
    r")\b"
)
_L2_AGENCY = re.compile(
    r"(?is)\b("
    r"implement|refactor|debug|fix (the |this |a )|"
    r"file_edit|file_read|create file|write (a |the )|"
    r"read (the |this |a |my )?(file|path|code|script|module|src)|"
    r"\bread\s+[\w./\\-]+\.(py|ts|tsx|js|md|json|toml|rs|go)\b|"
    r"bash|shell|pytest|npm run|cargo |"
    r"computer_|navigate|screenshot|click |"
    r"edit |open (the )?project|in (the )?repo|"
    r"list_dir|repo_search|mission_|spread_run|job_run"
    r")\b"
)
_L2_PATH = re.compile(
    r"(?:[A-Za-z]:\\|~/|\.\.?/|src/|desktop/|tests?/)[^\s]{2,}"
)
_COMPLEX = re.compile(
    r"(?is)\b("
    r"and then|after that|step by step|multi.?step|"
    r"plan (this|out)|break (this )?down"
    r")\b"
)


@dataclass(frozen=True)
class TierPolicy:
    """What the hot path is allowed to spend for this tier."""

    tier: TurnTier
    label: str
    allow_frontier: bool
    allow_tools: bool
    full_snapshot: bool  # phase-2 pack/scout/spread
    force_spread: bool
    record_ir: bool
    shadow_high_blast: bool
    allow_critical_verify: bool
    max_tool_result_chars: int
    system_note: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "tier": int(self.tier),
            "label": self.label,
            "allow_frontier": self.allow_frontier,
            "allow_tools": self.allow_tools,
            "full_snapshot": self.full_snapshot,
            "force_spread": self.force_spread,
            "record_ir": self.record_ir,
            "shadow_high_blast": self.shadow_high_blast,
            "allow_critical_verify": self.allow_critical_verify,
            "max_tool_result_chars": self.max_tool_result_chars,
        }


_POLICIES: dict[TurnTier, TierPolicy] = {
    TurnTier.L0_INSTANT: TierPolicy(
        tier=TurnTier.L0_INSTANT,
        label="L0_instant",
        allow_frontier=False,
        allow_tools=True,  # local tools only (PA / status)
        full_snapshot=False,
        force_spread=False,
        record_ir=False,
        shadow_high_blast=False,
        allow_critical_verify=False,
        max_tool_result_chars=4000,
        system_note="",
    ),
    TurnTier.L1_LEAN: TierPolicy(
        tier=TurnTier.L1_LEAN,
        label="L1_lean",
        allow_frontier=True,
        allow_tools=False,  # bias off; may still answer
        full_snapshot=False,
        force_spread=False,
        record_ir=False,
        shadow_high_blast=False,
        allow_critical_verify=False,
        max_tool_result_chars=2000,
        system_note=(
            "[Tier L1] Lean chat: answer from context. "
            "Tools only if the user clearly needs machine work."
        ),
    ),
    TurnTier.L2_AGENCY: TierPolicy(
        tier=TurnTier.L2_AGENCY,
        label="L2_agency",
        allow_frontier=True,
        allow_tools=True,
        full_snapshot=True,
        force_spread=False,
        record_ir=True,
        shadow_high_blast=True,
        allow_critical_verify=True,
        max_tool_result_chars=12_000,
        system_note=(
            "[Tier L2] Agency: prefer tools over monologue; "
            "batch independent reads; do not re-read known paths."
        ),
    ),
    TurnTier.L3_DEEP: TierPolicy(
        tier=TurnTier.L3_DEEP,
        label="L3_deep",
        allow_frontier=True,
        allow_tools=True,
        full_snapshot=True,
        force_spread=True,
        record_ir=True,
        shadow_high_blast=True,
        allow_critical_verify=True,
        max_tool_result_chars=12_000,
        system_note=(
            "[Tier L3] Deep / work-alone: finish end-to-end. "
            "When work partitions, use spread_run. Record progress; verify before done."
        ),
    ),
}


def tier_policy(tier: TurnTier | int | str) -> TierPolicy:
    if isinstance(tier, TurnTier):
        return _POLICIES[tier]
    if isinstance(tier, int):
        return _POLICIES.get(TurnTier(tier), _POLICIES[TurnTier.L1_LEAN])
    label = str(tier or "").strip().upper()
    for t, p in _POLICIES.items():
        if p.label.upper() == label or label == f"L{int(t)}":
            return p
    return _POLICIES[TurnTier.L1_LEAN]


def classify_turn_tier(
    user_text: str = "",
    *,
    intent: str = "chat",
    plan_mode: bool = False,
    has_attachments: bool = False,
    tools_enabled: bool = True,
    pure_action: bool = False,
    browse: bool = False,
) -> TurnTier:
    """Heuristic tier classification — deterministic, no network."""
    ut = (user_text or "").strip()
    low = ut.lower()
    intent_l = (intent or "chat").strip().lower()

    if has_attachments or pure_action or browse:
        return TurnTier.L2_AGENCY

    if _L3_AUTONOMOUS.search(ut) or intent_l == "autonomous":
        return TurnTier.L3_DEEP
    if _L3_PARTITION.search(ut):
        return TurnTier.L3_DEEP

    # L0 instant (only short, single-clause)
    if ut and len(ut) <= 120 and "\n" not in ut:
        if (
            _L0_WHOAMI.match(ut)
            or _L0_MODEL.match(ut)
            or _L0_SKILLS.match(ut)
            or _L0_STATUS.match(ut)
            or _L0_VERSION.match(ut)
        ):
            return TurnTier.L0_INSTANT

    if plan_mode:
        return TurnTier.L2_AGENCY if tools_enabled else TurnTier.L1_LEAN

    if intent_l in ("tool", "skill") or _L2_AGENCY.search(ut) or _L2_PATH.search(ut):
        if _COMPLEX.search(ut) and _L3_PARTITION.search(ut):
            return TurnTier.L3_DEEP
        return TurnTier.L2_AGENCY

    if intent_l in ("memory", "plan"):
        return TurnTier.L1_LEAN if intent_l == "memory" else TurnTier.L2_AGENCY

    if not tools_enabled:
        return TurnTier.L1_LEAN

    # Default chat
    if len(ut) > 400 or _COMPLEX.search(ut):
        return TurnTier.L2_AGENCY if any(
            w in low for w in ("code", "file", "project", "repo", "bug", "error")
        ) else TurnTier.L1_LEAN

    return TurnTier.L1_LEAN


def tier_system_block(tier: TurnTier | int) -> str:
    return tier_policy(tier).system_note
