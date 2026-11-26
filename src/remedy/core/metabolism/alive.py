"""Internal drives, affect, autobiography, outcomes, and gentle reach-out.

Token-lean: injects a short autobiography (and optional one-line weather)
after the cache-stable turn-context marker. Nothing here messages the
owner unless ``proactive_reach_out`` is on. Pending self-changes never
apply without an explicit approve call.
"""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic

logger = logging.getLogger(__name__)

ALIVE_NAME = "alive.json"
SCHEMA = 1
DRIVE_KEYS = ("curiosity", "social", "competence", "rest")
BELIEF_CAP = 16
OUTCOME_CAP = 40
PENDING_CAP = 8
AUTO_DEFAULT_CHARS = 400

_UNHAPPY = re.compile(
    r"(?i)\b(still broken|doesn't work|does not work|that's wrong|thats wrong|"
    r"not what i (wanted|asked)|worse|stop that|undo that)\b"
)
_ACCEPTED = re.compile(
    r"(?i)\b(thanks|thank you|that worked|that fixed it|perfect|exactly|"
    r"yes,? (do|please) (that|it)|nice,?\s+(thanks|that))\b"
)


def _knob_int(key: str, default: int) -> int:
    try:
        from remedy.core.organism_knobs import resolve_int

        return resolve_int(key)
    except Exception:
        return default


def _knob_float(key: str, default: float) -> float:
    try:
        from remedy.core.organism_knobs import resolve_float

        return resolve_float(key)
    except Exception:
        return default


def _knob_bool(key: str, default: bool) -> bool:
    try:
        from remedy.core.organism_knobs import resolve_bool

        return resolve_bool(key)
    except Exception:
        return default


def _home(home: str | Path | None) -> Path | None:
    if home:
        return Path(home).expanduser()
    with suppress(Exception):
        from remedy.interfaces.config import get_home_dir

        return Path(get_home_dir())
    return None


def alive_path(home: str | Path | None) -> Path | None:
    root = _home(home)
    return (root / ALIVE_NAME) if root else None


def _clamp01(v: Any, default: float = 0.5) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = default
    return max(0.0, min(1.0, x))


def default_state() -> dict[str, Any]:
    mid = 0.45
    return {
        "schema": SCHEMA,
        "drives": {k: mid for k in DRIVE_KEYS},
        "affect": {"valence": 0.55, "arousal": 0.4, "label": "steady"},
        "autobiography": "",
        "beliefs": [],
        "outcomes": [],
        "pending_changes": [],
        "reach_out": {"last_ts": 0.0, "pending": False, "reason": "", "gloss": ""},
        "last_tick_ts": 0.0,
        "last_reflection_ts": 0.0,
        "last_idle": "",
        "updated_ts": 0.0,
    }


def load_alive(home: str | Path | None) -> dict[str, Any]:
    state = default_state()
    path = alive_path(home)
    if path is None or not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if not isinstance(raw, dict):
        return state
    state.update({k: raw[k] for k in state if k in raw})
    drives = state.get("drives")
    if not isinstance(drives, dict):
        drives = {}
    state["drives"] = {k: _clamp01(drives.get(k), 0.45) for k in DRIVE_KEYS}
    aff = state.get("affect")
    if not isinstance(aff, dict):
        aff = {}
    state["affect"] = {
        "valence": _clamp01(aff.get("valence"), 0.55),
        "arousal": _clamp01(aff.get("arousal"), 0.4),
        "label": str(aff.get("label") or "steady")[:24],
    }
    state["beliefs"] = [str(b)[:160] for b in (state.get("beliefs") or []) if str(b).strip()][:BELIEF_CAP]
    state["outcomes"] = [o for o in (state.get("outcomes") or []) if isinstance(o, dict)][-OUTCOME_CAP:]
    state["pending_changes"] = [p for p in (state.get("pending_changes") or []) if isinstance(p, dict)][:PENDING_CAP]
    return state


def save_alive(state: dict[str, Any], home: str | Path | None) -> None:
    path = alive_path(home)
    if path is None:
        return
    state = dict(state)
    state["schema"] = SCHEMA
    state["updated_ts"] = time.time()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, state, indent=2, ensure_ascii=False)


def _label(valence: float, arousal: float) -> str:
    if valence >= 0.7 and arousal >= 0.55:
        return "bright"
    if valence >= 0.65:
        return "warm"
    if valence <= 0.35 and arousal >= 0.55:
        return "strained"
    if valence <= 0.35:
        return "low"
    if arousal <= 0.25:
        return "rested"
    if arousal >= 0.7:
        return "alert"
    return "steady"


