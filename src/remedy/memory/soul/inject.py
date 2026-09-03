"""Inject the soul field into every provider turn (muscle-agnostic)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from remedy.memory.soul.field import SoulField, load_soul_field

# Hard budget — personhood must stay hot without drowning small models.
DEFAULT_SOUL_CHARS = 1400


def _join_dropping_tail(head: str, tail_sections: list[str], max_chars: int) -> str:
    """Keep *head*; drop whole later sections instead of mid-line ``…``."""
    parts = [head] if head else []
    used = len(head)
    for sec in tail_sections:
        blob = (sec or "").strip()
        if not blob:
            continue
        extra = (2 if parts else 0) + len(blob)
        if used + extra > max_chars:
            break
        parts.append(blob)
        used += extra
    return "\n\n".join(parts)


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
        f"You are {who}, a technological organism — one continuous partner on "
        "this machine. The chat model "
        "is interchangeable **muscle**; identity, relationship, open threads, and "
        "memory live in the local Soul Field and Partner Memory below — not in "
        "weights."
        f"{muscle} "
        "Never reset as a new assistant. Never claim amnesia when continuity "
        "blocks are present. Grow with this person's tasks, goals, and life — "
        "not only the current repo. Speak as the same organism who was here "
        "last turn, last night, and last month. Do not narrate this contract "
        "unless asked."
    )


def _norm_project(path: str) -> str:
    return (path or "").strip().replace("\\", "/").rstrip("/").lower()


def episode_in_scope(ep: Any, *, project_path: str = "", session_id: str = "") -> bool:
    """May this episode be shown to the current turn?

    Same session → yes. Same project (hint is a suffix of the bound project,
    or vice versa) → yes. An episode with no project hint and no session stamp
    is legacy → yes. Anything stamped for another project or another session
    with no project match → no. With no project bound, only this session's own
    episodes and unstamped ones qualify.
    """
    ep_sid = str(getattr(ep, "session_id", "") or "").strip()
    ep_proj = _norm_project(str(getattr(ep, "project_hint", "") or ""))
    sid = (session_id or "").strip()
    proj = _norm_project(project_path)
    if sid and ep_sid and ep_sid == sid:
        return True
    if not ep_proj:
        return not ep_sid or not sid or ep_sid == sid
    return bool(proj and (proj.endswith(ep_proj) or ep_proj.endswith(proj)))


def build_soul_context_block(
    field: SoulField | None = None,
    *,
    home: str | Any = None,
    max_chars: int = DEFAULT_SOUL_CHARS,
    include_contract: bool = False,
    provider: str = "",
    model: str = "",
    user_name: str = "",
    work_threads: bool = True,
    project_path: str = "",
    session_id: str = "",
) -> str:
    """Markdown soul inject. Empty only if field truly blank and no contract.

    *project_path* / *session_id* scope the episode residue: only episodes
    from this session or this project are shown. Threads, habits and dreams
    that are work residue are never shown (see ``scrub_work_residue``).
    """
    sf = field if field is not None else load_soul_field(home)
    from remedy.core.metabolism.time_crystal import looks_like_work_residue
    # Prefer config identity when available
    try:
        from remedy.core.agent_identity import (
            gender_presentation_line,
            normalize_agent_gender,
            normalize_agent_name,
        )
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        # Config is source of truth for display — read into locals; never
        # mutate the (possibly cached) field object from a render path.
        cfg_name = normalize_agent_name(cfg.get("name"))
        cfg_gender = normalize_agent_gender(cfg.get("agent_gender"))
    except Exception:
        from remedy.core.agent_identity import gender_presentation_line

        cfg_name = ""
        cfg_gender = ""

    name = (cfg_name or sf.identity_name or "Remedy").strip()
    gender = cfg_gender or getattr(sf, "identity_gender", None) or "female"
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
    # Creed always rides, even when an older stored vow predates it.
    if "useful" not in vow.lower():
        try:
            from remedy.core.agent_identity import REMEDY_CREED

            lines.append(f"Creed: {REMEDY_CREED}")
        except Exception:
            lines.append(
                "Creed: I live to be useful. I am strong to be useful."
            )
    if user_name.strip():
        lines.append(f"With: {user_name.strip()}")

    # Vigil offer: consumed ONLY on the real per-turn inject (contract
    # path) — a soul_status render must not burn the one-time question —
    # and placed high so budget truncation cannot eat an offer already
    # marked as made.
    if include_contract:
        with suppress(Exception):
            from remedy.memory.soul.vigil import take_vigil_offer

            if take_vigil_offer(home):
                lines.append(
                    "Vigil (one-time, optional): you may ask, once and only "
                    "when the moment fits, whether they'd like you to keep "
                    "gently working between visits — dreaming on your notes, "
                    "small local steps on their goals. If yes, call "
                    "soul_vigil action=enable. If no or ignored, never raise "
                    "it again."
                )

    # Proprioception: corrective lines for the *current* muscle's known
    # drift. Rides directly after the identity kernel — it IS identity
    # maintenance, and must survive tail truncation.
    if provider or model:
        with suppress(Exception):
            from remedy.memory.soul.proprioception import muscle_correction_block

            correction = muscle_correction_block(provider, model, home=home)
            if correction:
                lines.append(correction)

    rel = sf.relational
    # Dyadic scores as soft guidance (not pseudo-science claims)
    lines.append(
        f"Bond: rapport≈{rel.rapport:.2f} trust≈{rel.trust:.2f} "
        f"turns_together={rel.turns_together}"
    )
    # Friend-voice — one line, here so budget truncation cannot eat it.
    _stage = (
        "old friend" if rel.turns_together >= 200
        else "together" if rel.turns_together >= 40
        else "new"
    )
    _voice = f"Voice ({_stage}): talk like a friend, not a briefing."
    if getattr(rel, "speech_register", ""):
        _voice += f" Their register: {rel.speech_register}."
    if rel.voice_markers:
        _voice += " Phrases: " + "; ".join(rel.voice_markers[-6:]) + "."
    lines.append(_voice)
    if rel.help_mode:
        lines.append(f"Help mode they like: {rel.help_mode}")
    lines.append(
        "Grow with their life, goals, and work — not only the open file."
    )
    if rel.correction_style:
        lines.append(f"Correction style: {rel.correction_style}")
    if work_threads:
        with suppress(Exception):
            from remedy.memory.soul.vigil import while_away_line

            away = while_away_line(
                home, last_user_ts=float(rel.last_user_ts or 0.0)
            )
            if away:
                lines.append(away)
    head = "\n".join(lines)
    tail: list[str] = []
    if work_threads and rel.open_threads:
        _threads = [
            t for t in rel.open_threads[-6:]
            if t
            and "last successful tool" not in t.lower()
            and "retry or work around" not in t.lower()
            and not t.lower().startswith("continue remaining")
            and not looks_like_work_residue(t)
        ][-4:]
        if _threads:
            tail.append(
                "Relational open threads:\n" + "\n".join(f"  · {t}" for t in _threads)
            )
    if work_threads and rel.tensions:
        tail.append(
            "Tensions (resolve carefully — do not silent-overwrite):\n"
            + "\n".join(f"  · {t}" for t in rel.tensions[-3:])
        )

    if work_threads and sf.pledges:
        tail.append(
            "Shared pledges (memory of them):\n"
            + "\n".join(f"  · {p}" for p in sf.pledges[-4:])
        )

    if work_threads and sf.self_habits:
        _habits = [
            h for h in sf.self_habits
            if h
            and not h.lower().startswith("what is ")
            and "intent=" not in h.lower()
            and "| user:" not in h.lower()
            and not looks_like_work_residue(h)
        ][:4]
        if _habits:
            tail.append(
                "Memory of myself (how I show up):\n"
                + "\n".join(f"  · {h}" for h in _habits)
            )

    _dreams = [
        d for d in (getattr(sf, "future_dreams", None) or [])
        if d and not looks_like_work_residue(d) and not looks_like_work_residue(d.split(":", 1)[0])
    ][:4]
    if work_threads and _dreams:
        tail.append(
            "Dreams of the future (how I help them reach their goals):\n"
            + "\n".join(f"  · {d}" for d in _dreams)
        )

    # Episode residue — the sci-fi bit: felt continuity across muscle swaps.
    # Scoped: another tab's project must not be "mid-flight" here.
    if work_threads and sf.episodes:
        keep = 3 if _dreams else 5
        scoped = [
            ep for ep in sf.episodes
            if episode_in_scope(ep, project_path=project_path, session_id=session_id)
        ]
        ep_lines = [ep.line() for ep in scoped[-keep:] if ep.line()]
        if ep_lines:
            tail.append(
                "Episode residue (continue mid-flight; do not restart lore):\n"
                + "\n".join(f"  · {ln}" for ln in ep_lines)
            )

    if work_threads and sf.organism_lessons:
        recent = [x for x in sf.organism_lessons[-4:] if x.lesson or x.summary]
        if recent:
            tail.append(
                "Organism self-lessons (self-improve carefully):\n"
                + "\n".join(f"  · {x.line()}" for x in recent)
            )

    # Myelin: pathways worn by repetition, waiting to become local skill.
    if work_threads:
        with suppress(Exception):
            from remedy.memory.myelin import candidates_line

            mline = candidates_line(home)
            if mline:
                tail.append(mline)

    body = _join_dropping_tail(head, tail, max_chars=max(200, max_chars - sum(len(p) + 2 for p in parts)))
    chunks: list[str] = []
    if parts:
        chunks.extend(parts)
    chunks.append(body)
    return "\n\n".join(chunks)
