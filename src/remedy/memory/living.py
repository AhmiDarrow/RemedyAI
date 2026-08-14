"""Living organism memory — grow with the person, not just the repo.

Partner Memory already stores facts. This module is the *life* layer:

- Hear life, goals, tasks, taste, craft, and corrections — not only
  ``I prefer TypeScript``.
- Rank what matters for *this* turn (a sick kid, a ship deadline, a UI pass).
- Keep a chapter for the current project without letting work erase the person.

No extra model call. Cheap extractors + query-aware recall. The Soul Field
still carries dyadic residue; this carries *who they are becoming*.
"""

from __future__ import annotations

import re
from typing import Any

# Imported lazily in extract to avoid cycles at module import.

# --- turn kind --------------------------------------------------------------

_LIFE_RE = re.compile(
    r"(?i)\b("
    r"kids?|child(?:ren)?|wife|husband|partner|family|dog|cat|mom|dad|"
    r"health|sleep|tired|burn(?:ed)?\s*out|overwhelmed|anxious|therapy|"
    r"money|rent|bills?|vacation|birthday|funeral|wedding|pregnant|"
    r"my life|personal|home|tonight|this weekend"
    r")\b"
)
_GOAL_RE = re.compile(
    r"(?i)\b("
    r"goal|priority|this (?:week|month|year)|deadline|ship(?:ping)?|"
    r"launch|quit|habit|resolution|i want to (?:get|become|finish|start)"
    r")\b"
)
_DESIGN_RE = re.compile(
    r"(?i)\b("
    r"ui|ux|visual|spacing|typeface|font|palette|contrast|radius|"
    r"dark mode|light mode|layout|look(?:s|ing)?|taste|aesthetic|"
    r"brutalist|minimal|rounded|shadow|gradient|density"
    r")\b"
)
_CODE_RE = re.compile(
    r"(?i)\b("
    r"implement|refactor|pytest|ruff|gguf|commit|pr\b|type hints?|"
    r"file_write|bug|compile|function|class|module|repo|git\b"
    r")\b"
)
_WORK_RE = re.compile(
    r"(?i)\b("
    r"meeting|standup|review|ticket|sprint|client|ship it|deploy|"
    r"project|deadline|blocker"
    r")\b"
)


def turn_kind(query: str | None) -> str:
    """Coarse intent of this turn: life | goal | design | code | work | general."""
    t = (query or "").strip()
    if not t:
        return "general"
    if _LIFE_RE.search(t) and not _CODE_RE.search(t):
        return "life"
    if _DESIGN_RE.search(t):
        return "design"
    if _CODE_RE.search(t):
        return "code"
    if _GOAL_RE.search(t):
        return "goal"
    if _WORK_RE.search(t):
        return "work"
    return "general"


# --- extractors -------------------------------------------------------------