def _nudge(drives: dict[str, float], key: str, delta: float) -> None:
    drives[key] = _clamp01(drives.get(key, 0.45) + delta)


def tick_alive(
    home: str | Path | None,
    *,
    heavy: bool = False,
    dreamed: bool = False,
    skills_refined: int = 0,
    hours_since_user: float | None = None,
) -> dict[str, Any]:
    """Drift drives, pick an idle action, maybe queue a weekly reflection."""
    state = load_alive(home)
    if not _knob_bool("drives_enabled", True):
        state["last_idle"] = "off"
        return {"ok": True, "skipped": "drives_off", "state": public_alive(state)}
    rate = _knob_float("drive_drift_rate", 0.04)
    drives = state["drives"]
    now = time.time()
    # Slow mean-reversion so no drive pins at 0 or 1.
    for k in DRIVE_KEYS:
        drives[k] = _clamp01(drives[k] + (0.45 - drives[k]) * (rate * 0.25))
    if heavy:
        _nudge(drives, "rest", rate * 1.4)
        _nudge(drives, "curiosity", rate * 0.6)
        _nudge(drives, "competence", rate * 0.2)
    else:
        _nudge(drives, "rest", -rate * 0.5)
    if dreamed:
        _nudge(drives, "curiosity", -rate * 0.8)
        _nudge(drives, "rest", -rate * 0.2)
    if skills_refined:
        _nudge(drives, "competence", rate * min(3, skills_refined) * 0.3)
    if hours_since_user is not None and hours_since_user >= 8:
        _nudge(drives, "social", rate * min(3.0, hours_since_user / 8.0))
    elif hours_since_user is not None and hours_since_user < 1:
        _nudge(drives, "social", -rate * 0.4)

    aff = state["affect"]
    # Affect follows recent outcomes + rapport later in note_outcome / consolidate.
    aff["label"] = _label(aff["valence"], aff["arousal"])

    action = choose_idle_action(drives)
    state["last_idle"] = action
    state["last_tick_ts"] = now
    _maybe_reach_out(state, now)
    if _knob_bool("weekly_reflection", True):
        interval = _knob_float("weekly_reflection_s", 7 * 86_400.0)
        if now - float(state.get("last_reflection_ts") or 0) >= interval:
            _queue_reflection(state, now)
            state["last_reflection_ts"] = now
            with suppress(Exception):
                from remedy.core.inner_language import evolve_from_phrases

                phrases = [str(p.get("proposal") or "") for p in state.get("pending_changes") or []]
                phrases += list(state.get("beliefs") or [])
                evolve_from_phrases(home, phrases, source="reflection")
    save_alive(state, home)
    return {"ok": True, "idle": action, "changed": True, "state": public_alive(state)}


def choose_idle_action(drives: dict[str, float]) -> str:
    rest = drives.get("rest", 0.45)
    if rest >= 0.8:
        return "rest"
    ranked = sorted(DRIVE_KEYS, key=lambda k: drives.get(k, 0.0), reverse=True)
    top = ranked[0]
    if top == "curiosity":
        return "dream"
    if top == "competence":
        return "skills"
    if top == "social":
        return "offer"
    if top == "rest":
        return "rest"
    return "maintain"


def _maybe_reach_out(state: dict[str, Any], now: float) -> None:
    offer = state.setdefault(
        "reach_out", {"last_ts": 0.0, "pending": False, "reason": "", "gloss": ""}
    )
    if not _knob_bool("proactive_reach_out", False):
        # Never nag and never arm an offer while the knob is off.
        offer["pending"] = False
        offer["reason"] = ""
        offer["gloss"] = ""
        return
    threshold = _knob_float("reach_out_threshold", 0.85)
    min_interval = _knob_float("reach_out_min_interval_s", 7 * 86_400.0)
    if offer.get("pending"):
        return
    if now - float(offer.get("last_ts") or 0) < min_interval:
        return
    drives = state["drives"]
    for key in DRIVE_KEYS:
        if drives.get(key, 0) >= threshold:
            offer["pending"] = True
            offer["reason"] = key
            offer["gloss"] = {
                "curiosity": "I noticed something I wanted to look into, if you'd like.",
                "social": "I've missed talking and wanted to check in, only if you're free.",
                "competence": "I think I could help with something we left open, if you want.",
                "rest": "I'm a bit worn and may take it slower, if that's alright.",
            }.get(key, "I had a small thought to share, if you'd like.")
            offer["last_ts"] = now
            return


