"""Continuity quality metrics — what the organism should self-improve next.

Self-inject targets these scores so auto-improvement densifies memory and
personhood, not only random code churn.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from remedy.memory.soul.field import load_soul_field, soul_dir


@dataclass
class ContinuityScore:
    """0..1 scores; higher is healthier personhood tissue."""

    bond_health: float = 0.5
    episode_density: float = 0.0
    pledge_coverage: float = 0.0
    open_thread_hygiene: float = 0.5  # lower if too many stale threads
    organism_learning: float = 0.0
    overall: float = 0.0
    gaps: list[str] = field(default_factory=list)
    suggested_targets: list[dict[str, str]] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return asdict(self)


# Code areas that improve continuity when self-inject lands green
_CONTINUITY_TARGETS: list[dict[str, str]] = [
    {
        "id": "soul_update",
        "path": "src/remedy/memory/soul/update.py",
        "why": "Richer episode residue and stance extraction",
        "tree": "python",
    },
    {
        "id": "soul_dream",
        "path": "src/remedy/memory/soul/dream.py",
        "why": "Better dream consolidation / local enrich",
        "tree": "python",
    },
    {
        "id": "soul_inject",
        "path": "src/remedy/memory/soul/inject.py",
        "why": "Hotter personhood inject for any provider",
        "tree": "python",
    },
    {
        "id": "partner_memory",
        "path": "src/remedy/memory/partner_memory.py",
        "why": "Stronger durable fact extraction",
        "tree": "python",
    },
    {
        "id": "session_continuity",
        "path": "src/remedy/core/session_continuity.py",
        "why": "Multi-tab isolation and brief binding",
        "tree": "python",
    },
    {
        "id": "agent_context",
        "path": "src/remedy/core/agent_context.py",
        "why": "Context assembly order / soul+builder wiring",
        "tree": "python",
    },
    {
        "id": "time_crystal",
        "path": "src/remedy/core/metabolism/time_crystal.py",
        "why": "Horizon promotion of durable memory",
        "tree": "python",
    },
    {
        "id": "muscle_profile",
        "path": "src/remedy/core/muscle_profile.py",
        "why": "Capable-provider builder agency",
        "tree": "python",
    },
]


def measure_continuity(home: str | Path | None = None) -> ContinuityScore:
    """Score current soul tissue and emit gap-driven self-inject targets."""
    sf = load_soul_field(home)
    rel = sf.relational
    gaps: list[str] = []
    targets: list[dict[str, str]] = []

    bond = (float(rel.rapport) + float(rel.trust)) / 2.0
    if bond < 0.5:
        gaps.append("bond_low")
        targets.append(_CONTINUITY_TARGETS[0])  # update

    ep_n = len(sf.episodes)
    episode_density = min(1.0, ep_n / 8.0)
    if episode_density < 0.4:
        gaps.append("few_episodes")
        targets.append(_CONTINUITY_TARGETS[0])

    pledge_coverage = min(1.0, len(sf.pledges) / 4.0)
    if pledge_coverage < 0.25 and ep_n >= 3:
        gaps.append("pledges_thin")
        targets.append(_CONTINUITY_TARGETS[1])  # dream

    thr = len(rel.open_threads)
    # Hygiene: 1-4 threads is healthy; 0 or >6 is weak
    if thr == 0:
        open_hygiene = 0.35
        gaps.append("no_open_threads")
    elif thr <= 4:
        open_hygiene = 0.9
    else:
        open_hygiene = max(0.2, 1.0 - (thr - 4) * 0.12)
        gaps.append("thread_overflow")
        targets.append(_CONTINUITY_TARGETS[1])

    lessons = len(sf.organism_lessons)
    organism_learning = min(1.0, lessons / 6.0)
    if organism_learning < 0.3:
        gaps.append("few_self_lessons")
        targets.append(
            {
                "id": "self_inject",
                "path": "src/remedy/core/self_inject.py",
                "why": "Self-inject loop lessons → soul",
                "tree": "python",
            }
        )

    if not sf.self_habits:
        gaps.append("no_habits")
        targets.append(_CONTINUITY_TARGETS[2])

    # Always offer inject path if overall weak
    overall = (
        0.25 * bond
        + 0.2 * episode_density
        + 0.15 * pledge_coverage
        + 0.2 * open_hygiene
        + 0.2 * organism_learning
    )
    if overall < 0.55:
        for t in _CONTINUITY_TARGETS[:3]:
            if t not in targets:
                targets.append(t)

    # Dedupe targets by id
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for t in targets:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        uniq.append(t)

    score = ContinuityScore(
        bond_health=round(bond, 3),
        episode_density=round(episode_density, 3),
        pledge_coverage=round(pledge_coverage, 3),
        open_thread_hygiene=round(open_hygiene, 3),
        organism_learning=round(organism_learning, 3),
        overall=round(overall, 3),
        gaps=gaps,
        suggested_targets=uniq[:5],
    )
    try:
        path = soul_dir(home) / "continuity_score.json"
        path.write_text(
            json.dumps(score.to_public(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return score


def primary_self_inject_focus(home: str | Path | None = None) -> dict[str, Any]:
    """Pick the top continuity gap for a self-inject round."""
    score = measure_continuity(home)
    target = (score.suggested_targets or [_CONTINUITY_TARGETS[0]])[0]
    return {
        "overall": score.overall,
        "gaps": score.gaps,
        "focus": target,
        "summary": (
            f"Continuity overall={score.overall:.2f}; "
            f"focus {target['id']} ({target['why']})"
        ),
        "score": score.to_public(),
    }