# (pattern, category, confidence)
# Capture group 0 / full match is stored when group 1 is too short.
_LIVING_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    # Life / relationships (global — never project-scoped)
    (
        re.compile(
            r"(?i)\b(?:i have|we've got|we have)\s+(?:a |an |two |three |\d+ )?"
            r"(kids?|children|son|daughter|dog|cat|wife|husband|partner)\b"
            r"([^.!?\n]{0,60})"
        ),
        "life",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:my (?:wife|husband|partner|kids?|son|daughter|dog|cat|family))\s+"
            r"([^.!?\n]{3,80})"
        ),
        "life",
        0.86,
    ),
    (
        re.compile(
            r"(?i)\b(?:i live in|i'?m (?:based|from)|timezone is|i work nights|"
            r"i work mornings|i'?m a night owl|i'?m an early bird)\s+"
            r"([^.!?\n]{0,60})"
        ),
        "life",
        0.88,
    ),
    (
        re.compile(
            r"(?i)\b(?:don'?t|do not|never)\s+(?:mention|bring up|talk about|ask about)\s+"
            r"([^.!?\n]{3,80})"
        ),
        "life",
        0.91,
    ),
    # Goals / life direction
    (
        re.compile(
            r"(?i)\b(?:my |our )?(?:life )?goal(?:s)? (?:is|are|this (?:week|month|year) is)\s+"
            r"([^.!?\n]{4,100})"
        ),
        "goal",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:i(?:'m| am) (?:trying to|working toward|saving (?:up )?for))\s+"
            r"([^.!?\n]{4,100})"
        ),
        "goal",
        0.87,
    ),
    (
        re.compile(
            r"(?i)\b(?:priority this (?:week|month)|this week i need to|"
            r"the most important thing is)\s+([^.!?\n]{4,100})"
        ),
        "goal",
        0.88,
    ),
    # How they want to be helped (organism manners)
    (
        re.compile(
            r"(?i)\b(?:be (?:blunt|brief|direct|quiet|warm|patient)|"
            r"no fluff|no theater|just works|don'?t lecture|"
            r"don'?t ask(?: me)?(?: to)? confirm|"
            r"just (?:do it|ship it)|work alone)\b"
            r"([^.!?\n]{0,40})"
        ),
        "preference",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:stop|don'?t) (?:being|sounding) so\s+"
            r"(verbose|cheerful|corporate|salesy|robotic|generic)\b"
        ),
        "correction",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:too )(generic|ai[- ]looking|verbose|cheerful|corporate|salesy|sloppy)\b"
        ),
        "correction",
        0.88,
    ),
    (
        re.compile(
            r"(?i)\b(?:i already (?:told|asked) you|as i(?: already)? said|like i said)\s+"
            r"([^.!?\n]{6,100})"
        ),
        "correction",
        0.86,
    ),
    # Craft / stack (work muscle — still life of a builder)
    (
        re.compile(
            r"(?i)\b(?:don'?t|do not|never)\s+(?:add|write|leave)\s+"
            r"(?:comments|docstrings|type:?ignore|todos?)\b"
            r"([^.!?\n]{0,40})"
        ),
        "craft",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:always|prefer|use)\s+(type hints?|ruff|pytest|uv |pnpm|yarn|"
            r"strict types|small functions|early returns)\b"
            r"([^.!?\n]{0,40})"
        ),
        "craft",
        0.88,
    ),
    (
        re.compile(
            r"(?i)\b(?:we use|this (?:repo|project|app) uses|stack is)\s+"
            r"([^.!?\n]{3,80})"
        ),
        "stack",
        0.86,
    ),
    # Design taste (one facet of the person)
    (
        re.compile(
            r"(?i)\b(?:no|never|don'?t use)\s+"
            r"(rounded corners|drop shadows|gradients?|inter font|comic sans|"
            r"purple gradients?|glassmorphism)\b"
        ),
        "design",
        0.9,
    ),
    (
        re.compile(
            r"(?i)\b(?:keep it|make it|prefer)\s+"
            r"(brutalist|minimal|dense|quiet|sharp|flat|high contrast)\b"
            r"([^.!?\n]{0,40})"
        ),
        "design",
        0.88,
    ),
    (
        re.compile(
            r"(?i)\b(?:8px|4px|12px|16px)\s+(?:spacing|grid|rhythm)\b"
            r"|prefer (?:inter|geist|sf pro|jetbrains)"
        ),
        "design",
        0.86,
    ),
]


_EPHEMERAL_RE = re.compile(
    r"(?i)\b(today|tonight|right now|this (?:morning|afternoon)|for now|real quick)\b"
)


def extract_living_facts(user_text: str) -> list[Any]:
    """Pull life / goal / craft / correction facts from a user line."""
    from remedy.memory.partner_memory import (
        ExtractedFact,
        is_stable_fact_text,
        looks_like_secret,
        normalize_fact_key,
    )

    text = (user_text or "").strip()
    if not text or looks_like_secret(text):
        return []
    found: list[Any] = []
    seen: set[str] = set()
    for pattern, category, conf in _LIVING_PATTERNS:
        for m in pattern.finditer(text):
            raw = re.sub(r"\s+", " ", m.group(0).strip())
            if _EPHEMERAL_RE.search(raw) and category in ("life", "goal"):
                # Mood of the hour is not organism memory
                continue
            if not is_stable_fact_text(raw, explicit=conf >= 0.9):
                continue
            key = normalize_fact_key(raw)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                ExtractedFact(
                    text=raw[:280],
                    category=category,
                    confidence=conf,
                    source="living",
                )
            )
            if len(found) >= 6:
                return found
    return found


def category_boost(category: str, kind: str) -> float:
    """Extra rank when this turn is about that part of their life."""
    cat = (category or "").lower()
    if kind == "life" and cat in ("life", "personal", "identity", "goal"):
        return 0.85
    if kind == "goal" and cat in ("goal", "life", "constraint"):
        return 0.75
    if kind == "design" and cat in ("design", "preference", "correction"):
        return 0.7
    if kind == "code" and cat in ("craft", "stack", "constraint", "workflow"):
        return 0.65
    if kind == "work" and cat in ("goal", "constraint", "preference", "workflow"):
        return 0.45
    # Identity / life always a little hot — the organism is the person
    if cat in ("identity", "life") and kind != "code":
        return 0.25
    return 0.0


def bucket_for(category: str, *, project_scoped: bool) -> str:
    if project_scoped:
        return "chapter"
    cat = (category or "").lower()
    if cat in ("identity", "personal"):
        return "who"
    if cat in ("life", "goal"):
        return "life"
    if cat in (
        "preference",
        "craft",
        "constraint",
        "stack",
        "design",
        "correction",
        "workflow",
    ):
        return "work"
    return "work"


