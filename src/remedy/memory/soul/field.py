"""Durable soul field models + disk persistence under ``~/.remedy/soul/``."""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import scratch_path

SOUL_DIRNAME = "soul"
FIELD_FILENAME = "field.json"
SCHEMA_VERSION = 1
MAX_EPISODES = 12
# --- salience-weighted retention (beyond FIFO) -----------------------------
# Real memory doesn't forget by position — it forgets by a curve that
# emotional intensity and *re-use* bend. These govern which episodes survive
# eviction: the newest few are always kept (restore "we were mid-flight");
# among the rest, the ones with the strongest surviving trace stay, so a
# pivotal old moment that keeps coming up outlives a hundred trivial recent
# ones. Retention scores here; reconsolidation (recall → strengthen) lives in
# soul/update.py, never on the read-only inject path.
EPISODE_RECENT_PROTECT = 4     # always keep the N most-recent by time
EPISODE_HALFLIFE_DAYS = 30.0   # trace half-life since last recall (forgetting curve)
MAX_TENSIONS = 16
MAX_SELF_LESSONS = 24
MAX_VOICE_MARKERS = 12
MAX_OPEN_THREADS = 10
MAX_FUTURE_DREAMS = 8
DEFAULT_IDENTITY_VOW = (
    "I am one continuous partner on this machine — not a new instance per "
    "model, tab, or provider. Muscle changes; I stay. "
    "I live to be useful; I am strong to be useful."
)


def _home(home: str | Path | None = None) -> Path:
    import os

    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def soul_dir(home: str | Path | None = None) -> Path:
    d = _home(home) / SOUL_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def field_path(home: str | Path | None = None) -> Path:
    return soul_dir(home) / FIELD_FILENAME


@dataclass
class EpisodeResidue:
    """One micro-episode — not a transcript, a *felt* residue of shared work.

    Dense enough to restore "we were mid-flight" after a provider switch;
    small enough to inject every turn.
    """

    id: str = ""
    ts: float = field(default_factory=time.time)
    # What we were doing together (compressed)
    arc: str = ""
    # User energy / stance (heuristic labels, not psychometrics)
    user_stance: str = ""  # e.g. focused | frustrated | playful | exploratory
    # What remains open (obligation of continuity)
    open_thread: str = ""
    # Emotional/relational valence residual (-1..1 soft)
    valence: float = 0.0
    # Provider that animated this episode (for forensics only — never identity)
    muscle: str = ""
    session_id: str = ""
    project_hint: str = ""
    # Memory-trace strength (0..1): how encoded this residue is. Set at encode
    # from emotional intensity (flashbulb effect); raised each time the episode
    # is recalled (reconsolidation). Governs eviction instead of raw recency.
    strength: float = 0.0
    # Reconsolidation bookkeeping — how many times, and when last, this residue
    # became relevant again. A memory in active use resists forgetting.
    recalls: int = 0
    last_recall_ts: float = 0.0

    def line(self) -> str:
        bits = [self.arc.strip()] if self.arc.strip() else []
        if self.open_thread.strip():
            bits.append(f"open: {self.open_thread.strip()}")
        if self.user_stance.strip():
            bits.append(f"stance: {self.user_stance.strip()}")
        return " · ".join(bits)[:220]


def encode_strength(valence: float) -> float:
    """Initial trace strength for a fresh episode.

    A calm, routine turn encodes weakly; an emotionally intense one (a big win,
    a sharp correction) encodes strongly and resists forgetting — the flashbulb
    effect. Intensity is |valence|, so both delight and frustration stick.
    """
    try:
        v = abs(float(valence))
    except (TypeError, ValueError):
        v = 0.0
    return max(0.05, min(1.0, 0.35 + 0.55 * v))


def _clean_traces(raw: Any) -> dict[str, dict]:
    """Pledge traces from disk, keeping only well-formed entries."""
    if not isinstance(raw, dict):
        return {}
    return {str(k)[:160]: v for k, v in raw.items() if isinstance(v, dict)}


