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

SOUL_DIRNAME = "soul"
FIELD_FILENAME = "field.json"
SCHEMA_VERSION = 1
MAX_EPISODES = 12
MAX_TENSIONS = 16
MAX_SELF_LESSONS = 24
MAX_VOICE_MARKERS = 12
MAX_OPEN_THREADS = 10
MAX_FUTURE_DREAMS = 8
DEFAULT_IDENTITY_VOW = (
    "I am one continuous partner on this machine — not a new instance per "
    "model, tab, or provider. Muscle changes; I stay."
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

    def line(self) -> str:
        bits = [self.arc.strip()] if self.arc.strip() else []
        if self.open_thread.strip():
            bits.append(f"open: {self.open_thread.strip()}")
        if self.user_stance.strip():
            bits.append(f"stance: {self.user_stance.strip()}")
        return " · ".join(bits)[:220]


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
    # Shared humor / register markers (short phrases)
    voice_markers: list[str] = field(default_factory=list)
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
        self.voice_markers = [v[:80] for v in self.voice_markers if v][:MAX_VOICE_MARKERS]
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

    def line(self) -> str:
        return f"[{self.outcome}] {self.lesson or self.summary}"[:200]


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
    # Future-facing partner dreams: how I will help them reach their goals
    future_dreams: list[str] = field(default_factory=list)
    updated_ts: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_ts = time.time()
        self.relational.clamp()
        self.episodes = self.episodes[-MAX_EPISODES:]
        self.organism_lessons = self.organism_lessons[-MAX_SELF_LESSONS:]
        self.self_habits = [h[:120] for h in self.self_habits if h][:16]
        self.pledges = [p[:160] for p in self.pledges if p][:12]
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
            episodes.append(
                EpisodeResidue(
                    id=str(e.get("id") or ""),
                    ts=float(e.get("ts") or time.time()),
                    arc=str(e.get("arc") or ""),
                    user_stance=str(e.get("user_stance") or ""),
                    open_thread=str(e.get("open_thread") or ""),
                    valence=float(e.get("valence") or 0.0),
                    muscle=str(e.get("muscle") or ""),
                    session_id=str(e.get("session_id") or ""),
                    project_hint=str(e.get("project_hint") or ""),
                )
            )
        lessons: list[OrganismLesson] = []
        for x in raw.get("organism_lessons") or []:
            if not isinstance(x, dict):
                continue
            lessons.append(
                OrganismLesson(
                    ts=float(x.get("ts") or time.time()),
                    outcome=str(x.get("outcome") or ""),
                    tree=str(x.get("tree") or ""),
                    summary=str(x.get("summary") or ""),
                    lesson=str(x.get("lesson") or ""),
                    round_id=str(x.get("round_id") or ""),
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
            future_dreams=list(raw.get("future_dreams") or []),
            updated_ts=float(raw.get("updated_ts") or time.time()),
        )
        sf.touch()
        return sf


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
        if path.is_file():
            with suppress(Exception):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raw = {}
        sf = SoulField.from_dict(raw)
        _cache[key] = sf
        return sf


def save_soul_field(field: SoulField, home: str | Path | None = None) -> Path:
    """Persist soul field atomically-ish (write temp + replace)."""
    field.touch()
    path = field_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
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
    r"(?i)(api[_-]?key|password\s*[:=]|sk-[a-z0-9]{10,}|bearer\s+[a-z0-9])"
)


def looks_like_secret_soul(text: str) -> bool:
    return bool(_SECRET_RE.search(text or ""))
