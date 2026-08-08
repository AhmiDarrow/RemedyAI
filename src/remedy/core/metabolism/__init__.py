"""Remedy metabolism — silent partner OS organs (speed, accuracy, trust).

One voice. Local-first. Never blocks the hot path on local inference.
Submodules implement turn tiers, evidence ledger, decision currency,
machine map, shadow rehearsal, action IR, time crystal, governor,
critical verify, and portable identity export.
"""

from __future__ import annotations

from remedy.core.metabolism.tier import TurnTier, classify_turn_tier, tier_policy

__all__ = [
    "TurnTier",
    "classify_turn_tier",
    "tier_policy",
    "organism_pulse_block",
]


def __getattr__(name: str):
    if name == "organism_pulse_block":
        from remedy.core.metabolism.organism import organism_pulse_block

        return organism_pulse_block
    raise AttributeError(name)