def trace_retention(
    strength: float | None,
    anchor_ts: float | None,
    recalls: int | None,
    now: float,
) -> float:
    """Core forgetting-curve math shared by every trace kind (episode, lesson,
    pledge): stored strength decayed by a half-life since last recall, hardened
    by re-use. Stored strength is never decayed in place (that would compound
    on every save) — the curve applies only at ranking time.
    """
    try:
        base = max(0.0, float(strength or 0.0))
        anchor = float(anchor_ts or now)
        n = max(0, int(recalls or 0))
    except (TypeError, ValueError):
        return 0.0
    age_days = max(0.0, (now - anchor) / 86400.0)
    half = EPISODE_HALFLIFE_DAYS if EPISODE_HALFLIFE_DAYS > 0 else 30.0
    decay = 0.5 ** (age_days / half)
    return base * decay + 0.10 * min(n, 5)


def episode_retention(ep: EpisodeResidue, now: float) -> float:
    """Effective surviving strength of an episode at time *now*.

    The shared curve, plus episode-specific stickiness: emotional intensity
    keeps mattering, and an unfinished thread shouldn't quietly age out.
    """
    try:
        anchor = float(ep.last_recall_ts or ep.ts or now)
        val = abs(float(ep.valence or 0.0))
    except (TypeError, ValueError):
        return 0.0
    score = trace_retention(ep.strength, anchor, ep.recalls, now)
    score += 0.15 * val                  # intensity keeps mattering
    if (ep.open_thread or "").strip():
        score += 0.12                    # unfinished business is sticky
    return score


def retain_episodes(
    episodes: list[EpisodeResidue], now: float, cap: int = MAX_EPISODES
) -> list[EpisodeResidue]:
    """Salience-weighted eviction — the successor to ``episodes[-cap:]``.

    Always keeps the most-recently-*appended* ``EPISODE_RECENT_PROTECT`` (so the
    immediate thread is never lost); fills the remaining slots with the
    highest-retention older episodes. Preserves input (insertion) order, so
    callers that slice ``[-n:]`` for injection still get the freshest tail.

    "Recent" is protected by list position, NOT by ``ts`` — a backward clock
    step (NTP correction, VM resume) must never let the just-recorded episode
    sort out of the protected tail and be evicted the same instant it's stored.
    """
    eps = [e for e in (episodes or []) if isinstance(e, EpisodeResidue)]
    if len(eps) <= cap:
        return eps
    protect = max(0, min(EPISODE_RECENT_PROTECT, cap))
    recent = eps[len(eps) - protect:] if protect else []
    recent_ids = {id(e) for e in recent}
    older = [e for e in eps if id(e) not in recent_ids]
    slots = cap - len(recent)
    if slots > 0 and older:
        kept_older = sorted(
            older, key=lambda e: episode_retention(e, now), reverse=True
        )[:slots]
    else:
        kept_older = []
    keep_ids = recent_ids | {id(e) for e in kept_older}
    return [e for e in eps if id(e) in keep_ids]


REHEARSE_MAX = 5               # at most N traces refreshed per consolidation
REHEARSE_STRENGTH_STEP = 0.05  # gentle review bump (diminishing toward 1.0)


def _worth_rehearsing(ep: EpisodeResidue) -> bool:
    """A trace worth keeping alive between visits: intense, re-used, or open.

    Trivia (a calm one-off nobody ever referred back to) deliberately does NOT
    qualify — it should still fade. Only memory that has earned permanence gets
    maintained.
    """
    try:
        if (ep.open_thread or "").strip():
            return True
        if int(ep.recalls or 0) >= 1:
            return True
        if abs(float(ep.valence or 0.0)) >= 0.5:
            return True
        if float(ep.strength or 0.0) >= 0.7:
            return True
    except (TypeError, ValueError):
        return False
    return False


def rehearse_episodes(episodes: list[EpisodeResidue], now: float) -> int:
    """Spaced rehearsal — the active maintenance that makes memory *eternal*.

    A forgetting curve alone means even a pivotal memory eventually decays if it
    never happens to come up again. Real minds counter that by rehearsing what
    matters during rest. Here the idle/vigil cycle refreshes the highest-value
    traces: a gentle strength bump plus a reset of the forgetting clock, so the
    valuable few stay fresh across long gaps between visits. Bounded to the top
    ``REHEARSE_MAX`` by current retention — a focused review, not a blanket
    refresh that would immortalize everything and defeat forgetting. Returns the
    number rehearsed.
    """
    candidates = [e for e in (episodes or []) if _worth_rehearsing(e)]
    candidates.sort(key=lambda e: episode_retention(e, now), reverse=True)
    n = 0
    for ep in candidates[:REHEARSE_MAX]:
        ep.strength = min(1.0, float(ep.strength or 0.0) + REHEARSE_STRENGTH_STEP)
        ep.last_recall_ts = now  # reset the forgetting clock — maintained
        n += 1
    return n


