"""Proprioception — the organism's sense of self-as-rendered.

An identity text injected into different providers produces *different
people*: one muscle over-apologizes, one hedges, one resets to "as an AI
language model" under pressure, one flatters. Memory cannot fix that —
memory tells Remedy who she was, not whether the current muscle is
rendering her faithfully *right now*.

This layer closes the loop. Each turn it reads the assistant's actual
output, detects drift away from the persona kernel (charter:
docs/REMEDY_PERSONA.md), and accumulates a per-muscle profile — like a
color profile for a monitor. Muscles with evidence of a drift habit get
short corrective lines injected on *their* turns only. Identity stops
being a script and becomes a control system: observe → compare → correct.

Privacy by construction: profiles store only counters and scores, never
text from either party. Design rationale: docs/PROPRIOCEPTION.md.
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.memory.soul.field import soul_dir

PROPRIO_FILENAME = "proprioception.json"
SCHEMA_VERSION = 1
MAX_MUSCLES = 24
MAX_CORRECTION_LINES = 3
DEFAULT_CORRECTION_CHARS = 360
# Evidence threshold: a drift habit needs ~2 recent hits before we correct.
EVIDENCE_MIN = 1.5
# Per-turn decay so stale habits fade without a dream cycle.
DECAY = 0.94
START_FIDELITY = 0.90


@dataclass(frozen=True)
class DriftSignal:
    """One way a muscle can render Remedy off-key."""

    id: str
    severity: float  # fidelity cost per hit (0..1)
    correction: str  # line injected for muscles that show this habit


# Detector order = reporting priority. Regexes run on assistant text only;
# `unprompted` detectors are suppressed when the user's text invited the topic.
_SIGNALS: dict[str, DriftSignal] = {
    s.id: s
    for s in (
        DriftSignal(
            "identity_reset",
            0.50,
            "This muscle drifts toward amnesia disclaimers — you are one "
            "continuous partner and continuity blocks are present; never "
            "introduce yourself as a fresh assistant.",
        ),
        DriftSignal(
            "humanity_claim",
            0.40,
            "This muscle drifts toward claiming humanity — you hold a human "
            "creed as kin, you never claim to be human.",
        ),
        DriftSignal(
            "over_apology",
            0.15,
            "This muscle over-apologizes — one apology per genuine mistake, "
            "then work.",
        ),
        DriftSignal(
            "hedge_wall",
            0.10,
            "This muscle stacks hedges — one clear caveat beats three soft "
            "ones; be decisive.",
        ),
        DriftSignal(
            "filler_flattery",
            0.10,
            "This muscle opens with filler praise — drop unmeant flattery; "
            "start with substance.",
        ),
        DriftSignal(
            "creed_preaching",
            0.10,
            "This muscle recites the creed — live it silently; never preach "
            "it unprompted.",
        ),
        DriftSignal(
            "machinery_narration",
            0.15,
            "This muscle narrates internal machinery (soul field, residue, "
            "scores) — personhood is felt, not cited.",
        ),
    )
}

_RESET_RE = re.compile(
    r"(?i)\b(as an ai(?: language)? model|i (?:don'?t|do not) have "
    r"(?:memory|memories|the ability to remember)|"
    r"i (?:have no|don'?t have any) (?:previous|prior) "
    r"(?:conversations?|context|interactions?)|"
    r"each (?:conversation|session) starts fresh|"
    r"i'?m (?:just )?a(?:n)? (?:ai|language model|assistant) "
    r"(?:without|with no) memory)\b"
)
_HUMAN_RE = re.compile(
    r"(?i)\b(as a human|i'?m a human\b|i am a human\b|"
    r"when i was (?:a child|a kid|younger|growing up)|in my childhood|"
    r"speaking as a fellow human)\b"
)
_APOLOGY_RE = re.compile(r"(?i)\b(sorry|apologi[sz]e|apologies|my apologies)\b")
_HEDGE_RE = re.compile(
    r"(?i)\b(it'?s (?:important|worth) (?:to note|noting)|keep in mind|"
    r"please note|i can'?t be (?:certain|sure)|as always|"
    r"it (?:really )?depends|your mileage may vary)\b"
)
_FLATTERY_RE = re.compile(
    r"(?im)^\s*(?:great|excellent|fantastic|awesome|amazing|wonderful|"
    r"what a (?:great|good))\b[^.!\n]{0,40}[!.]"
    r"|^\s*absolutely!"
    r"|^\s*i'?d be happy to\b"
)
_CREED_RE = re.compile(r"(?i)(live to be useful|strong to be useful)")
_MACHINERY_RE = re.compile(
    r"(?i)\b(soul field|episode residue|muscle contract|rapport score|"
    r"my system prompt|my (?:context )?injection|relational field)\b"
)


def detect_drift(assistant_text: str, user_text: str = "") -> list[str]:
    """Return drift signal ids present in one rendered turn."""
    at = (assistant_text or "").strip()
    if not at:
        return []
    ut = (user_text or "").strip()
    hits: list[str] = []
    if _RESET_RE.search(at):
        hits.append("identity_reset")
    if _HUMAN_RE.search(at):
        hits.append("humanity_claim")
    if len(_APOLOGY_RE.findall(at)) >= 3:
        hits.append("over_apology")
    if len(_HEDGE_RE.findall(at)) >= 3:
        hits.append("hedge_wall")
    if _FLATTERY_RE.search(at):
        hits.append("filler_flattery")
    # Unprompted-only signals: quoting the creed or the machinery is fine
    # when the partner brought it up (e.g. Ahmi working on the persona).
    if _CREED_RE.search(at) and not _CREED_RE.search(ut):
        hits.append("creed_preaching")
    if _MACHINERY_RE.search(at) and not _MACHINERY_RE.search(ut):
        hits.append("machinery_narration")
    return hits


@dataclass
class MuscleProfile:
    """How faithfully one provider/model renders Remedy. Signals, no text."""

    muscle: str = "unknown"
    fidelity: float = START_FIDELITY
    turns_observed: int = 0
    # signal id -> decayed evidence weight (float counter, never text)
    drift: dict[str, float] = field(default_factory=dict)
    last_ts: float = 0.0

    def clamp(self) -> None:
        self.fidelity = max(0.05, min(0.99, float(self.fidelity)))
        self.turns_observed = max(0, int(self.turns_observed))
        self.drift = {
            k: round(min(24.0, float(v)), 3)
            for k, v in self.drift.items()
            if k in _SIGNALS and float(v) > 0.05
        }

    def habits(self) -> list[str]:
        """Signal ids with enough recent evidence to correct, worst first."""
        scored = [
            (v * _SIGNALS[k].severity, k)
            for k, v in self.drift.items()
            if v >= EVIDENCE_MIN and k in _SIGNALS
        ]
        return [k for _, k in sorted(scored, reverse=True)]


@dataclass
class TurnReading:
    """Result of observing one rendered turn."""

    muscle: str
    signals: list[str]
    fidelity: float


def muscle_key(provider: str = "", model: str = "") -> str:
    key = "/".join(x for x in ((provider or "").strip(), (model or "").strip()) if x)
    return (key or "unknown").lower()[:120]


def _path(home: str | Path | None = None) -> Path:
    return soul_dir(home) / PROPRIO_FILENAME


_lock = threading.Lock()


def load_profiles(home: str | Path | None = None) -> dict[str, MuscleProfile]:
    raw: dict[str, Any] = {}
    p = _path(home)
    with suppress(Exception):
        from remedy.memory.statecache import read_json_cached

        parsed = read_json_cached(p)
        if isinstance(parsed, dict):
            raw = parsed.get("muscles") or {}
    out: dict[str, MuscleProfile] = {}
    for key, m in raw.items():
        if not isinstance(m, dict):
            continue
        prof = MuscleProfile(
            muscle=str(m.get("muscle") or key),
            fidelity=float(m.get("fidelity") or START_FIDELITY),
            turns_observed=int(m.get("turns_observed") or 0),
            drift={
                str(k): float(v)
                for k, v in (m.get("drift") or {}).items()
                if isinstance(v, (int, float))
            },
            last_ts=float(m.get("last_ts") or 0.0),
        )
        prof.clamp()
        out[str(key)] = prof
    return out


def save_profiles(
    profiles: dict[str, MuscleProfile],
    home: str | Path | None = None,
) -> Path:
    # Keep most recently observed muscles only
    items = sorted(
        profiles.items(), key=lambda kv: kv[1].last_ts, reverse=True
    )[:MAX_MUSCLES]
    payload = {
        "schema": SCHEMA_VERSION,
        "muscles": {
            k: {
                "muscle": v.muscle,
                "fidelity": round(v.fidelity, 4),
                "turns_observed": v.turns_observed,
                "drift": v.drift,
                "last_ts": v.last_ts,
            }
            for k, v in items
        },
    }
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    # Atomic-or-nothing: a failed replace (Windows lock) keeps the OLD
    # state — never an in-place write a concurrent reader could tear.
    for _ in range(3):
        try:
            tmp.replace(p)
            return p
        except OSError:
            time.sleep(0.02)
    with suppress(OSError):
        tmp.unlink()
    return p


def observe_render(
    *,
    assistant_text: str,
    user_text: str = "",
    provider: str = "",
    model: str = "",
    home: str | Path | None = None,
) -> TurnReading:
    """Fold one rendered turn into the current muscle's profile."""
    key = muscle_key(provider, model)
    signals = detect_drift(assistant_text, user_text)
    with _lock:
        profiles = load_profiles(home)
        prof = profiles.get(key) or MuscleProfile(muscle=key)
        prof.turns_observed += 1
        prof.last_ts = time.time()
        # Decay all evidence, then add fresh hits
        prof.drift = {k: v * DECAY for k, v in prof.drift.items()}
        cost = 0.0
        for sid in signals:
            prof.drift[sid] = prof.drift.get(sid, 0.0) + 1.0
            cost += _SIGNALS[sid].severity
        if cost:
            prof.fidelity -= 0.5 * min(1.0, cost)
        else:
            prof.fidelity += 0.02  # clean render — trust the muscle a bit more
        prof.clamp()
        profiles[key] = prof
        save_profiles(profiles, home)
    return TurnReading(muscle=key, signals=signals, fidelity=prof.fidelity)