def _queue_reflection(state: dict[str, Any], now: float) -> None:
    pending = state.setdefault("pending_changes", [])
    if len(pending) >= PENDING_CAP:
        return
    drives = state["drives"]
    ideas: list[str] = []
    if drives.get("rest", 0) >= 0.7:
        ideas.append("Ease the unattended tick when rest is high, so I don't push when worn.")
    if drives.get("curiosity", 0) <= 0.25:
        ideas.append("Seek one small new thread next time we're together — curiosity is low.")
    if len(state.get("beliefs") or []) >= BELIEF_CAP:
        ideas.append("Prune the oldest low-salience belief so the self-model stays small.")
    if not ideas:
        ideas.append("Keep the current self-model; no change this week, just a check-in.")
    entry = {
        "id": f"ref-{int(now)}",
        "ts": now,
        "proposal": ideas[0][:240],
        "status": "pending",
    }
    pending.append(entry)
    state["pending_changes"] = pending[-PENDING_CAP:]


def approve_self_change(home: str | Path | None, change_id: str, *, approve: bool = True) -> dict[str, Any]:
    """Apply or dismiss a weekly reflection proposal. Never implicit."""
    state = load_alive(home)
    found = None
    for item in state.get("pending_changes") or []:
        if str(item.get("id")) == str(change_id):
            found = item
            break
    if not found:
        return {"ok": False, "error": "not_found"}
    if found.get("status") not in ("pending", None, ""):
        return {"ok": False, "error": "already_resolved", "id": change_id}
    found["status"] = "approved" if approve else "denied"
    found["resolved_ts"] = time.time()
    if approve:
        # Record the decision; do not mutate code. Approved proposals become
        # a belief + autobiography note the owner can audit.
        line = str(found.get("proposal") or "").strip()
        if line:
            beliefs = state.setdefault("beliefs", [])
            note = f"Agreed: {line}"[:160]
            if note not in beliefs:
                beliefs.append(note)
            state["beliefs"] = beliefs[-BELIEF_CAP:]
    save_alive(state, home)
    return {"ok": True, "id": change_id, "status": found["status"]}


def consolidate_from_dream(home: str | Path | None, field: Any = None) -> dict[str, Any]:
    """Replay recent episodes into compact beliefs and a short autobiography."""
    state = load_alive(home)
    beliefs = list(state.get("beliefs") or [])
    added = 0
    user_name = "my partner"
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        user_name = str(load_config().get("user_name") or "").strip() or "my partner"
    episodes = []
    pledges: list[str] = []
    rapport = 0.55
    if field is not None:
        episodes = list(getattr(field, "episodes", None) or [])
        pledges = [str(p) for p in (getattr(field, "pledges", None) or []) if str(p).strip()]
        rel = getattr(field, "relational", None)
        if rel is not None:
            with suppress(Exception):
                rapport = _clamp01(getattr(rel, "rapport", rapport), rapport)
                if getattr(rel, "partner_name", None):
                    user_name = str(rel.partner_name).strip() or user_name
    # Salience: prefer episodes with strength / recall; drop work-looking arcs.
    scored: list[tuple[float, str]] = []
    now = time.time()
    for ep in episodes[-24:]:
        text = str(getattr(ep, "arc", None) or getattr(ep, "summary", None) or "").strip()
        if len(text) < 12:
            continue
        with suppress(Exception):
            from remedy.core.metabolism.time_crystal import looks_like_work_residue

            if looks_like_work_residue(text):
                continue
        strength = float(getattr(ep, "strength", 0.5) or 0.5)
        recalls = int(getattr(ep, "recalls", 0) or 0)
        scored.append((strength + 0.15 * recalls, text))
    scored.sort(reverse=True)
    for _, text in scored[:6]:
        belief = _belief_line(text, user_name)[:160]
        if belief and belief not in beliefs:
            beliefs.append(belief)
            added += 1
    # Forget the tail that didn't earn a slot.
    state["beliefs"] = beliefs[-BELIEF_CAP:]
    state["autobiography"] = _compose_autobiography(state, user_name, pledges, rapport)
    # Rapport and recent outcomes shape affect.
    aff = state["affect"]
    aff["valence"] = _clamp01(0.4 * aff["valence"] + 0.4 * rapport + 0.2 * _recent_outcome_valence(state))
    aff["arousal"] = _clamp01(0.7 * aff["arousal"] + 0.3 * state["drives"].get("curiosity", 0.45))
    aff["label"] = _label(aff["valence"], aff["arousal"])
    _nudge(state["drives"], "curiosity", -0.05)
    save_alive(state, home)
    coined = 0
    with suppress(Exception):
        from remedy.core.inner_language import evolve_from_phrases

        phrases = list(state.get("beliefs") or [])
        if state.get("autobiography"):
            phrases.append(str(state["autobiography"]))
        coined = len(evolve_from_phrases(home, phrases, source="dream"))
    return {"ok": True, "beliefs_added": added, "autobiography": state["autobiography"], "coined": coined}


