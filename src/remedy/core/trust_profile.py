"""v0.37 trust profiles change autonomy inside policy, never owner checkpoints."""

from __future__ import annotations

from enum import StrEnum

from remedy.core.approvals import SENSITIVE_PREFIX, sensitive_computer_checkpoint


class TrustProfile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AUTONOMOUS = "autonomous"


def normalize_trust_profile(raw: object | None) -> TrustProfile:
    """Parse config/UI value; invalid or missing → BALANCED."""
    try:
        return TrustProfile(str(raw or "").strip().lower())
    except ValueError:
        return TrustProfile.BALANCED


def profile_skips_high_impact_ask(profile: TrustProfile) -> bool:
    """Autonomous ≈ today's auto mode for in-project work. Checkpoints still ask."""
    return profile == TrustProfile.AUTONOMOUS


def profile_forces_high_impact_ask(profile: TrustProfile) -> bool:
    """Conservative still asks for high-impact in Auto. Full stays Full."""
    return profile == TrustProfile.CONSERVATIVE


def checkpoint_still_required(tool_name: str, command: str) -> str | None:
    """Money / send / mail cannot be waived by any profile, including AUTONOMOUS."""
    hit = sensitive_computer_checkpoint(tool_name, command)
    if hit:
        return hit
    if (tool_name or "").strip() in ("mail_send", "mail_reply"):
        return f"{SENSITIVE_PREFIX} — sending mail always needs your go-ahead"
    return None
