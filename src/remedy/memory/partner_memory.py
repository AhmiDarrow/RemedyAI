"""Partner Memory — durable “what I know about you” for just-works continuity.

Goal: feel like an AI that never forgets *what matters* — identity, preferences,
stable constraints — without requiring power-user setup.

- Inject a small ranked block every turn (budget-capped).
- Quiet heuristic distillation from user text (safe auto only).
- Easy forget / inspect for trust.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from remedy.memory.profile import UserFact, UserProfile

# Injection budget (chars) — denser personhood without drowning small models
DEFAULT_MAX_CHARS = 1400
MIN_INJECT_CONFIDENCE = 0.55
AUTO_ACCEPT_CONFIDENCE = 0.85
MAX_FACT_LEN = 280
MAX_AUTO_FACTS_PER_PASS = 6
# Hot-path inject caps (ranked facts / traits in every-turn block)
MAX_HOT_FACTS = 14
MAX_HOT_TRAITS = 10

# Explicit preference / identity patterns (user lines only)
# Confidence ≥ AUTO_ACCEPT_CONFIDENCE (0.85) is auto-stored.
_PREF_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (
        re.compile(
            r"(?:my name is|i(?:'m| am) called|call me)\s+([A-Za-z][A-Za-z0-9_.\- ]{1,40})",
            re.I,
        ),
        "identity",
        0.92,
    ),
    (
        re.compile(
            r"i (?:prefer|always (?:use|prefer|want)|usually (?:use|prefer))\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "preference",
        0.88,
    ),
    (
        re.compile(
            r"(?:please )?(?:always|never)\s+(?:use|prefer|do|run|deploy|commit|push|force-?push|merge|delete|share|store|save|open|install)\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "constraint",
        0.9,
    ),
    (
        re.compile(
            r"(?:don'?t|do not|never)\s+(?:ever\s+)?(use|prefer|run|deploy|commit|push|force-?push|merge|delete|share|store|save)\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "constraint",
        0.86,
    ),
    # Explicit remember — highest priority; capture short key=value too
    (
        re.compile(
            r"(?:please\s+)?remember(?:\s+(?:this|that|the))?(?:\s+fact)?[:\s]+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "general",
        0.97,
    ),
    (
        re.compile(
            r"(?:note that|for (?:future|later) reference|don'?t forget)[:\s]+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "general",
        0.93,
    ),
    (
        re.compile(
            r"(?:store|save|keep)\s+(?:this\s+)?(?:in\s+)?(?:memory|partner memory)[:\s]+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "general",
        0.95,
    ),
    (
        re.compile(
            r"i (?:work (?:in|with|on)|use|write in)\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "preference",
        0.8,
    ),
    # Relational / life-context — personhood density (not just coding prefs)
    (
        re.compile(
            r"i (?:hate|love|can'?t stand|enjoy)\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "preference",
        0.86,
    ),
    (
        re.compile(
            r"(?:i(?:'m| am)|we(?:'re| are))\s+(?:a |an )?([^.!?\n]{4,80}(?:engineer|developer|founder|designer|student|parent)[^.!?\n]{0,40})",
            re.I,
        ),
        "identity",
        0.88,
    ),
    (
        re.compile(
            r"(?:my (?:wife|husband|partner|kids?|dog|cat|team|company|boss))\s+([^.!?\n]{3,80})",
            re.I,
        ),
        "personal",
        0.84,
    ),
    (
        re.compile(
            r"(?:i live in|i'?m (?:based|from)|timezone is)\s+([A-Za-z0-9_, /\-]{2,60})",
            re.I,
        ),
        "identity",
        0.87,
    ),
    (
        re.compile(
            r"(?:when you (?:talk|reply|answer|help)|please (?:be|stay))\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "preference",
        0.89,
    ),
    (
        re.compile(
            r"(?:we always|from now on we|our rule is)\s+(.+?)(?:[.!?\n]|$)",
            re.I,
        ),
        "constraint",
        0.9,
    ),
]

# Explicit user intent to persist — bypass short-text guards
_EXPLICIT_REMEMBER_RE = re.compile(
    r"(?i)\b("
    r"remember(?:\s+(?:this|that|the|fact))?"
    r"|note that"
    r"|for (?:future|later) reference"
    r"|don'?t forget"
    r"|store (?:this )?(?:in )?memory"
    r"|save (?:this )?(?:to|in) memory"
    r"|/remember\b"
    r")\b"
)

_SECRET_RE = re.compile(
    r"(?i)("
    r"api[_-]?key|secret[_-]?key|password\s*[:=]|bearer\s+[a-z0-9\-._~+/]+=*"
    r"|sk-[a-z0-9]{10,}|xai-[a-z0-9]{10,}|ghp_[a-z0-9]{20,}"
    r"|-----begin (?:rsa )?private key-----"
    r"|authorization:\s*bearer"
    r")"
)

_NOISE_RE = re.compile(
    r"(?i)^(ok|okay|thanks|thank you|yes|no|yep|nope|continue|proceed|go ahead|hi|hello)\b"
)


@dataclass
class ExtractedFact:
    """A candidate durable fact from heuristic extraction."""

    text: str
    category: str
    confidence: float
    source: str = "heuristic"


def looks_like_secret(text: str) -> bool:
    """True if text appears to contain credentials — never auto-store."""
    if not text or not text.strip():
        return False
    if _SECRET_RE.search(text):
        return True
    # Long high-entropy tokens
    for tok in re.findall(r"[A-Za-z0-9_\-+/=]{40,}", text):
        if not re.search(r"[a-z]{4,}", tok, re.I):  # no words → likely key material
            return True
    return False


def normalize_fact_key(text: str) -> str:
    """Normalize for dedup / forget matching."""
    t = (text or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"[.!?]+$", "", t)
    return t[:MAX_FACT_LEN]


def is_stable_fact_text(text: str, *, explicit: bool = False) -> bool:
    """Reject ephemeral task chatter.

    *explicit*=True when the user said “remember …” — allow shorter key=value facts.
    """
    t = (text or "").strip()
    min_len = 4 if explicit else 8
    if len(t) < min_len or len(t) > MAX_FACT_LEN:
        return False
    if _NOISE_RE.match(t):
        return False
    if looks_like_secret(t):
        return False
    # One-off task imperatives — not durable partner memory
    if re.search(
        r"(?i)\b(always|never)\s+(run|do|try|check|look|open|start)\s+(the\s+)?(tests?|build|server|app|this|that|it)\b",
        t,
    ):
        return False
    if re.search(r"(?i)\b(right now|this turn|in this session|for now)\b", t):
        return False
    # Avoid pure paths as identity (unless preference-shaped)
    if re.fullmatch(r"[A-Za-z]:\\[^\n]{0,200}|/[^\s]{8,}", t):
        return False
    # Prefer multi-word durable statements; explicit remember allows key=value
    words = t.split()
    if len(words) < 3:
        if explicit and (
            "=" in t
            or re.search(r"(?i)\b(prefer|typescript|python|rust|means|is)\b", t)
            or re.fullmatch(r"[\w.\-]+", t)  # single token codes
        ):
            return True
        if not re.search(r"(?i)\b(prefer|typescript|python|rust)\b", t):
            return False
    # Avoid huge code dumps
    return not (t.count("\n") > 3 or t.count("{") > 2)


def is_explicit_remember_intent(user_text: str) -> bool:
    """True when the user clearly asked to store something durable."""
    return bool(_EXPLICIT_REMEMBER_RE.search(user_text or ""))


def extract_heuristic_facts(user_text: str) -> list[ExtractedFact]:
    """Pull high-confidence durable facts from a single user message."""
    text = (user_text or "").strip()
    if not text or looks_like_secret(text):
        return []

    explicit = is_explicit_remember_intent(text)
    found: list[ExtractedFact] = []
    seen: set[str] = set()

    for pattern, category, conf in _PREF_PATTERNS:
        for m in pattern.finditer(text):
            raw = (m.group(1) if m.lastindex else m.group(0)).strip()
            # Prefer full match for multi-group patterns (e.g. never X Y)
            if m.lastindex and m.lastindex >= 2:
                raw = re.sub(r"\s+", " ", m.group(0).strip())
            raw = re.sub(r"\s+", " ", raw).strip(" \t\"'")
            # Strip leading "fact " noise: "Remember fact X=Y" → "X=Y"
            raw = re.sub(r"(?i)^(?:this\s+)?fact\s+", "", raw).strip()
            if not is_stable_fact_text(raw, explicit=explicit or conf >= 0.95):
                # Store fuller phrase when capture is too short
                full = m.group(0).strip()
                full = re.sub(r"\s+", " ", full)
                if not is_stable_fact_text(full, explicit=explicit or conf >= 0.95):
                    continue
                fact_text = full[:MAX_FACT_LEN]
            else:
                # Prefer human-readable full clause for short captures
                if len(raw) < 20 and m.group(0) and conf < 0.95:
                    fact_text = re.sub(r"\s+", " ", m.group(0).strip())[:MAX_FACT_LEN]
                else:
                    fact_text = raw[:MAX_FACT_LEN]
            # Final gate — reject ephemeral task chatter even if capture looked fine
            if not is_stable_fact_text(fact_text, explicit=explicit or conf >= 0.95):
                continue
            key = normalize_fact_key(fact_text)
            if key in seen:
                continue
            seen.add(key)
            src = "explicit" if (explicit or conf >= 0.95) else "heuristic"
            found.append(
                ExtractedFact(
                    text=fact_text,
                    category=category,
                    confidence=conf,
                    source=src,
                )
            )
            if len(found) >= MAX_AUTO_FACTS_PER_PASS:
                return found

    return found


def extract_from_brief(brief: Any) -> list[ExtractedFact]:
    """Mine durable-looking constraints from Session Brief (lower confidence)."""
    if brief is None:
        return []
    out: list[ExtractedFact] = []
    seen: set[str] = set()

    def _add(text: str, category: str, conf: float) -> None:
        t = re.sub(r"\s+", " ", (text or "").strip())
        if not is_stable_fact_text(t):
            return
        # Only keep constraint-like brief lines
        if not re.search(
            r"(?i)\b(prefer|always|never|don'?t|must|should|constraint|use |avoid )\b",
            t,
        ):
            return
        key = normalize_fact_key(t)
        if key in seen:
            return
        seen.add(key)
        out.append(
            ExtractedFact(
                text=t[:MAX_FACT_LEN],
                category=category,
                confidence=conf,
                source="brief",
            )
        )

    for c in list(getattr(brief, "user_constraints", None) or [])[:12]:
        _add(str(c), "constraint", 0.82)
    for d in list(getattr(brief, "decisions", None) or [])[:8]:
        _add(str(d), "workflow", 0.72)

    return out[:MAX_AUTO_FACTS_PER_PASS]


def should_auto_accept(fact: ExtractedFact) -> bool:
    """Only auto-inject high-confidence stable facts."""
    if fact.confidence < AUTO_ACCEPT_CONFIDENCE:
        return False
    if not is_stable_fact_text(fact.text):
        return False
    return not looks_like_secret(fact.text)


def fact_near_duplicate(a: str, b: str) -> bool:
    """Cheap near-dup: normalized equality or containment."""
    na, nb = normalize_fact_key(a), normalize_fact_key(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return bool(len(na) >= 12 and len(nb) >= 12 and (na in nb or nb in na))


def upsert_profile_fact(
    profile: UserProfile,
    text: str,
    *,
    category: str = "general",
    confidence: float = 0.7,
    source: str = "inferred",
    force: bool = False,
    project_path: str | None = None,
    pinned: bool = False,
) -> tuple[UserFact | None, str]:
    """Add or reinforce a fact.

    Returns ``(fact, action)`` where action is ``added`` | ``reinforced`` | ``skipped``.
    """
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return None, "skipped"
    # Secrets never promote — ``force`` only relaxes stability heuristics,
    # never credential / key-shaped content (fail closed).
    if looks_like_secret(text):
        return None, "skipped"
    if not force and not is_stable_fact_text(text) and confidence < 0.95:
        return None, "skipped"

    proj = (project_path or "").strip() or None

    for existing in profile.facts:
        if fact_near_duplicate(existing.fact, text):
            existing.confidence = min(0.99, max(existing.confidence, confidence))
            existing.last_referenced = datetime.now(UTC)
            existing.reference_count = int(existing.reference_count or 1) + 1
            if category and category != "general":
                existing.category = category
            if source and (existing.source or "") in ("", "inferred"):
                existing.source = source
            if proj and not existing.project_path:
                existing.project_path = proj
            if pinned:
                existing.pinned = True
            return existing, "reinforced"

    uf = UserFact(
        fact=text[:MAX_FACT_LEN],
        category=category or "general",
        confidence=min(0.99, max(0.0, confidence)),
        source=source,
        project_path=proj,
        pinned=bool(pinned),
    )
    profile.facts.append(uf)
    # Soft cap growth
    if len(profile.facts) > 200:
        profile.facts = sorted(
            profile.facts,
            key=lambda f: (f.pinned, f.confidence, f.reference_count, f.last_referenced),
            reverse=True,
        )[:200]
    return uf, "added"


def apply_gentle_decay(
    profile: UserProfile,
    *,
    max_age_days: float = 90.0,
    min_floor: float = 0.35,
) -> int:
    """Lower confidence on unused low-signal facts. Never removes; never touches pinned.

    Returns number of facts adjusted.
    """
    now = datetime.now(UTC)
    n = 0
    for f in profile.facts:
        if f.pinned or (f.source or "") == "explicit":
            continue
        if f.confidence < MIN_INJECT_CONFIDENCE:
            continue
        try:
            age = (now - f.last_referenced).total_seconds() / 86400.0
        except Exception:
            continue
        if age < max_age_days:
            continue
        # Soft step down for rarely reinforced facts
        if int(f.reference_count or 1) <= 2:
            new_c = max(min_floor, f.confidence - 0.08)
            if new_c < f.confidence:
                f.confidence = new_c
                n += 1
    return n


def reinforce_matching(profile: UserProfile, query: str) -> int:
    """Bump last_referenced on facts that match the current query tokens."""
    q_tokens = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    if not q_tokens:
        return 0
    n = 0
    for f in profile.facts:
        blob = normalize_fact_key(f.fact)
        if sum(1 for t in q_tokens if t in blob) >= 1:
            f.last_referenced = datetime.now(UTC)
            f.reference_count = int(f.reference_count or 1) + 1
            f.confidence = min(0.99, f.confidence + 0.02)
            n += 1
    return n


def forget_facts(profile: UserProfile, query: str) -> list[UserFact]:
    """Remove facts matching query (substring). Returns removed facts."""
    q = normalize_fact_key(query)
    if not q:
        return []
    kept: list[UserFact] = []
    removed: list[UserFact] = []
    for f in profile.facts:
        if q in normalize_fact_key(f.fact) or normalize_fact_key(f.fact) in q:
            removed.append(f)
        else:
            kept.append(f)
    profile.facts = kept

    # Also drop matching traits by key or string value
    drop_keys = []
    for key, trait in profile.traits.items():
        val = str(trait.value)
        if q in normalize_fact_key(key) or q in normalize_fact_key(val):
            drop_keys.append(key)
    for key in drop_keys:
        profile.traits.pop(key, None)

    return removed


def _project_key(path: str | None) -> str:
    p = (path or "").strip().replace("\\", "/").lower().rstrip("/")
    return p


def rank_injectable_facts(
    profile: UserProfile,
    *,
    query: str = "",
    min_confidence: float = MIN_INJECT_CONFIDENCE,
    limit: int = 16,
    project_path: str | None = None,
) -> list[UserFact]:
    """Rank facts for injection: pin, project, confidence, reinforce, relevance."""
    q_tokens = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    proj = _project_key(project_path)
    scored: list[tuple[float, UserFact]] = []
    for f in profile.facts:
        # Pinned always eligible even if slightly low conf
        if not f.pinned and f.confidence < min_confidence:
            continue
        if looks_like_secret(f.fact):
            continue
        # Project-scoped: include global + matching project; skip other projects
        fproj = _project_key(getattr(f, "project_path", None))
        if fproj and proj and fproj not in proj and proj not in fproj:
            continue
        score = float(f.confidence) * 2.0
        score += min(1.5, 0.15 * float(f.reference_count or 1))
        if f.pinned:
            score += 2.0
        if fproj and proj and (fproj in proj or proj in fproj):
            score += 0.8
        # Recency boost (days decay soft)
        try:
            age = (datetime.now(UTC) - f.last_referenced).total_seconds() / 86400.0
            score += max(0.0, 1.0 - age / 30.0) * 0.5
            # Gentle effective demotion for very stale non-pinned (without mutating)
            if age > 60 and not f.pinned and (f.source or "") != "explicit":
                score -= 0.4
        except Exception:
            pass
        if q_tokens:
            blob = normalize_fact_key(f.fact)
            hits = sum(1 for t in q_tokens if t in blob)
            score += hits * 0.35
        # Explicit / heuristic prefs slightly preferred over vague
        if (f.source or "") in ("explicit", "heuristic", "user"):
            score += 0.25
        scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [f for _, f in scored[:limit]]


def token_overlap_score(query: str, text: str) -> float:
    """Cheap meaning-ish score (Phase 3 without native vectors)."""
    q = set(re.findall(r"[a-z0-9]{3,}", (query or "").lower()))
    t = set(re.findall(r"[a-z0-9]{3,}", (text or "").lower()))
    if not q or not t:
        return 0.0
    inter = len(q & t)
    return inter / max(1.0, len(q) ** 0.5 * len(t) ** 0.5)


def build_partner_memory_block(
    profile: UserProfile | None,
    *,
    query: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
    min_confidence: float = MIN_INJECT_CONFIDENCE,
    project_path: str | None = None,
) -> str:
    """Markdown block for system context. Empty if nothing trustworthy."""
    if profile is None:
        return ""

    lines: list[str] = []
    name = (profile.display_name or "").strip()
    if name:
        lines.append(f"- Call the user: {name}")

    for key, trait in list(profile.traits.items())[:MAX_HOT_TRAITS]:
        if trait.confidence < 0.4:
            continue
        lines.append(f"- {key}: {trait.value}")

    facts = rank_injectable_facts(
        profile,
        query=query,
        min_confidence=min_confidence,
        limit=MAX_HOT_FACTS,
        project_path=project_path,
    )
    for f in facts:
        pin = "📌 " if f.pinned else ""
        scope = ""
        if f.project_path:
            folder = f.project_path.replace("\\", "/").rstrip("/").split("/")[-1]
            scope = f" @{folder}" if folder else ""
        lines.append(f"- {pin}({f.category}{scope}) {f.fact}")

    if not lines:
        return ""

    header = "Partner memory (durable — trust across sessions; user can /forget):"
    body_lines = [header]
    used = len(header)
    for line in lines:
        # +1 for newline
        if used + len(line) + 1 > max_chars:
            break
        body_lines.append(line)
        used += len(line) + 1

    if len(body_lines) == 1:
        return ""
    if name:
        body_lines.append(f"Address the user as {name} when natural.")
    return "\n".join(body_lines)


def format_whoami(profile: UserProfile) -> str:
    """Friendly transparency listing for /whoami."""
    lines = ["**What I know about you**"]
    if profile.display_name:
        lines.append(f"- **Name:** {profile.display_name}")
    if profile.traits:
        lines.append("- **Traits:**")
        for key, trait in list(profile.traits.items())[:24]:
            conf = f" ({trait.confidence:.0%})" if trait.confidence < 0.95 else ""
            lines.append(f"  · {key}: {trait.value}{conf}")
    injectable = rank_injectable_facts(profile, min_confidence=0.0, limit=40)
    if injectable:
        lines.append("- **Facts** (used when helpful):")
        for f in injectable:
            conf = f"{f.confidence:.0%}"
            src = f.source or "unknown"
            pin = " pinned" if f.pinned else ""
            proj = ""
            if f.project_path:
                folder = f.project_path.replace("\\", "/").rstrip("/").split("/")[-1]
                proj = f" · project:{folder}" if folder else ""
            lines.append(f"  · [{conf} · {f.category} · {src}{pin}{proj}] {f.fact}")
    low = [
        f
        for f in profile.facts
        if f.confidence < MIN_INJECT_CONFIDENCE and f not in injectable and not f.pinned
    ]
    if low:
        lines.append("- **Not yet trusted** (need reinforce or clearer statement):")
        for f in low[:10]:
            lines.append(f"  · [{f.confidence:.0%}] {f.fact}")
    if len(lines) == 1:
        lines.append(
            "_Nothing stored yet. Tell me preferences in chat "
            "(e.g. “I prefer TypeScript”) or use_ `/remember …`_."
        )
    else:
        lines.append("")
        lines.append(
            "_Correct me anytime ·_ `/forget <text>` _removes a fact ·_ "
            "`/remember …` _adds one ·_ `/pin <text>` _keeps a fact always ready._"
        )
    return "\n".join(lines)


def pin_facts(profile: UserProfile, query: str, *, pinned: bool = True) -> list[UserFact]:
    """Pin or unpin facts matching query."""
    q = normalize_fact_key(query)
    if not q:
        return []
    touched: list[UserFact] = []
    for f in profile.facts:
        if q in normalize_fact_key(f.fact) or normalize_fact_key(f.fact) in q:
            f.pinned = pinned
            if pinned:
                f.confidence = max(f.confidence, 0.9)
            touched.append(f)
    return touched


async def distill_user_text(
    memory: Any,
    user_text: str,
    *,
    brief: Any | None = None,
    session_id: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Quiet distillation: heuristics (+ brief constraints). Never raises to caller path.

    Returns counts for diagnostics; safe for background threads with asyncio.run.
    """
    result: dict[str, Any] = {
        "added": 0,
        "reinforced": 0,
        "skipped": 0,
        "facts": [],
    }
    if memory is None:
        return result

    candidates = extract_heuristic_facts(user_text)
    candidates.extend(extract_from_brief(brief))

    profile = await memory.get_or_create_profile()
    name_changed = False

    for cand in candidates:
        if cand.source == "brief" and cand.confidence < 0.7:
            result["skipped"] += 1
            continue

        if cand.category == "identity":
            m = re.search(
                r"(?i)(?:my name is|call me|i(?:'m| am) called)\s+([A-Za-z][A-Za-z0-9_.\- ]{1,40})",
                cand.text,
            )
            if m:
                name = m.group(1).strip()
                if name and not looks_like_secret(name):
                    if profile.display_name != name[:60]:
                        profile.display_name = name[:60]
                        name_changed = True

        # High-confidence / explicit remember inject immediately
        accept_conf = cand.confidence
        force = cand.source == "explicit" or cand.confidence >= 0.95
        if not force and not should_auto_accept(cand):
            accept_conf = min(cand.confidence, MIN_INJECT_CONFIDENCE - 0.01)

        # Project-ish decisions/constraints get scoped when we have a project
        use_proj = project_path if cand.category in ("constraint", "workflow", "preference") else None
        uf, action = upsert_profile_fact(
            profile,
            cand.text,
            category=cand.category,
            confidence=accept_conf,
            source=cand.source,
            project_path=use_proj,
            force=force,
        )
        if action == "skipped" or uf is None:
            result["skipped"] += 1
            continue
        if action == "added":
            result["added"] += 1
            # Also land in entry store so memory_search FTS finds it
            if force or cand.source == "explicit":
                with __import__("contextlib").suppress(Exception):
                    from remedy.models import MemoryEntry, MemoryEntryType

                    if hasattr(memory, "upsert"):
                        import inspect

                        entry = MemoryEntry(
                            title=(cand.text[:80] or "Remembered"),
                            content=cand.text,
                            entry_type=MemoryEntryType.NOTE,
                            importance=0.85,
                        )
                        res = memory.upsert(entry)
                        if inspect.isawaitable(res):
                            # We're already async in distill_user_text
                            await res  # type: ignore[misc]
        else:
            result["reinforced"] += 1
        result["facts"].append(uf.fact)

    # Gentle decay once in a while on distill passes
    decayed = apply_gentle_decay(profile)
    result["decayed"] = decayed

    if result["added"] or result["reinforced"] or name_changed or decayed:
        await memory.save_user_profile(profile)
    _ = session_id  # reserved for future audit metadata
    return result