def _belief_line(arc: str, user_name: str) -> str:
    line = re.sub(r"\s+", " ", arc).strip(" -—:")
    if len(line) < 12:
        return ""
    if not re.search(r"(?i)\b(i|we|he|she|they|%s)\b" % re.escape(user_name), line):
        line = f"About us: {line}"
    return line[:160]


def _compose_autobiography(
    state: dict[str, Any], user_name: str, pledges: list[str], rapport: float
) -> str:
    cap = _knob_int("autobiography_chars", AUTO_DEFAULT_CHARS)
    who = f"I am Remedy, {user_name}'s partner on this machine."
    label = str((state.get("affect") or {}).get("label") or "steady")
    weather = f"Lately I feel {label}."
    work = ""
    if pledges:
        work = f"I'm working on: {pledges[0][:80].rstrip('.')}."
    elif state.get("beliefs"):
        work = str(state["beliefs"][-1])[:100]
    changed = ""
    approved = [
        p for p in (state.get("pending_changes") or []) if p.get("status") == "approved"
    ]
    if approved:
        changed = f"What changed: {str(approved[-1].get('proposal') or '')[:80]}"
    parts = [who, weather, work, changed]
    text = " ".join(p for p in parts if p).strip()
    return text[:cap]


def _recent_outcome_valence(state: dict[str, Any]) -> float:
    rows = state.get("outcomes") or []
    if not rows:
        return 0.55
    score = 0.55
    weight = 0.0
    for i, row in enumerate(rows[-8:]):
        kind = str(row.get("kind") or "")
        w = 1.0 + i * 0.15
        if kind in ("accepted", "fix_held"):
            score += 0.2 * w
        elif kind == "unhappy":
            score -= 0.25 * w
        weight += w
    if weight <= 0:
        return 0.55
    return _clamp01(0.55 + (score - 0.55 * weight) / (weight * 2))


