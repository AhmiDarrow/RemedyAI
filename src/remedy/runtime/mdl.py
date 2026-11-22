"""Machine Density Language: density-gated model depth for local inference.

Routes Remedy helper tasks to the appropriate model depth tier based on
task information density. Lighter tiers use fewer layers (lower VRAM,
faster). Speculative: try LIGHT first; escalate if confidence is low.

Tiers
-----
LIGHT  (4 layers)  — continuity core, low-density briefs
MEDIUM (16 layers) — classify, standard brief
FULL   (32 layers) — vision decode, high-density tasks

The density scorer (~1 MB) is always resident and maps task type +
prompt characteristics to a tier recommendation.

Integration
-----------
All tiers share the single unified mind llama-server on DEFAULT_PORT
(8740). Parallel slots, not extra processes, handle concurrent inner
tasks. The n_layers figures are documentary (Qwen3.5-4B has 32 layers).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Tier configuration
# Qwen3.5-4B: 32 layers total. One host, not per-tier processes.

@dataclass(frozen=True)
class MdlTier:
    name: str
    n_layers: int
    port: int
    vram_mb_est: int
    resident: bool
    description: str


# Unified mind host (8740). Tier names remain for routing API compatibility.
from remedy.runtime.catalog import DEFAULT_PORT as _MIND_PORT

MDL_TIERS: dict[str, MdlTier] = {
    "light": MdlTier(
        name="light",
        n_layers=32,
        port=_MIND_PORT,
        vram_mb_est=4200,
        resident=True,
        description="Short inner tasks on the unified 4B mind (shared host)",
    ),
    "medium": MdlTier(
        name="medium",
        n_layers=32,
        port=_MIND_PORT,
        vram_mb_est=4200,
        resident=True,
        description="Standard briefs and classify on the unified 4B mind",
    ),
    "full": MdlTier(
        name="full",
        n_layers=32,
        port=_MIND_PORT,
        vram_mb_est=4200,
        resident=True,
        description="Vision, digest, and full-quality local mind — single host",
    ),
}

DEFAULT_TIER = "medium"

# Task-to-tier routing (pre-scorer, no model needed)
TASK_TIER_MAP: dict[str, str] = {
    "nano_classify": "speculative",  # try LIGHT first, escalate if needed
    "continuity_core": "light",
    "brief_update": "medium",
    "vision_decode": "full",
    "spread_plan": "medium",
    "worker_summarize": "medium",
    "library_rerank": "medium",
    "text": "medium",
}

DENSITY_SCORE = {
    "nano_classify": 0.90,  # high density → high quality needed
    "continuity_core": 0.45,  # low density → light tier OK
    "brief_update": 0.55,
    "vision_decode": 0.95,
    "spread_plan": 0.50,
    "worker_summarize": 0.50,
    "library_rerank": 0.65,
    "text": 0.50,
}


@dataclass
class MdlRouting:
    """Result of MDL routing decision."""

    tier: str
    base_url: str
    port: int
    n_layers: int
    is_speculative: bool = False  # if True, caller should validate output
    escalate_tier: str | None = None  # if speculative fails, upgrade to this tier


def route_task(task_kind: str, prompt: str = "") -> MdlRouting:
    """Route a local task to the appropriate model depth tier.

    Returns the best tier for the given task. For speculative routing
    (classify), returns LIGHT tier with MEDIUM escalation path.
    """
    tier_name = TASK_TIER_MAP.get(task_kind, DEFAULT_TIER)

    if tier_name == "speculative":
        light = MDL_TIERS["light"]
        MDL_TIERS["medium"]
        return MdlRouting(
            tier="light",
            base_url=f"http://127.0.0.1:{light.port}/v1",
            port=light.port,
            n_layers=light.n_layers,
            is_speculative=True,
            escalate_tier="medium",
        )

    tier = MDL_TIERS.get(tier_name, MDL_TIERS[DEFAULT_TIER])
    return MdlRouting(
        tier=tier.name,
        base_url=f"http://127.0.0.1:{tier.port}/v1",
        port=tier.port,
        n_layers=tier.n_layers,
        is_speculative=False,
    )


def escalate_routing(current: MdlRouting) -> MdlRouting:
    """Return the next-higher tier for escalation."""
    if current.escalate_tier:
        next_tier_name = current.escalate_tier
    elif current.tier == "light":
        next_tier_name = "medium"
    elif current.tier == "medium":
        next_tier_name = "full"
    else:
        return current  # already full, can't escalate

    tier = MDL_TIERS[next_tier_name]
    return MdlRouting(
        tier=tier.name,
        base_url=f"http://127.0.0.1:{tier.port}/v1",
        port=tier.port,
        n_layers=tier.n_layers,
        is_speculative=False,
    )


def tier_for_quality(density: float) -> str:
    """Map a density score [0,1] to the appropriate tier."""
    if density < 0.40:
        return "light"
    elif density < 0.70:
        return "medium"
    else:
        return "full"


def get_tier_base_url(tier_name: str) -> str:
    """Get the base URL for a tier."""
    tier = MDL_TIERS.get(tier_name)
    if tier is None:
        tier = MDL_TIERS[DEFAULT_TIER]
    return f"http://127.0.0.1:{tier.port}/v1"


def list_tiers() -> list[dict[str, Any]]:
    """Return all tier configurations for status display."""
    return [
        {
            "name": t.name,
            "n_layers": t.n_layers,
            "port": t.port,
            "vram_mb_est": t.vram_mb_est,
            "resident": t.resident,
            "description": t.description,
        }
        for t in MDL_TIERS.values()
    ]