_SECTION_TITLES = {
    "who": "Who you are",
    "life": "Life & goals",
    "work": "How we work together",
    "chapter": "This chapter",
}


def format_living_sections(
    lines_by_bucket: dict[str, list[str]],
    *,
    name: str = "",
    project_label: str = "",
    header: str = "",
    max_chars: int = 1600,
    recalled: list[str] | None = None,
) -> str:
    """Assemble the organism inject. Empty string if nothing trustworthy."""
    hdr = header or (
        "Partner memory (grows with you — same organism across sessions; "
        "user can /forget):"
    )
    body: list[str] = [hdr]
    used = len(hdr)
    if name.strip():
        line = f"- Call the user: {name.strip()}"
        body.append(line)
        used += len(line) + 1

    order = ("who", "life", "work", "chapter")
    for key in order:
        items = lines_by_bucket.get(key) or []
        if not items:
            continue
        title = _SECTION_TITLES[key]
        if key == "chapter" and project_label:
            title = f"This chapter ({project_label})"
        title_line = f"{title}:"
        if used + len(title_line) + 8 > max_chars:
            break
        body.append(title_line)
        used += len(title_line) + 1
        for line in items:
            if used + len(line) + 1 > max_chars:
                break
            body.append(line)
            used += len(line) + 1

    if recalled:
        title_line = "Recalled for this turn:"
        if used + len(title_line) + 20 <= max_chars:
            body.append(title_line)
            used += len(title_line) + 1
            for line in recalled:
                if used + len(line) + 1 > max_chars:
                    break
                body.append(line)
                used += len(line) + 1

    if name.strip() and used + 40 < max_chars:
        addr = f"Address the user as {name.strip()} when natural."
        body.append(addr)

    if len(body) <= 1:
        return ""
    # Need more than just the header
    if len(body) == 2 and body[1].startswith("- Call the user"):
        body.append(f"Address the user as {name.strip()} when natural.")
    return "\n".join(body)


def format_turn_recall(
    hits: list[dict[str, Any]] | None,
    *,
    already: set[str] | None = None,
    max_items: int = 3,
    max_chars: int = 360,
) -> list[str]:
    """Lines for 'Recalled for this turn' — skip facts already in the hot block."""
    if not hits:
        return []
    have = {re.sub(r"\s+", " ", (a or "").strip().lower())[:80] for a in (already or set())}
    lines: list[str] = []
    used = 0
    for h in hits:
        if not isinstance(h, dict):
            continue
        text = str(h.get("content") or h.get("title") or "").strip()
        if len(text) < 8:
            continue
        key = re.sub(r"\s+", " ", text.lower())[:80]
        if key in have:
            continue
        have.add(key)
        kind = str(h.get("kind") or "memory")
        line = f"- ({kind}) {text[:180]}"
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
        if len(lines) >= max_items:
            break
    return lines


def project_label(path: str | None) -> str:
    if not path:
        return ""
    return path.replace("\\", "/").rstrip("/").split("/")[-1] or ""


def life_goal_lines(profile: Any, *, limit: int = 4, home_dir: Any = None) -> list[str]:
    """Short life/goal facts for continuity / organism pulse (not the full dump)."""
    out: list[str] = []
    try:
        from remedy.memory.life_goals import LifeGoalStore

        store = LifeGoalStore(home_dir)
        for g in store.list()[:limit]:
            line = g.title
            if g.next_action:
                line = f"{g.title} — next: {g.next_action}"
            out.append(line[:160])
        last = store.last_step()
        if last and last.get("did") and len(out) < limit:
            out.append(f"Last I did: {last['did']}"[:160])
    except Exception:
        pass
    if profile is None:
        return out[:limit]
    facts = list(getattr(profile, "facts", None) or [])
    for f in facts:
        cat = str(getattr(f, "category", "") or "").lower()
        if cat not in ("life", "goal"):
            continue
        text = str(getattr(f, "fact", "") or "").strip()
        if len(text) < 6:
            continue
        try:
            if float(getattr(f, "confidence", 0) or 0) < 0.55:
                continue
        except (TypeError, ValueError):
            continue
        out.append(text[:160])
        if len(out) >= limit:
            break
    return out[:limit]


def whoami_sections(facts: list[Any]) -> dict[str, list[Any]]:
    """Bucket profile facts for /whoami (same map as inject)."""
    buckets: dict[str, list[Any]] = {
        "who": [],
        "life": [],
        "work": [],
        "chapter": [],
    }
    for f in facts:
        cat = str(getattr(f, "category", "") or "general")
        scoped = bool(getattr(f, "project_path", None))
        buckets.setdefault(bucket_for(cat, project_scoped=scoped), []).append(f)
    return buckets