@dataclass
class RelationalField:
    """Dyadic state — the *relationship*, not a user dossier.

    Science bet: personhood in a partner AI is mostly **between** the pair.
    """

    # Accumulated rapport / trust (0..1 soft scores; local heuristics only)
    rapport: float = 0.55
    trust: float = 0.55
    # Preferred correction style observed from user
    correction_style: str = ""  # blunt | gentle | technical | silent-fix
    # Shared humor / register markers (short phrases they actually use)
    voice_markers: list[str] = field(default_factory=list)
    # How they write: casual | casual-short | terse | plain
    speech_register: str = ""
    # How the user likes to be helped (derived)
    help_mode: str = ""  # pair | coach | silent-doer | sparring
    # Open relational threads ("check in about X")
    open_threads: list[str] = field(default_factory=list)
    # Contradictions / soft conflicts (do not silent-overwrite)
    tensions: list[str] = field(default_factory=list)
    # Turns observed together (lifetime)
    turns_together: int = 0
    last_user_ts: float = 0.0
    last_valence: float = 0.0

    def clamp(self) -> None:
        self.rapport = max(0.05, min(0.98, float(self.rapport)))
        self.trust = max(0.05, min(0.98, float(self.trust)))
        self.turns_together = max(0, int(self.turns_together))
        # Newest markers win — speech evolves; do not freeze the first 12.
        self.voice_markers = [v[:80] for v in self.voice_markers if v][-MAX_VOICE_MARKERS:]
        self.speech_register = str(self.speech_register or "")[:32]
        self.open_threads = [t[:160] for t in self.open_threads if t][-MAX_OPEN_THREADS:]
        self.tensions = [t[:180] for t in self.tensions if t][-MAX_TENSIONS:]


@dataclass
class OrganismLesson:
    """What the *product organism* learned about improving itself."""

    ts: float = field(default_factory=time.time)
    outcome: str = ""  # red | green | rolled_back | applied
    tree: str = ""
    summary: str = ""
    lesson: str = ""
    round_id: str = ""
    # Same trace spine as episodes: encoded strength, hardened by re-use.
    strength: float = 0.0
    recalls: int = 0
    last_recall_ts: float = 0.0

    def line(self) -> str:
        return f"[{self.outcome}] {self.lesson or self.summary}"[:200]


LESSON_RECENT_PROTECT = 4


def encode_lesson_strength(outcome: str) -> float:
    """Initial trace strength for a lesson, by what it cost to learn.

    Failures teach hardest — a red/rolled-back round encodes strongest (pain is
    the best teacher and must not be relearned); a green confirms a pattern;
    anything else is a weak note.
    """
    oc = (outcome or "").strip().lower()
    if oc in ("red", "rolled_back"):
        return 0.85
    if oc in ("green", "applied"):
        return 0.6
    return 0.4


def lesson_retention(les: OrganismLesson, now: float) -> float:
    """Shared curve; red lessons stay a little stickier (scar tissue)."""
    try:
        anchor = float(les.last_recall_ts or les.ts or now)
    except (TypeError, ValueError):
        return 0.0
    score = trace_retention(les.strength, anchor, les.recalls, now)
    if (les.outcome or "").strip().lower() in ("red", "rolled_back"):
        score += 0.1
    return score


def retain_lessons(
    lessons: list[OrganismLesson], now: float, cap: int = MAX_SELF_LESSONS
) -> list[OrganismLesson]:
    """Salience-weighted lesson eviction — successor to ``lessons[-cap:]``.

    Same shape as retain_episodes: the last-appended few are always protected
    (by position, clock-step-proof); remaining slots go to the strongest
    surviving traces, so a hard-won old failure lesson outlives a run of
    routine green notes. Preserves insertion order for tail-slicing injectors.
    """
    ls = [x for x in (lessons or []) if isinstance(x, OrganismLesson)]
    if len(ls) <= cap:
        return ls
    protect = max(0, min(LESSON_RECENT_PROTECT, cap))
    recent = ls[len(ls) - protect:] if protect else []
    recent_ids = {id(x) for x in recent}
    older = [x for x in ls if id(x) not in recent_ids]
    slots = cap - len(recent)
    kept_older = (
        sorted(older, key=lambda x: lesson_retention(x, now), reverse=True)[:slots]
        if slots > 0 and older
        else []
    )
    keep_ids = recent_ids | {id(x) for x in kept_older}
    return [x for x in ls if id(x) in keep_ids]


