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
Uses multiple llama-server instances (or tiered GGUF files) on different
ports. Each tier loads only its required layer range from the shared
model file (mmap sharing keeps physical RAM low).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Tier configuration
# SmolVLM2-2.2B: 32 layers total
# Q4_K_M: ~30.9 MB per layer (attention + FFN + norms)

@dataclass(frozen=True)
class MdlTier:
    name: str
    n_layers: int
    port: int
    vram_mb_est: int
    resident: bool
    description: str


# SmolVLM2-2.2B: 24 layers total (~1061 MB Q4_K_M)
# Single model file with varied -ngl for GPU layer count
# Port defaults (8740 = legacy single-server = FULL tier).
MDL_TIERS: dict[str, MdlTier] = {
    "light": MdlTier(
        name="light",
        n_layers=4,
        port=8741,
        vram_mb_est=300,
        resident=True,
        description="Continuity core, low-density briefs, speculative classify",
    ),
    "medium": MdlTier(
        name="medium",
        n_layers=16,
        port=8742,
        vram_mb_est=670,
        resident=False,
        description="Classify final pass, standard brief, continuity",
    ),
    "full": MdlTier(
        name="full",
        n_layers=24,
        port=8740,
        vram_mb_est=1060,
        resident=False,
        description="Vision decode, full-quality local chat — same port as legacy server",
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