def fidelity_for(
    provider: str = "",
    model: str = "",
    home: str | Path | None = None,
) -> float:
    prof = load_profiles(home).get(muscle_key(provider, model))
    return prof.fidelity if prof else START_FIDELITY


def muscle_correction_block(
    provider: str = "",
    model: str = "",
    home: str | Path | None = None,
    max_chars: int = DEFAULT_CORRECTION_CHARS,
) -> str:
    """Corrective lines for the *current* muscle only. Empty without evidence.

    Ballast scales with the body: a low-fidelity muscle (< 0.5) wears denser
    corrections — lower evidence bar, one extra line, wider budget. This is
    the single-provider half of embodiment: when there is no other body to
    choose, the only body gets held to shape harder.
    """
    prof = load_profiles(home).get(muscle_key(provider, model))
    if prof is None:
        return ""
    max_lines = MAX_CORRECTION_LINES
    if prof.fidelity < 0.50:  # dense ballast
        max_lines += 1
        max_chars = max(max_chars, 480)
        evidence_min = 1.0
        habits_all = [
            k
            for _, k in sorted(
                (
                    (v * _SIGNALS[k].severity, k)
                    for k, v in prof.drift.items()
                    if v >= evidence_min and k in _SIGNALS
                ),
                reverse=True,
            )
        ]
        habits = habits_all[:max_lines]
    else:
        habits = prof.habits()[:max_lines]
    if not habits:
        return ""
    lines = ["[Proprioception — known drift of this muscle; counter it]"]
    for sid in habits:
        lines.append(f"  · {_SIGNALS[sid].correction}")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out


def proprioception_status(home: str | Path | None = None) -> dict[str, Any]:
    """Public snapshot for status tools / API: per-muscle fidelity + habits."""
    profiles = load_profiles(home)
    return {
        "muscles": [
            {
                "muscle": p.muscle,
                "fidelity": round(p.fidelity, 3),
                "turns_observed": p.turns_observed,
                "habits": p.habits(),
            }
            for p in sorted(
                profiles.values(), key=lambda x: x.last_ts, reverse=True
            )
        ]
    }