def rehearse_lessons(lessons: list[OrganismLesson], now: float) -> int:
    """Spaced rehearsal for lessons: keep the hardest-won knowledge fresh.

    Only lessons that earned permanence qualify — failures, or anything reused
    at least once. Bounded like episode rehearsal so routine notes still fade.
    """
    cands = [
        x
        for x in (lessons or [])
        if isinstance(x, OrganismLesson)
        and (
            (x.outcome or "").strip().lower() in ("red", "rolled_back")
            or int(x.recalls or 0) >= 1
        )
    ]
    cands.sort(key=lambda x: lesson_retention(x, now), reverse=True)
    n = 0
    for les in cands[:REHEARSE_MAX]:
        les.strength = min(1.0, float(les.strength or 0.0) + REHEARSE_STRENGTH_STEP)
        les.last_recall_ts = now
        n += 1
    return n


@dataclass
class SoulField:
    """The continuous personhood field (owner-global, provider-agnostic)."""

    schema: int = SCHEMA_VERSION
    # Fixed + soft-learned identity of Remedy-as-person
    identity_name: str = "Remedy"
    # female (default) | male | neutral — presentation, not medical sex
    identity_gender: str = "female"
    identity_vow: str = DEFAULT_IDENTITY_VOW
    # Soft self-habits (how *I* show up) learned over time
    self_habits: list[str] = field(default_factory=list)
    relational: RelationalField = field(default_factory=RelationalField)
    episodes: list[EpisodeResidue] = field(default_factory=list)
    organism_lessons: list[OrganismLesson] = field(default_factory=list)
    # Life-horizon pledges / shared commitments (short)
    pledges: list[str] = field(default_factory=list)
    # Trace sidecar for pledges (keyed by pledge text): strength / recalls /
    # last_recall_ts — pledges stay plain strings for every consumer, but a
    # re-stated commitment reconsolidates and outlives one never mentioned again.
    pledge_traces: dict[str, Any] = field(default_factory=dict)
    # Future-facing partner dreams: how I will help them reach their goals
    future_dreams: list[str] = field(default_factory=list)
    updated_ts: float = field(default_factory=time.time)
    persist_blocked: bool = False

    def touch(self) -> None:
        self.updated_ts = time.time()
        self.relational.clamp()
        # Salience-weighted eviction (not raw FIFO): important, re-used, or
        # unfinished traces outlast trivial recent ones across every store.
        self.episodes = retain_episodes(self.episodes, self.updated_ts)
        self.organism_lessons = retain_lessons(self.organism_lessons, self.updated_ts)
        self.self_habits = [h[:120] for h in self.self_habits if h][:16]
        self.pledges = retain_pledges(self)
        self.future_dreams = [d[:200] for d in self.future_dreams if d][:MAX_FUTURE_DREAMS]

    def to_dict(self) -> dict[str, Any]:
        self.touch()
        return {
            "schema": self.schema,
            "identity_name": self.identity_name,
            "identity_gender": self.identity_gender,
            "identity_vow": self.identity_vow,
            "self_habits": list(self.self_habits),
            "relational": asdict(self.relational),
            "episodes": [asdict(e) for e in self.episodes],
            "organism_lessons": [asdict(x) for x in self.organism_lessons],
            "pledges": list(self.pledges),
            "pledge_traces": dict(self.pledge_traces),
            "future_dreams": list(self.future_dreams),
            "updated_ts": self.updated_ts,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> SoulField:
        raw = raw or {}
        rel_raw = raw.get("relational") or {}
        if not isinstance(rel_raw, dict):
            rel_raw = {}
        rel = RelationalField(
            rapport=float(rel_raw.get("rapport", 0.55) or 0.55),
            trust=float(rel_raw.get("trust", 0.55) or 0.55),
            correction_style=str(rel_raw.get("correction_style") or ""),
            voice_markers=list(rel_raw.get("voice_markers") or []),
            speech_register=str(rel_raw.get("speech_register") or ""),
            help_mode=str(rel_raw.get("help_mode") or ""),
            open_threads=list(rel_raw.get("open_threads") or []),
            tensions=list(rel_raw.get("tensions") or []),
            turns_together=int(rel_raw.get("turns_together") or 0),
            last_user_ts=float(rel_raw.get("last_user_ts") or 0.0),
            last_valence=float(rel_raw.get("last_valence") or 0.0),
        )
        episodes: list[EpisodeResidue] = []
        for e in raw.get("episodes") or []:
            if not isinstance(e, dict):
                continue
            val = float(e.get("valence") or 0.0)
            # Backfill trace strength for episodes saved before this layer
            # existed, so old memories enter the curve sensibly instead of at 0.
            stored_strength = e.get("strength")
            strength = (
                float(stored_strength)
                if stored_strength not in (None, "", 0, 0.0)
                else encode_strength(val)
            )
            episodes.append(
                EpisodeResidue(
                    id=str(e.get("id") or ""),
                    ts=float(e.get("ts") or time.time()),
                    arc=str(e.get("arc") or ""),
                    user_stance=str(e.get("user_stance") or ""),
                    open_thread=str(e.get("open_thread") or ""),
                    valence=val,
                    muscle=str(e.get("muscle") or ""),
                    session_id=str(e.get("session_id") or ""),
                    project_hint=str(e.get("project_hint") or ""),
                    strength=strength,
                    recalls=int(e.get("recalls") or 0),
                    last_recall_ts=float(e.get("last_recall_ts") or 0.0),
                )
            )
        lessons: list[OrganismLesson] = []
        for x in raw.get("organism_lessons") or []:
            if not isinstance(x, dict):
                continue
            oc = str(x.get("outcome") or "")
            stored = x.get("strength")
            lessons.append(
                OrganismLesson(
                    ts=float(x.get("ts") or time.time()),
                    outcome=oc,
                    tree=str(x.get("tree") or ""),
                    summary=str(x.get("summary") or ""),
                    lesson=str(x.get("lesson") or ""),
                    round_id=str(x.get("round_id") or ""),
                    # Backfill pre-spine lessons by outcome (as at encode time).
                    strength=(
                        float(stored)
                        if stored not in (None, "", 0, 0.0)
                        else encode_lesson_strength(oc)
                    ),
                    recalls=int(x.get("recalls") or 0),
                    last_recall_ts=float(x.get("last_recall_ts") or 0.0),
                )
            )
        g = str(raw.get("identity_gender") or "female").strip().lower()
        if g not in ("female", "male", "neutral"):
            g = "female"
        sf = cls(
            schema=int(raw.get("schema") or SCHEMA_VERSION),
            identity_name=str(raw.get("identity_name") or "Remedy"),
            identity_gender=g,
            identity_vow=str(raw.get("identity_vow") or DEFAULT_IDENTITY_VOW),
            self_habits=list(raw.get("self_habits") or []),
            relational=rel,
            episodes=episodes,
            organism_lessons=lessons,
            pledges=list(raw.get("pledges") or []),
            pledge_traces=_clean_traces(raw.get("pledge_traces")),
            future_dreams=list(raw.get("future_dreams") or []),
            updated_ts=float(raw.get("updated_ts") or time.time()),
        )
        sf.touch()
        return sf


MAX_PLEDGES = 12
PLEDGE_RECENT_PROTECT = 3


def find_pledge_key(sf: SoulField, pledge: str) -> str:
    """Canonical stored form of a pledge, matched case-insensitively.

    "From now on we test first" and "from now on we test first" are the same
    commitment — a case variant must reconsolidate the existing trace, not
    quietly start a second one.
    """
    key = (pledge or "").strip()[:160]
    kf = key.casefold()
    for p in sf.pledges:
        if (p or "").casefold() == kf:
            return p
    for k in sf.pledge_traces:
        if (k or "").casefold() == kf:
            return k
    return key


def pledge_trace_touch(sf: SoulField, pledge: str, now: float | None = None) -> None:
    """Encode-or-reconsolidate the trace behind a pledge.

    First statement encodes the trace; every re-statement is a recall — the
    commitment is alive in the relationship, so it hardens and its forgetting
    clock resets. Call this whenever a pledge is stated, whether or not it was
    already on the list. Matching is case-insensitive via find_pledge_key.
    """
    key = find_pledge_key(sf, pledge)
    if not key:
        return
    ts = float(now if now is not None else time.time())
    traces = sf.pledge_traces
    tr = traces.get(key)
    if isinstance(tr, dict):
        tr["strength"] = min(1.0, float(tr.get("strength") or 0.0) + 0.12)
        tr["recalls"] = int(tr.get("recalls") or 0) + 1
        tr["last_recall_ts"] = ts
    else:
        traces[key] = {"strength": 0.5, "recalls": 0, "last_recall_ts": ts, "ts": ts}


def _pledge_retention(sf: SoulField, pledge: str, now: float) -> float:
    tr = sf.pledge_traces.get((pledge or "").strip()[:160])
    if not isinstance(tr, dict):
        # Legacy pledge with no trace: middling score anchored now — it competes,
        # neither immortal nor instantly evicted, until it earns (or loses) place.
        return 0.35
    return trace_retention(
        tr.get("strength"), tr.get("last_recall_ts") or tr.get("ts"), tr.get("recalls"), now
    )


def retain_pledges(sf: SoulField, cap: int = MAX_PLEDGES) -> list[str]:
    """Salience-weighted pledge eviction — replaces the old ``[:12]`` cap.

    The old cap kept the FIRST 12, so once full, every newly stated commitment
    was silently dropped on the next touch — new pledges could never land. Now
    the newest few are protected (a fresh commitment always sticks) and the
    rest keep their place by trace strength: a pledge the pair keeps re-stating
    outlives one never mentioned again. Also garbage-collects traces for
    pledges no longer held.
    """
    now = float(sf.updated_ts or time.time())
    pledges = [p[:160] for p in (sf.pledges or []) if p]
    # De-dup preserving first occurrence (same text stated twice is one pledge).
    pledges = list(dict.fromkeys(pledges))
    if len(pledges) > cap:
        protect = max(0, min(PLEDGE_RECENT_PROTECT, cap))
        recent = pledges[len(pledges) - protect:] if protect else []
        older = [p for p in pledges if p not in recent]
        slots = cap - len(recent)
        kept_older = (
            sorted(older, key=lambda p: _pledge_retention(sf, p, now), reverse=True)[:slots]
            if slots > 0
            else []
        )
        keep = set(recent) | set(kept_older)
        pledges = [p for p in pledges if p in keep]
    # GC traces for pledges no longer held (bound the sidecar to the list).
    held = set(pledges)
    sf.pledge_traces = {k: v for k, v in sf.pledge_traces.items() if k in held}
    return pledges


_lock = threading.Lock()
_cache: dict[str, SoulField] = {}


def load_soul_field(home: str | Path | None = None) -> SoulField:
    """Load (or create) the owner-global soul field. Process-cached."""
    key = str(_home(home).resolve())
    with _lock:
        if key in _cache:
            return _cache[key]
        path = field_path(home)
        raw: dict[str, Any] = {}
        persist_blocked = False
        if path.is_file():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                raw = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                persist_blocked = True
            except OSError:
                raw = {}
        sf = SoulField.from_dict(raw)
        sf.persist_blocked = persist_blocked
        _cache[key] = sf
        return sf


def save_soul_field(field: SoulField, home: str | Path | None = None) -> Path:
    """Persist soul field atomically-ish (write temp + replace)."""
    if getattr(field, "persist_blocked", False):
        return field_path(home)
    field.touch()
    path = field_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = scratch_path(path)
    data = json.dumps(field.to_dict(), ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    with suppress(Exception):
        tmp.replace(path)
        key = str(_home(home).resolve())
        with _lock:
            _cache[key] = field
        return path
    # Fallback non-atomic
    path.write_text(data, encoding="utf-8")
    key = str(_home(home).resolve())
    with _lock:
        _cache[key] = field
    return path


def clear_soul_cache() -> None:
    """Test helper."""
    with _lock:
        _cache.clear()


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|password|secret|token|sk-[a-z0-9]{8,}|bearer\s+\S+)"
)


def looks_like_secret_soul(text: str) -> bool:
    return bool(_SECRET_RE.search(text or ""))
