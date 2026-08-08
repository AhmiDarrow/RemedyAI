"""Soul OS — provider-invariant personhood for Remedy.

Thesis
------
The LLM is **muscle** (interchangeable compute). Continuity lives in a local
**soul field**: identity kernel, dyadic relational state, episode residue, and
an organism self-model. Any provider that animates the field should feel like
the *same person* because the field — not the weights — carries who we are
together.

This is intentionally not RAG-of-facts. Facts are one stream. Personhood is
the field dynamics: unfinished arcs, rapport, tensions, and how we correct
each other over time.
"""

from __future__ import annotations

from remedy.memory.soul.field import (
    EpisodeResidue,
    RelationalField,
    SoulField,
    load_soul_field,
    save_soul_field,
)
from remedy.memory.soul.continuity_metrics import measure_continuity, primary_self_inject_focus
from remedy.memory.soul.dream import dream_cycle
from remedy.memory.soul.inject import build_soul_context_block, provider_muscle_contract
from remedy.memory.soul.missions_bridge import arm_soul_missions
from remedy.memory.soul.portable import import_soul_file, soul_export_payload
from remedy.memory.soul.recall import recall_unified
from remedy.memory.soul.somatic import compute_soma, refresh_soma
from remedy.memory.soul.update import (
    record_self_inject_lesson,
    update_soul_after_turn,
)

__all__ = [
    "EpisodeResidue",
    "RelationalField",
    "SoulField",
    "arm_soul_missions",
    "build_soul_context_block",
    "compute_soma",
    "dream_cycle",
    "import_soul_file",
    "load_soul_field",
    "measure_continuity",
    "primary_self_inject_focus",
    "provider_muscle_contract",
    "recall_unified",
    "record_self_inject_lesson",
    "refresh_soma",
    "save_soul_field",
    "soul_export_payload",
    "update_soul_after_turn",
]