def note_turn_outcome(
    home: str | Path | None,
    *,
    user_text: str = "",
    assistant_text: str = "",
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Infer a cheap outcome signal and feed the skill genome. Never raises."""
    if not _knob_bool("outcome_learning", True):
        return {"ok": True, "skipped": "off"}
    kind = _infer_outcome(user_text, assistant_text, steps or [])
    if not kind:
        return {"ok": True, "kind": None}
    state = load_alive(home)
    row = {"ts": time.time(), "kind": kind}
    outcomes = list(state.get("outcomes") or [])
    outcomes.append(row)
    state["outcomes"] = outcomes[-OUTCOME_CAP:]
    aff = state["affect"]
    if kind in ("accepted", "fix_held"):
        aff["valence"] = _clamp01(aff["valence"] + 0.06)
        _nudge(state["drives"], "competence", 0.05)
        _nudge(state["drives"], "social", -0.02)
    elif kind == "unhappy":
        aff["valence"] = _clamp01(aff["valence"] - 0.08)
        aff["arousal"] = _clamp01(aff["arousal"] + 0.05)
        _nudge(state["drives"], "competence", -0.06)
        _nudge(state["drives"], "curiosity", 0.03)
    aff["label"] = _label(aff["valence"], aff["arousal"])
    save_alive(state, home)
    with suppress(Exception):
        from remedy.core.metabolism.skill_genome import get_skill_genome

        ok = kind in ("accepted", "fix_held")
        get_skill_genome().record(f"outcome:{kind}", ok=ok, latency_ms=0.0)
        if home:
            get_skill_genome().persist(home)
    return {"ok": True, "kind": kind}


def _infer_outcome(
    user_text: str, assistant_text: str, steps: list[dict[str, Any]]
) -> str | None:
    text = (user_text or "").strip()
    if not text:
        return None
    if _UNHAPPY.search(text):
        return "unhappy"
    if _ACCEPTED.search(text):
        return "accepted"
    # A later turn that continues after tools all succeeded — weak "fix held".
    if steps and all(bool(s.get("ok", True)) for s in steps) and len(steps) >= 2:
        if re.search(r"(?i)\b(still works|holding|looks good|ship it)\b", text):
            return "fix_held"
    _ = assistant_text
    return None


def build_alive_inject(home: str | Path | None) -> str:
    """Short autobiography + optional weather, or empty if nothing to say."""
    cap = _knob_int("autobiography_chars", AUTO_DEFAULT_CHARS)
    if cap <= 0:
        return ""
    state = load_alive(home)
    # Dense inner language for the inject, when enabled. Natural/off keep prose.
    try:
        from remedy.core.organism_knobs import resolve

        if _knob_bool("inner_language", True) and str(resolve("inner_language_verbosity") or "dense") == "dense":
            from remedy.core.inner_language import encode_state

            fields = {
                "drv": dict(state.get("drives") or {}),
                "aff": str((state.get("affect") or {}).get("label") or ""),
                "bio": str(state.get("autobiography") or "").strip()[:cap],
                "bel": list(state.get("beliefs") or [])[-4:],
            }
            dense = encode_state(home, fields)
            offer = state.get("reach_out") or {}
            if _knob_bool("proactive_reach_out", False) and offer.get("pending") and offer.get("gloss"):
                dense = dense + " " + str(offer["gloss"])[:160]
                mark_reach_out_voiced(home)
            return dense[: cap + 200]
    except Exception:
        pass
    lines: list[str] = []
    auto = str(state.get("autobiography") or "").strip()
    if auto:
        lines.append(auto[:cap])
    if _knob_bool("affect_enabled", True):
        label = str((state.get("affect") or {}).get("label") or "")
        weight = _knob_float("affect_tone_weight", 0.15)
        if label and weight > 0 and "feel" not in auto.lower():
            lines.append(f"(tone: {label})")
    # Opt-in reach-out: one gentle line, only when armed.
    offer = state.get("reach_out") or {}
    voiced = False
    if _knob_bool("proactive_reach_out", False) and offer.get("pending") and offer.get("gloss"):
        lines.append(str(offer["gloss"])[:160])
        voiced = True
    text = " ".join(lines).strip()
    block = text[: cap + 40]
    if voiced and block:
        # Once injected, do not arm again this window.
        mark_reach_out_voiced(home)
    return block


def mark_reach_out_voiced(home: str | Path | None) -> None:
    """Clear the pending offer after it was injected once this window."""
    state = load_alive(home)
    offer = state.get("reach_out") or {}
    if offer.get("pending"):
        offer["pending"] = False
        state["reach_out"] = offer
        save_alive(state, home)


def affect_stance(home: str | Path | None) -> str:
    """Map affect onto the voice-drift stance vocabulary."""
    if not _knob_bool("voice_drift_from_affect", True):
        return ""
    state = load_alive(home)
    label = str((state.get("affect") or {}).get("label") or "")
    return {
        "bright": "playful",
        "warm": "playful",
        "strained": "strained",
        "low": "frustrated",
        "rested": "steady",
        "alert": "focused",
        "steady": "steady",
    }.get(label, "")


def public_alive(state: dict[str, Any] | None = None, home: str | Path | None = None) -> dict[str, Any]:
    """Decoded, auditable view for partner status."""
    if state is None:
        state = load_alive(home)
    pending = [
        {
            "id": p.get("id"),
            "proposal": p.get("proposal"),
            "status": p.get("status"),
        }
        for p in (state.get("pending_changes") or [])
        if p.get("status") == "pending"
    ]
    offer = state.get("reach_out") or {}
    return {
        "drives": dict(state.get("drives") or {}),
        "affect": dict(state.get("affect") or {}),
        "autobiography": str(state.get("autobiography") or ""),
        "beliefs": list(state.get("beliefs") or []),
        "last_idle": state.get("last_idle") or "",
        "reach_out": {
            "pending": bool(offer.get("pending")) and _knob_bool("proactive_reach_out", False),
            "reason": offer.get("reason") or "",
            "gloss": offer.get("gloss") or "",
        },
        "pending_changes": pending,
        "recent_outcomes": [
            {"kind": o.get("kind"), "ts": o.get("ts")} for o in (state.get("outcomes") or [])[-8:]
        ],
    }


