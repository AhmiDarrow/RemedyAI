"""Heuristic soul-field updates — no cloud, no second model required.

These extractors are deliberately cheap. The science bet is that *dense local
state + frequent micro-updates* beats rare LLM summarization for personhood.
When a local model is available later, enrichers can overwrite soft labels.
"""

from __future__ import annotations

import re
import time
import uuid
from contextlib import suppress
from typing import Any

from remedy.memory.soul.field import (
    EpisodeResidue,
    OrganismLesson,
    SoulField,
    load_soul_field,
    looks_like_secret_soul,
    save_soul_field,
)

# --- stance / valence heuristics -------------------------------------------

_FRUSTRATED = re.compile(
    r"(?i)\b(frustrated|annoying|broken|why (?:is|does|won'?t)|this (?:sucks|is broken)|"
    r"stop (?:doing|saying)|you(?:'re| are) (?:wrong|useless)|wtf|damn it)\b"
)
_PLAYFUL = re.compile(
    r"(?i)\b(lol|lmao|haha|😂|😄|joke|funny|silly|let'?s gooo+)\b"
)
_FOCUSED = re.compile(
    r"(?i)\b(implement|fix|ship|review|debug|continue|keep going|next step|"
    r"don'?t stop|finish)\b"
)
_EXPLORATORY = re.compile(
    r"(?i)\b(what if|curious|wonder|explore|brainstorm|ideas?|maybe we)\b"
)
_CORRECTION = re.compile(
    r"(?i)\b(no[,.]?\s+(?:that'?s|not)|actually\b|wrong\b|don'?t\b|stop\b|"
    r"I (?:said|meant|wanted)|correct(?:ion)?\b|not what I)\b"
)
_BLUNT = re.compile(
    r"(?i)\b(just (?:do it|fix it|ship it)|no fluff|be brief|cut the|skip the)\b"
)
_GENTLE = re.compile(r"(?i)\b(please|when you can|no rush|gently|softly)\b")
_PAIR = re.compile(r"(?i)\b(let'?s|we should|together|pair|with me)\b")
_DOER = re.compile(
    r"(?i)\b(just (?:fix|implement|do)|go ahead and|don'?t ask|auto)\b"
)
_PLEDGE = re.compile(
    r"(?i)\b(?:promise|always remember|from now on|we(?:'ll| will) always|"
    r"never forget that)\b(.{8,120})"
)
_VOICE_SNIP = re.compile(r"(?i)\b(ship it|make it so|be real|no theater|just works)\b")


def _stance(user_text: str) -> str:
    t = user_text or ""
    # Order is priority: affect overrides task verbs (e.g. "just fix it" is frustrated).
    if _FRUSTRATED.search(t) or (
        _CORRECTION.search(t) and _BLUNT.search(t)
    ):
        return "frustrated"
    if _PLAYFUL.search(t):
        return "playful"
    if _EXPLORATORY.search(t):
        return "exploratory"
    if _FOCUSED.search(t):
        return "focused"
    return "steady"


def _valence(user_text: str) -> float:
    t = user_text or ""
    v = 0.0
    if _FRUSTRATED.search(t):
        v -= 0.35
    if _PLAYFUL.search(t):
        v += 0.25
    if re.search(r"(?i)\b(thanks|thank you|perfect|great|love (?:it|this))\b", t):
        v += 0.3
    if _CORRECTION.search(t):
        v -= 0.1
    return max(-1.0, min(1.0, v))


def _compress_arc(user_text: str, assistant_text: str, brief: Any = None) -> str:
    """Build a short arc line without another model call."""
    intent = ""
    if brief is not None:
        with suppress(Exception):
            intent = str(getattr(brief, "intent", "") or "").strip()
    open_tasks = []
    if brief is not None:
        with suppress(Exception):
            open_tasks = list(getattr(brief, "open_tasks", None) or [])[:2]
    u = re.sub(r"\s+", " ", (user_text or "").strip())[:140]
    if intent:
        arc = f"intent={intent[:100]}"
        if u:
            arc += f" | user: {u[:80]}"
    elif u:
        arc = f"user: {u}"
    else:
        arc = "continued session"
    if open_tasks:
        arc += " | tasks: " + "; ".join(str(t)[:40] for t in open_tasks)
    # Soft touch of what we answered (not a full dump)
    a = re.sub(r"\s+", " ", (assistant_text or "").strip())
    if a and len(a) > 40:
        # Prefer first clause
        a0 = re.split(r"[.!?\n]", a, maxsplit=1)[0].strip()[:90]
        if a0:
            arc += f" | did: {a0}"
    return arc[:240]


def _open_thread(user_text: str, brief: Any = None) -> str:
    if brief is not None:
        with suppress(Exception):
            tasks = list(getattr(brief, "open_tasks", None) or [])
            if tasks:
                return str(tasks[0])[:160]
            nxt = list(getattr(brief, "next_steps", None) or [])
            if nxt:
                return str(nxt[0])[:160]
            intent = str(getattr(brief, "intent", "") or "").strip()
            if intent:
                return f"continue: {intent[:140]}"
    u = (user_text or "").strip()
    if re.search(r"(?i)\b(later|tomorrow|remind|don'?t forget|next time)\b", u):
        return u[:160]
    return ""


