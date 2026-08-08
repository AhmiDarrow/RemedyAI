"""Muscle profile — how strong the current provider/model is as *compute*.

The Soul Field is personhood. This module is pure capability sensing so a
Grok / Claude / GPT-class muscle unlocks full builder agency (parallel tools,
long-horizon coding contract, spread bias) while small/local muscle stays lean.
"""

from __future__ import annotations

import re
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

# Capability tiers: 0=tiny local, 1=small, 2=mid, 3=frontier builder
TIER_TINY = 0
TIER_SMALL = 1
TIER_MID = 2
TIER_FRONTIER = 3


@dataclass(frozen=True, slots=True)
class MuscleProfile:
    """Resolved capability for this turn's provider+model."""

    tier: int
    label: str  # frontier | mid | small | tiny
    provider: str
    model: str
    max_parallel_tools: int
    prefer_spread: bool
    long_horizon: bool
    builder_contract: bool
    dense_memory: bool

    @property
    def is_frontier(self) -> bool:
        return self.tier >= TIER_FRONTIER

    @property
    def is_capable(self) -> bool:
        """True when the muscle can drive serious multi-step builds."""
        return self.tier >= TIER_MID


_FRONTIER_MODEL = re.compile(
    r"(?i)("
    r"grok[-_ ]?([234]|4|code|heavy|fast)|"
    r"claude[-_ ]?(opus|sonnet|4|3\.5|3\.7)|"
    r"gpt[-_ ]?([45]|4o|4\.1|4\.5|o[134]|5)|"
    r"\bo[134](?:-mini|-pro)?\b|"
    r"gemini[-_ ]?(2|1\.5|pro|ultra|flash-2)|"
    r"deepseek[-_ ]?(r1|v3|chat|coder|reasoner)|"
    r"mistral[-_ ]?(large|medium|codestral)|"
    r"qwen.*72b|qwen2\.5[-_ ]?72|qwen3|"
    r"llama[-_ ]?3\.1[-_ ]?70|llama[-_ ]?4|"
    r"command[-_ ]?r"
    r")"
)

_MID_MODEL = re.compile(
    r"(?i)("
    r"gpt[-_ ]?3\.5|claude[-_ ]?haiku|gemini[-_ ]?flash|"
    r"deepseek[-_ ]?lite|mistral[-_ ]?small|"
    r"qwen.*32b|qwen2\.5[-_ ]?32|llama[-_ ]?3\.1[-_ ]?8|"
    r"grok[-_ ]?beta"
    r")"
)

_TINY_MODEL = re.compile(
    r"(?i)("
    r"(?:^|[-_./])(?:1b|2b|3b|7b|0\.5b|1\.5b)(?:$|[-_./])|"
    r"tiny|nano|phi-3|gemma-2b|smollm"
    r")"
)

_STRONG_PROVIDERS = frozenset(
    {
        "xai",
        "anthropic",
        "openai",
        "google",
        "gemini",
        "deepseek",
        "mistral",
        "openrouter",
        "groq",
    }
)


def classify_muscle(
    provider: str = "",
    model: str = "",
    *,
    base_url: str = "",
) -> MuscleProfile:
    """Classify provider/model into a muscle profile (no network)."""
    p = (provider or "").strip().lower()
    m = (model or "").strip()
    m_l = m.lower()
    url = (base_url or "").strip().lower()
    is_local = p in ("ollama", "llamacpp", "rmb", "local") or "11434" in url

    if _TINY_MODEL.search(m_l):
        tier = TIER_TINY if is_local else TIER_SMALL
    elif _FRONTIER_MODEL.search(m_l):
        tier = TIER_FRONTIER
    elif _MID_MODEL.search(m_l):
        tier = TIER_MID
    elif p in ("xai", "anthropic", "openai"):
        # Desktop defaults: strong hosts even if model string is empty/custom
        tier = TIER_FRONTIER
    elif p in ("google", "gemini", "deepseek", "mistral"):
        tier = TIER_FRONTIER if m_l else TIER_MID
    elif p == "openrouter":
        tier = TIER_MID  # unknown routed model
    elif is_local:
        tier = TIER_SMALL if m_l else TIER_TINY
    elif p in _STRONG_PROVIDERS:
        tier = TIER_MID
    else:
        tier = TIER_SMALL if m_l else TIER_TINY

    labels = {0: "tiny", 1: "small", 2: "mid", 3: "frontier"}
    if tier >= TIER_FRONTIER:
        parallel = 24
    elif tier >= TIER_MID:
        parallel = 16
    elif tier >= TIER_SMALL:
        parallel = 8
    else:
        parallel = 4

    return MuscleProfile(
        tier=tier,
        label=labels.get(tier, "small"),
        provider=p,
        model=m,
        max_parallel_tools=parallel,
        prefer_spread=tier >= TIER_MID,
        long_horizon=tier >= TIER_MID,
        builder_contract=tier >= TIER_MID,
        dense_memory=tier >= TIER_MID,
    )


def muscle_from_runtime(runtime: Any = None) -> MuscleProfile:
    """Resolve muscle for the current turn (ContextVar binding preferred)."""
    provider = model = base_url = ""
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding

        b = get_llm_binding(runtime)
        provider = str(b.provider or "")
        model = str(b.model or "")
        base_url = str(b.base_url or "")
    if runtime is not None and not (provider or model):
        provider = str(getattr(runtime, "_llm_provider", "") or "")
        model = str(getattr(runtime, "_llm_model", "") or "")
        base_url = str(getattr(runtime, "_llm_base_url", "") or "")
    return classify_muscle(provider, model, base_url=base_url)


def builder_system_addendum(profile: MuscleProfile) -> str:
    """Extra system block when muscle is capable of multi-step construction."""
    if not profile.builder_contract:
        return ""
    spread = (
        " Prefer **spread_run** when ≥2 independent modules/areas can be surveyed "
        "in parallel."
        if profile.prefer_spread
        else ""
    )
    parallel = profile.max_parallel_tools
    return (
        f"[Builder muscle — {profile.label} · {profile.provider or 'provider'}"
        f"{(' / ' + profile.model) if profile.model else ''} · "
        f"parallel≤{parallel}]\n"
        "You are compute for a **machine-native build engine**. Schedule:\n"
        "  SCOUT (batch 4–12 reads) → IMPLEMENT (file_write/file_edit) → "
        "VERIFY (bash_exec / job_run verify / mission_verify) → REPAIR → DONE.\n"
        "Hard rules: no monologue without tool_calls; no one-file-per-step explore; "
        "no claim shipped without a green verify signal; recover on Error with "
        "path:line edits. "
        "Use subgoal_open / mission_start for multi-phase unattended work. "
        "Soul Field is identity; tools are how you build."
        f"{spread} "
        "Run until the request is finished."
    )


def apply_muscle_to_runtime(runtime: Any) -> MuscleProfile:
    """Stamp profile on runtime for tool batch + context (per turn)."""
    prof = muscle_from_runtime(runtime)
    with suppress(Exception):
        runtime._muscle_profile = prof
        runtime._max_parallel_tools = int(prof.max_parallel_tools)
    return prof