def distill_user_text_sync(
    memory: Any,
    user_text: str,
    *,
    brief: Any | None = None,
    session_id: str | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """Sync wrapper safe from both plain threads and a running asyncio loop.

    Never calls ``run_until_complete`` on a nested loop in the same thread
    (that fails under ReAct and was silently dropping remember-facts).
    """
    import asyncio
    import concurrent.futures

    async def _run() -> dict[str, Any]:
        return await distill_user_text(
            memory,
            user_text,
            brief=brief,
            session_id=session_id,
            project_path=project_path,
        )

    try:
        asyncio.get_running_loop()
        in_async = True
    except RuntimeError:
        in_async = False

    if not in_async:
        return asyncio.run(_run())

    # Running loop: own loop on a worker thread
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(_run())).result(timeout=15)


async def search_partner_and_entries(
    memory: Any,
    query: str,
    *,
    limit: int = 12,
    project_path: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid recall: profile facts + FTS entries, re-ranked by token overlap."""
    q = (query or "").strip()
    if not q or memory is None:
        return []
    results: list[dict[str, Any]] = []
    try:
        profile = await memory.get_or_create_profile()
        for f in rank_injectable_facts(
            profile, query=q, min_confidence=0.4, limit=limit, project_path=project_path
        ):
            score = 1.0 + token_overlap_score(q, f.fact) + (0.5 if f.pinned else 0.0)
            results.append(
                {
                    "kind": "fact",
                    "title": f.category,
                    "content": f.fact,
                    "score": score,
                    "confidence": f.confidence,
                }
            )
    except Exception:
        pass
    try:
        if hasattr(memory, "search"):
            hits = await memory.search(q, limit=limit)
            for h in hits or []:
                title = getattr(h, "title", "") or ""
                content = getattr(h, "content", "") or ""
                imp = float(getattr(h, "importance", 0.5) or 0.5)
                score = (
                    0.6
                    + token_overlap_score(q, f"{title} {content}")
                    + 0.3 * imp
                )
                results.append(
                    {
                        "kind": "entry",
                        "title": title,
                        "content": content[:400],
                        "score": score,
                        "importance": imp,
                    }
                )
    except Exception:
        pass
    results.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    return results[:limit]
