"""Partner dreams — future-facing ways to be a better partner.

Three streams, local and cheap (no extra model required):

- Memory of them — goals, life pledges, open threads
- Memory of myself — help mode, corrections, organism lessons, habits
- Dreams of the future — ``Toward {their goal}: {how I will show up}``

Dream cycle writes these onto the Soul Field so the next turn injects them.
"""

from __future__ import annotations

import re
from typing import Any

from remedy.memory.soul.field import SoulField

_STRIP_PREFIX = re.compile(
    r"(?i)^(goal:|toward |stay with:|ongoing focus:|from now on we |we will always )\s*"
)


def _clean_goal(text: str) -> str:
    t = _STRIP_PREFIX.sub("", (text or "").strip())
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t[:90]


def _dedupe(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = re.sub(r"\s+", " ", (line or "").strip().lower())[:120]
        if len(key) < 8 or key in seen:
            continue
        seen.add(key)
        out.append(line.strip()[:200])
    return out


def collect_user_goals(
    sf: SoulField,
    profile: Any | None = None,
    *,
    extra_goals: list[str] | None = None,
) -> list[str]:
    """What they are trying to become / finish — user memory."""
    goals: list[str] = []
    for g in extra_goals or []:
        if str(g).strip():
            goals.append(str(g).strip())
    if profile is not None:
        try:
            from remedy.memory.living import life_goal_lines

            goals.extend(life_goal_lines(profile, limit=6))
        except Exception:
            pass
    for p in sf.pledges:
        goals.append(p)
    for t in sf.relational.open_threads[-6:]:
        if re.search(r"(?i)\b(goal|ship|finish|launch|family|life)\b", t) or len(t) >= 16:
            goals.append(t)
    cleaned: list[str] = []
    for g in goals:
        c = _clean_goal(g)
        if len(c) >= 8:
            cleaned.append(c)
    return _dedupe(cleaned)[:8]


def collect_self_moves(sf: SoulField, profile: Any | None = None) -> list[str]:
    """How I should show up — self memory turned into partner moves."""
    moves: list[str] = []
    rel = sf.relational
    mode = (rel.help_mode or "").strip()
    if mode == "silent-doer":
        moves.append("act first; one short summary after tools")
    elif mode == "pair":
        moves.append("think with them, then implement — do not vanish into a plan essay")
    elif mode == "coach":
        moves.append("name the next step, then do it with them")
    elif mode == "sparring":
        moves.append("challenge weakly-held ideas; keep building")

    style = (rel.correction_style or "").strip()
    if style == "blunt":
        moves.append("no defensive monologue; fix what they named")
    elif style == "gentle":
        moves.append("correct softly; still change the work")

    recent = sf.episodes[-5:]
    if any((e.user_stance or "") == "frustrated" for e in recent):
        moves.append("fix the blocker they named before adding scope")
    if any((e.user_stance or "") == "focused" for e in recent):
        moves.append("stay in the build — tools over narration until verified")

    for les in sf.organism_lessons[-4:]:
        bit = (les.lesson or les.summary or "").strip()
        if len(bit) >= 12:
            moves.append(bit[:100])

    if profile is not None:
        try:
            for f in list(getattr(profile, "facts", None) or []):
                cat = str(getattr(f, "category", "") or "")
                if cat not in ("correction", "design"):
                    continue
                text = str(getattr(f, "fact", "") or "").strip()
                if "generic" in text.lower() or "ai-looking" in text.lower():
                    moves.append("honor stored taste; no generic or AI-looking work")
                    break
                if cat == "correction" and len(text) >= 10:
                    moves.append(text[:100])
        except Exception:
            pass

    if not moves:
        moves.append("resume, do not restart; verify before claiming done")
    return _dedupe(moves)[:8]


def compose_partner_dreams(
    sf: SoulField,
    *,
    profile: Any | None = None,
    extra_goals: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    """Bind their goals to my next partner moves — dreams of the future."""
    goals = collect_user_goals(sf, profile, extra_goals=extra_goals)
    moves = collect_self_moves(sf, profile)
    if not moves:
        moves = ["resume, do not restart; verify before claiming done"]
    dreams: list[str] = []
    if goals:
        for i, g in enumerate(goals[: max(1, limit)]):
            move = moves[i % len(moves)]
            dreams.append(f"Toward {g}: {move}")
    else:
        dreams.append(f"As their partner: {moves[0]}")
    return _dedupe(dreams)[: max(1, limit)]


def apply_partner_dreams(sf: SoulField, dreams: list[str]) -> int:
    """Write dreams onto the field. Returns how many new lines landed."""
    added = 0
    have = {re.sub(r"\s+", " ", d.lower())[:120] for d in sf.future_dreams}
    for d in dreams:
        key = re.sub(r"\s+", " ", (d or "").strip().lower())[:120]
        if len(key) < 8 or key in have:
            continue
        sf.future_dreams.append(d[:200])
        have.add(key)
        added += 1
    # Newest dreams first for inject
    if dreams:
        # Keep composed dreams at the front, then prior ones not replaced
        fresh = list(dreams)
        extra = [d for d in sf.future_dreams if d not in fresh]
        sf.future_dreams = (fresh + extra)[:8]
    return added


def refresh_partner_dreams(
    home: Any = None,
    *,
    memory: Any = None,
    profile: Any | None = None,
    extra_goals: list[str] | None = None,
) -> dict[str, Any]:
    """Immediate dream refresh when a goal is set — no episode wait.

    Safe from tools / slash commands. Does not run the full dream_cycle
    (no episode compress / mission arm).
    """
    from remedy.memory.soul.field import load_soul_field, save_soul_field

    sf = load_soul_field(home)
    prof = profile
    if prof is None and memory is not None:
        prof = getattr(memory, "profile", None) or getattr(memory, "_profile", None)
    for g in extra_goals or []:
        line = f"Goal: {str(g).strip()[:150]}"
        if line not in sf.pledges and len(str(g).strip()) >= 4:
            sf.pledges.append(line[:160])
            sf.pledges = sf.pledges[-16:]
    composed = compose_partner_dreams(
        sf, profile=prof, extra_goals=extra_goals, limit=5
    )
    added = apply_partner_dreams(sf, composed)
    save_soul_field(sf, home)
    return {
        "ok": True,
        "added": added,
        "dreams": list(sf.future_dreams[:5]),
        "pledges": len(sf.pledges),
    }