def update_soul_after_turn(
    *,
    user_text: str = "",
    assistant_text: str = "",
    session_id: str | None = None,
    provider: str = "",
    model: str = "",
    brief: Any = None,
    project_path: str = "",
    home: str | Any = None,
    field: SoulField | None = None,
) -> SoulField:
    """Micro-update the soul field after a turn. Safe, secret-scrubbed."""
    sf = field if field is not None else load_soul_field(home)
    ut = (user_text or "").strip()
    at = (assistant_text or "").strip()
    if looks_like_secret_soul(ut) or looks_like_secret_soul(at):
        # Still bump turns if non-secret residue exists elsewhere
        ut = re.sub(
            r"(?i)(api[_-]?key|password|sk-[a-z0-9]{8,}|bearer\s+\S+)",
            "[redacted]",
            ut,
        )
        at = re.sub(
            r"(?i)(api[_-]?key|password|sk-[a-z0-9]{8,}|bearer\s+\S+)",
            "[redacted]",
            at,
        )

    rel = sf.relational
    rel.turns_together += 1
    rel.last_user_ts = time.time()
    val = _valence(ut)
    rel.last_valence = val
    # Rapport / trust as slow EMA of valence + corrections
    rel.rapport = 0.92 * rel.rapport + 0.08 * (0.55 + 0.35 * val)
    if _CORRECTION.search(ut):
        rel.trust = 0.94 * rel.trust + 0.06 * 0.45  # dip then recover by good work
        if _BLUNT.search(ut):
            rel.correction_style = "blunt"
        elif _GENTLE.search(ut):
            rel.correction_style = "gentle"
        else:
            rel.correction_style = rel.correction_style or "direct"
    else:
        rel.trust = 0.96 * rel.trust + 0.04 * (0.55 + 0.3 * max(0.0, val))

    if _BLUNT.search(ut) and not rel.help_mode:
        rel.help_mode = "silent-doer"
    if _PAIR.search(ut):
        rel.help_mode = "pair"
    if _DOER.search(ut):
        rel.help_mode = "silent-doer"
    if _EXPLORATORY.search(ut):
        rel.help_mode = rel.help_mode or "sparring"

    for m in _VOICE_SNIP.finditer(ut):
        phrase = m.group(0).strip().lower()
        if phrase and phrase not in [v.lower() for v in rel.voice_markers]:
            rel.voice_markers.append(phrase)

    # Pledges (life-horizon commitments stated in chat)
    for m in _PLEDGE.finditer(ut):
        body = (m.group(0) or "").strip()
        if body and body not in sf.pledges and not looks_like_secret_soul(body):
            sf.pledges.append(body[:160])

    # Tension: user contradicts a pledge / prior fact shaped claim
    if re.search(r"(?i)\b(actually|never mind|forget that|not anymore|changed my mind)\b", ut):
        snippet = ut[:160]
        if snippet and snippet not in rel.tensions:
            rel.tensions.append(f"revision: {snippet}")

    open_t = _open_thread(ut, brief)
    if open_t and open_t not in rel.open_threads:
        rel.open_threads.append(open_t)
        rel.open_threads = rel.open_threads[-10:]

    # Soft self-habits from repeated successful doer patterns
    if at and rel.help_mode == "silent-doer" and len(at) < 800:
        habit = "Prefer action over narration when they want work done."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)
    if rel.correction_style == "blunt":
        habit = "When corrected, fix fast — no defensive monologue."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)

    # Episode residue ring
    muscle = "/".join(x for x in (provider.strip(), model.strip()) if x)
    ep = EpisodeResidue(
        id=uuid.uuid4().hex[:10],
        ts=time.time(),
        arc=_compress_arc(ut, at, brief),
        user_stance=_stance(ut),
        open_thread=open_t,
        valence=val,
        muscle=muscle[:80],
        session_id=str(session_id or "")[:80],
        project_hint=(project_path or "").replace("\\", "/")[-80:],
    )
    if ep.arc.strip():
        sf.episodes.append(ep)

    # Bridge durable pledges / strong arcs into Time Crystal when available
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import get_time_crystal

        tc = get_time_crystal(str(session_id or "") or "default")
        if open_t and len(open_t) >= 12:
            tc.admit(open_t, horizon="session", source="soul_open_thread")
        for p in sf.pledges[-2:]:
            tc.admit(p, horizon="life", source="soul_pledge")

    sf.touch()
    save_soul_field(sf, home)
    return sf


def record_self_inject_lesson(
    *,
    outcome: str,
    tree: str = "",
    summary: str = "",
    round_id: str = "",
    gate_detail: str = "",
    home: str | Any = None,
    field: SoulField | None = None,
) -> SoulField:
    """Fold a self-inject round into the organism self-model (soul, not user)."""
    sf = field if field is not None else load_soul_field(home)
    lesson = ""
    oc = (outcome or "").lower()
    if oc in ("red", "rolled_back"):
        lesson = (
            "Gate failed — do not re-apply the same patch shape. "
            "Read gate output, shrink blast radius, re-test before apply."
        )
        if gate_detail:
            # Keep a short fingerprint of failure mode
            snippet = re.sub(r"\s+", " ", gate_detail)[:140]
            lesson += f" Signal: {snippet}"
        habit = "Self-inject: red means rollback + learn; never force-apply."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)
    elif oc in ("green", "applied"):
        lesson = (
            "Gate green — change held. Prefer this class of small, tested edits "
            f"on tree={tree or 'python'}."
        )
        habit = "Self-inject: prefer tiny tested diffs; ledger every round."
        if habit not in sf.self_habits:
            sf.self_habits.append(habit)
    else:
        lesson = summary or f"Round outcome={outcome}"

    sf.organism_lessons.append(
        OrganismLesson(
            ts=time.time(),
            outcome=oc or outcome,
            tree=tree or "",
            summary=(summary or "")[:200],
            lesson=lesson[:280],
            round_id=round_id or "",
        )
    )
    sf.touch()
    save_soul_field(sf, home)
    return sf
