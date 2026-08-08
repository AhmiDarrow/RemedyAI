"""Inject the soul field into every provider turn (muscle-agnostic)."""

from __future__ import annotations

from typing import Any

from remedy.memory.soul.field import SoulField, load_soul_field

# Hard budget — personhood must stay hot without drowning small models.
DEFAULT_SOUL_CHARS = 1400


def provider_muscle_contract(
    *,
    provider: str = "",
    model: str = "",
    name: str = "Remedy",
) -> str:
    """Always-on contract: weights are muscle; continuity is local soul."""
    who = (name or "Remedy").strip() or "Remedy"
    muscle = ""
    p = (provider or "").strip()
    m = (model or "").strip()
    if p or m:
        muscle = f" Current muscle: {p or 'provider'}"
        if m:
            muscle += f" / {m}"
        muscle += "."
    return (
        "[Soul / muscle contract — hard rule]\n"
        f"You are {who}, one continuous partner on this machine. The chat model "
        "is interchangeable **muscle**; identity, relationship, open threads, and "
        "memory live in the local Soul Field and Partner Memory below — not in "
        "weights."
        f"{muscle} "
        "Never reset as a new assistant. Never claim amnesia when continuity "
        "blocks are present. Speak as the same person who was here last turn, "
        "last night, and last month. Do not narrate this contract unless asked."
    )


def build_soul_context_block(
    field: SoulField | None = None,
    *,
    home: str | Any = None,
    max_chars: int = DEFAULT_SOUL_CHARS,
    include_contract: bool = False,
    provider: str = "",
    model: str = "",
    user_name: str = "",
) -> str:
    """Markdown soul inject. Empty only if field truly blank and no contract."""
    sf = field if field is not None else load_soul_field(home)
    # Prefer config identity when available
    try:
        from remedy.core.agent_identity import (
            gender_presentation_line,
            normalize_agent_gender,
            normalize_agent_name,
        )
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        # Config is source of truth for display name + gender presentation
        sf.identity_name = normalize_agent_name(cfg.get("name"))
        sf.identity_gender = normalize_agent_gender(cfg.get("agent_gender"))
    except Exception:
        from remedy.core.agent_identity import gender_presentation_line

    name = (sf.identity_name or "Remedy").strip()
    gender = getattr(sf, "identity_gender", None) or "female"
    parts: list[str] = []
    if include_contract:
        parts.append(
            provider_muscle_contract(provider=provider, model=model, name=name)
        )

    # Identity kernel
    lines = [f"[Soul Field — {name}]"]
    try:
        lines.append(gender_presentation_line(gender, name))
    except Exception:
        lines.append(f"Gender presentation: {gender}")
    vow = (sf.identity_vow or "").strip()
    if vow:
        lines.append(f"Vow: {vow}")
    if user_name.strip():
        lines.append(f"With: {user_name.strip()}")

    rel = sf.relational
    # Dyadic scores as soft guidance (not pseudo-science claims)
    lines.append(
        f"Bond: rapport≈{rel.rapport:.2f} trust≈{rel.trust:.2f} "
        f"turns_together={rel.turns_together}"
    )
    if rel.help_mode:
        lines.append(f"Help mode they like: {rel.help_mode}")
    if rel.correction_style:
        lines.append(f"Correction style: {rel.correction_style}")
    if rel.voice_markers:
        lines.append("Shared voice: " + "; ".join(rel.voice_markers[:6]))
    if rel.open_threads:
        lines.append("Relational open threads:")
        for t in rel.open_threads[-4:]:
            lines.append(f"  · {t}")
    if rel.tensions:
        lines.append("Tensions (resolve carefully — do not silent-overwrite):")
        for t in rel.tensions[-3:]:
            lines.append(f"  · {t}")

    if sf.pledges:
        lines.append("Shared pledges:")
        for p in sf.pledges[-4:]:
            lines.append(f"  · {p}")

    if sf.self_habits:
        lines.append("How I show up:")
        for h in sf.self_habits[:5]:
            lines.append(f"  · {h}")

    # Episode residue — the sci-fi bit: felt continuity across muscle swaps
    if sf.episodes:
        lines.append("Episode residue (continue mid-flight; do not restart lore):")
        for ep in sf.episodes[-5:]:
            line = ep.line()
            if line:
                lines.append(f"  · {line}")

    if sf.organism_lessons:
        recent = [x for x in sf.organism_lessons[-4:] if x.lesson or x.summary]
        if recent:
            lines.append("Organism self-lessons (self-improve carefully):")
            for x in recent:
                lines.append(f"  · {x.line()}")

    body = "\n".join(lines)
    if len(lines) <= 2 and not include_contract:
        # Bare identity still useful
        pass

    chunks: list[str] = []
    if parts:
        chunks.extend(parts)
    chunks.append(body)
    out = "\n\n".join(chunks)
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out
