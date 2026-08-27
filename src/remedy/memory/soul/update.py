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
    encode_lesson_strength,
    encode_strength,
    find_pledge_key,
    load_soul_field,
    looks_like_secret_soul,
    pledge_trace_touch,
    save_soul_field,
)

# Words too common to signal that an old memory is genuinely relevant again.
_RECON_STOP = frozenset(
    ["the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with", "it", "this", "that", "is", "are", "was", "were", "be", "i", "you", "we", "he", "she", "they", "me", "my", "your", "our", "do", "did", "does", "can", "could", "would", "should", "just", "now", "then", "so", "if", "not", "no", "yes", "ok", "okay", "let", "lets", "get", "got", "go", "going", "make", "want", "need", "have", "has", "had", "will", "won", "about", "into", "out", "up", "down", "over", "what", "how", "why", "when", "where", "who", "which", "as", "at", "by", "from"]
)


def _recon_tokens(text: str) -> set[str]:
    """Content tokens for reconsolidation matching (lowercased, de-noised)."""
    out: set[str] = set()
    for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", (text or "").lower()):
        if w not in _RECON_STOP:
            out.add(w)
    return out


def _reconsolidate_episodes(sf: SoulField, tokens: set[str], now: float) -> None:
    """Recall strengthens memory: when this turn is about what an old episode
    holds, that trace is in active use — harden it against forgetting.

    Content-token overlap is a cheap, local stand-in for "this came up again."
    Two shared meaningful words is enough to count as a recall; the trace ticks
    up toward 1.0 and its forgetting clock resets to now. Bounded per turn so a
    chatty message can't reinforce the whole ring at once.
    """
    if not tokens:
        return
    scored: list[tuple[int, EpisodeResidue]] = []
    for ep in sf.episodes:
        et = _recon_tokens(f"{ep.arc} {ep.open_thread}")
        if not et:
            continue
        overlap = len(tokens & et)
        if overlap >= 2:
            scored.append((overlap, ep))
    # Reinforce the strongest few matches only (avoid ring-wide inflation).
    for _, ep in sorted(scored, key=lambda s: s[0], reverse=True)[:3]:
        ep.strength = min(1.0, float(ep.strength or 0.0) + 0.12)
        ep.recalls = int(ep.recalls or 0) + 1
        ep.last_recall_ts = now

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
_VOICE_SNIP = re.compile(
    r"(?i)\b(ship it|make it so|be real|no theater|just works|feels like|no fluff)\b"
)
_CASUAL = re.compile(
    r"(?i)\b(yeah|yep|nah|gonna|wanna|kinda|idk|lol|hey|feels like)\b|"
    r"\b\w+n't\b|\b(?:it's|i'm|that's|you're)\b"
)


def _infer_register(text: str) -> str:
    """Cheap write-register from this turn. Empty = nothing to learn."""
    t = (text or "").strip()
    if not t:
        return ""
    casual = bool(_CASUAL.search(t))
    if len(t) < 90:
        return "casual-short" if casual else "terse"
    return "casual" if casual else ""


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
    with suppress(Exception):
        from remedy.memory.authority import is_hive_writer

        if is_hive_writer(session_id):
            return field if field is not None else load_soul_field(home)
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
    _reg = _infer_register(ut)
    if _reg:
        # Casual sticks; one long task prompt must not wipe it.
        if not rel.speech_register or not rel.speech_register.startswith("casual"):
            rel.speech_register = _reg
        elif _reg.startswith("casual"):
            rel.speech_register = _reg

    # Pledges (life-horizon commitments stated in chat). A re-stated pledge is
    # a recall: its trace reconsolidates, so live commitments outlast dormant ones.
    for m in _PLEDGE.finditer(ut):
        body = (m.group(0) or "").strip()
        if body and not looks_like_secret_soul(body):
            key = find_pledge_key(sf, body)
            if key not in sf.pledges:
                sf.pledges.append(key)
            pledge_trace_touch(sf, body)

    # Living extractors: life/goal lines become soul pledges (same organism)
    with suppress(Exception):
        from remedy.memory.living import extract_living_facts

        for fact in extract_living_facts(ut):
            if fact.category not in ("life", "goal"):
                continue
            body = (fact.text or "").strip()
            if body and len(body) >= 8 and not looks_like_secret_soul(body):
                key = find_pledge_key(sf, body)
                if key not in sf.pledges:
                    sf.pledges.append(key)
                pledge_trace_touch(sf, body)

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

    # Reconsolidation: strengthen any existing episode this turn is *about*,
    # before laying down the new one (so it can't match itself). Memories in
    # active use resist the forgetting curve; trivial one-offs age out.
    now_ts = time.time()
    _reconsolidate_episodes(sf, _recon_tokens(ut), now_ts)

    # Episode residue ring
    muscle = "/".join(x for x in (provider.strip(), model.strip()) if x)
    ep = EpisodeResidue(
        id=uuid.uuid4().hex[:10],
        ts=now_ts,
        arc=_compress_arc(ut, at, brief),
        user_stance=_stance(ut),
        open_thread=open_t,
        valence=val,
        muscle=muscle[:80],
        session_id=str(session_id or "")[:80],
        project_hint=(project_path or "").replace("\\", "/")[-80:],
        # Encode strength from emotional intensity; fresh episode counts as its
        # own first "recall" so its forgetting clock starts now.
        strength=encode_strength(val),
        recalls=0,
        last_recall_ts=now_ts,
    )
    if ep.arc.strip():
        sf.episodes.append(ep)

    # Bridge durable pledges / strong arcs into Time Crystal when available
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import (
            get_time_crystal,
            looks_like_job_resume_fact,
        )

        tc = get_time_crystal(str(session_id or "") or "default")
        if open_t and len(open_t) >= 12:
            if not looks_like_job_resume_fact(open_t, source="soul_open_thread"):
                tc.admit(open_t, horizon="session", source="soul_open_thread")

        for p in sf.pledges[-2:]:
            # Identity pledges stay life. "Stay with: Continue…" is last-tab
            # work and must not land on a fresh session as a standing job.
            if looks_like_job_resume_fact(p, source="soul_pledge"):
                continue
            tc.admit(p, horizon="life", source="soul_pledge")

    # Myelin: count the pathway this turn wore (repetition earns crystallizing)
    with suppress(Exception):
        from remedy.memory.myelin import observe_pathway

        observe_pathway(ut, home)

    # Proprioception: fold how this muscle rendered us into its profile
    with suppress(Exception):
        from remedy.memory.soul.proprioception import observe_render

        observe_render(
            assistant_text=at,
            user_text=ut,
            provider=provider,
            model=model,
            home=home,
        )

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

    now_ts = time.time()
    sf.organism_lessons.append(
        OrganismLesson(
            ts=now_ts,
            outcome=oc or outcome,
            tree=tree or "",
            summary=(summary or "")[:200],
            lesson=lesson[:280],
            round_id=round_id or "",
            # Encode by what it cost to learn: red scars hardest.
            strength=encode_lesson_strength(oc or outcome),
            recalls=0,
            last_recall_ts=now_ts,
        )
    )
    sf.touch()
    save_soul_field(sf, home)
    return sf
