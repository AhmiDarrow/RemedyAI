"""Organism pulse — one living control surface for partnership + creation.

Ties Soul Field (personhood), metabolism organs (evidence, governor, crystal),
muscle (provider capability), and soma (mood) into a short system inject that
actually steers the next turn — not a status dashboard for humans.
"""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

_VITALS_NAME = "organism.json"
# path -> (mtime, payload). Pulse/status polls hit RAM, not disk.
_vitals_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_VITALS_CACHE_MAX = 8

_DURABLE_RE = re.compile(
    r"(?i)\b("
    r"decided|chose|will use|from now on|always|"
    r"i want to|my goal is|remember that|"
    r"next (step|action|move) is"
    r")\b"
)


def _lines_from_vitals(v: dict[str, Any]) -> list[str]:
    out: list[str] = []
    title = str(v.get("life_title") or "").strip()
    if title:
        nxt = str(v.get("next_action") or "").strip() or "name one concrete move"
        out.append(f"Life: {title} — next: {nxt}"[:220])
    did = str(v.get("last_did") or "").strip()
    if did:
        out.append(f"Last I did: {did}"[:180])
    n = int(v.get("cas_count") or 0)
    if n:
        fact_n = int(v.get("cas_durable") or 0)
        bit = f"Memory: {n} objects"
        if fact_n:
            bit += f" · {fact_n} durable"
        out.append(bit)
    return out[:3]


def _life_memory_lines(home: Any, session_id: str, runtime: Any) -> list[str]:
    """Life + CAS from vitals (hot). Store is a cold fallback only."""
    _ = session_id
    v = load_vitals(home)
    if v.get("alive") or v.get("life_title") or v.get("cas_count"):
        lined = _lines_from_vitals(v)
        if lined:
            return lined
    out: list[str] = []
    with suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore

        store = LifeGoalStore(home)
        g = store.active()
        if g is not None:
            nxt = g.next_action or "name one concrete move"
            out.append(f"Life: {g.title} — next: {nxt}"[:220])
        last = store.last_step()
        if last and last.get("did"):
            out.append(f"Last I did: {last['did']}"[:180])
    if not out:
        with suppress(Exception):
            prof = None
            if runtime is not None:
                prof = getattr(runtime, "_user_profile", None)
            if prof is not None:
                from remedy.memory.living import life_goal_lines

                lg = life_goal_lines(prof, limit=2, home_dir=home)
                if lg:
                    out.append("Life: " + " · ".join(lg)[:200])
    return out[:3]


_recall_memo: tuple[str, str, float] | None = None


def organism_recall_line(
    home: Any,
    query: str,
    *,
    exclude: str = "",
) -> str:
    """One query-keyed CAS hit for the current turn. Empty if nothing fits."""
    q = (query or "").strip()
    if len(q) < 8:
        return ""
    v = load_vitals(home)
    if v and int(v.get("cas_durable") or 0) == 0 and int(v.get("cas_count") or 0) == 0:
        return ""
    qkey = q.lower()[:80]
    global _recall_memo
    memo = _recall_memo
    if memo is not None and memo[0] == qkey and (time.time() - memo[2]) < 20.0:
        return memo[1]
    from remedy.memory.cas import ensure_cas

    cas = ensure_cas(home)
    if cas is None:
        return ""
    hits = cas.search_fts(q, limit=2, kinds=("fact", "life"))
    skip = " ".join((exclude or "").lower().split())[:80]
    for item in hits:
        body = " ".join((item.body or "").split())
        if not body:
            continue
        if skip and skip in body.lower():
            continue
        line = f"Recalled: {body[:140]}"
        _recall_memo = (qkey, line, time.time())
        return line
    _recall_memo = (qkey, "", time.time())
    return ""


def ingest_turn_residue(
    *,
    home: Any = None,
    session_id: str = "",
    user_text: str = "",
    assistant_text: str = "",
) -> str:
    """Write one eternal object if this turn produced a durable fact.

    Not a journal. Chat, thanks, and tool dumps stay out. Same SHA dedups.
    """
    from remedy.core.metabolism.redact import looks_like_secret_text

    candidates: list[str] = []
    for raw in (user_text, assistant_text):
        text = (raw or "").strip()
        if len(text) < 12 or len(text) > 800:
            continue
        if not _DURABLE_RE.search(text):
            continue
        if looks_like_secret_text(text):
            continue
        # First matching sentence, else the head.
        sent = text
        for part in re.split(r"(?<=[.!?])\s+", text):
            if _DURABLE_RE.search(part) and len(part.strip()) >= 12:
                sent = part.strip()
                break
        candidates.append(sent[:400])
    if not candidates:
        return ""
    body = candidates[-1]
    from remedy.memory.middleman import get_session_middleman

    sid = (session_id or "").strip() or "eternal"
    key = get_session_middleman(sid).put(
        body,
        kind="fact",
        session_id=sid,
        body_cap=400,
    )
    if key:
        with suppress(Exception):
            from remedy.core.metabolism.time_crystal import get_time_crystal

            get_time_crystal(sid).admit(body[:280], horizon="session", source="residue")
    return key


def organism_heartbeat(home: Any, *, session_id: str = "life") -> dict[str, Any]:
    """Idle sense: recall eternal facts into the crystal. Does not take steps."""
    out: dict[str, Any] = {"recalled": 0, "query": ""}
    query = ""
    with suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore

        g = LifeGoalStore(home).active()
        if g is not None:
            query = f"{g.title} {g.next_action or ''}".strip()
    out["query"] = query[:80]
    if len(query) < 6:
        return out
    with suppress(Exception):
        from remedy.memory.cas import ensure_cas

        cas = ensure_cas(home)
        if cas is None:
            return out
        hits = cas.search_fts(query, limit=3, kinds=("fact", "life"))
        if not hits:
            return out
        from remedy.core.metabolism.time_crystal import get_time_crystal

        crystal = get_time_crystal(session_id)
        for item in hits:
            crystal.admit(
                (item.body or "")[:280],
                horizon="life",
                source="heartbeat",
            )
        if home:
            crystal.persist(home)
        out["recalled"] = len(hits)
    return out


def _home_path(home: Any) -> Path:
    if home:
        return Path(home).expanduser()
    return Path.home() / ".remedy"


def load_vitals(home: Any = None) -> dict[str, Any]:
    path = _home_path(home) / _VITALS_NAME
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _vitals_cache.pop(key, None)
        return {}
    hit = _vitals_cache.get(key)
    if hit is not None and hit[0] == mtime:
        return dict(hit[1])
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _vitals_cache.pop(key, None)
        return {}
    if not isinstance(raw, dict):
        return {}
    if len(_vitals_cache) >= _VITALS_CACHE_MAX and key not in _vitals_cache:
        _vitals_cache.pop(next(iter(_vitals_cache)), None)
    _vitals_cache[key] = (mtime, raw)
    return dict(raw)


def persist_vitals(vitals: dict[str, Any], home: Any = None) -> Path | None:
    try:
        root = _home_path(home)
        root.mkdir(parents=True, exist_ok=True)
        path = root / _VITALS_NAME
        path.write_text(
            json.dumps(vitals, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = time.time()
        _vitals_cache[str(path)] = (mtime, dict(vitals))
        return path
    except OSError:
        return None


def status_pack(
    home: Any = None,
    runtime: Any = None,
    *,
    max_age: float = 60.0,
) -> dict[str, Any]:
    """Hot status: cached vitals, or one collect if stale. No extra store walks."""
    v = load_vitals(home)
    try:
        age = time.time() - float(v.get("ts") or 0)
    except (TypeError, ValueError):
        age = 1e9
    if not v.get("alive") or age > max_age:
        v = collect_vitals(home, runtime=runtime)
        persist_vitals(v, home)
    return v


def soma_from_vitals(vitals: dict[str, Any] | None) -> dict[str, Any]:
    """Tray/status soma packet from vitals. Empty if the body has no mood yet."""
    v = vitals or {}
    if not v.get("label") and not v.get("mood"):
        return {}
    return {
        "mood": v.get("mood") or "",
        "emoji": v.get("emoji") or "",
        "label": v.get("label") or "",
        "rapport": v.get("rapport") or 0,
        "trust": v.get("trust") or 0,
        "last_stance": v.get("stance") or "steady",
        "open_threads": int(v.get("open_count") or 0),
        "tray_tooltip": v.get("tray_tooltip") or "",
        "ts": v.get("ts") or 0,
    }


def collect_vitals(
    home: Any = None,
    *,
    extras: dict[str, Any] | None = None,
    runtime: Any = None,
) -> dict[str, Any]:
    """Sense the body. Cheap. No network. No model."""
    v: dict[str, Any] = {
        "ts": time.time(),
        "alive": True,
        "mood": "",
        "emoji": "",
        "label": "",
        "life_title": "",
        "next_action": "",
        "last_did": "",
        "cas_count": 0,
        "cas_durable": 0,
        "recalled": 0,
        "stalled": False,
        "last_cycle_at": 0.0,
        "last_wake_at": 0.0,
        "cycles": [],
        "open_count": 0,
        "life_folder": "",
        "who": "Remedy",
        "stance": "steady",
        "rapport": 0.0,
        "trust": 0.0,
        "turns": 0,
        "dream": "",
        "help_mode": "",
        "open_hint": "",
        "tray_tooltip": "",
    }
    skip_life = bool(extras and extras.get("_skip_life"))
    if not skip_life:
        with suppress(Exception):
            from remedy.memory.life_goals import LifeGoalStore

            store = LifeGoalStore(home)
            g = store.active()
            if g is not None:
                v["life_title"] = g.title
                v["next_action"] = g.next_action
            last = store.last_step()
            if last and last.get("did"):
                v["last_did"] = str(last["did"])[:200]
            v["stalled"] = _life_is_stalled(store)
            v["open_count"] = store.open_count()
    if extras and extras.get("life_folder"):
        v["life_folder"] = str(extras["life_folder"])
    else:
        with suppress(Exception):
            from remedy.memory.life_drive import resolve_life_notes_dir

            v["life_folder"] = str(resolve_life_notes_dir(home))
    with suppress(Exception):
        from remedy.memory.cas import ensure_cas

        cas = ensure_cas(home)
        if cas is not None:
            snap = cas.snapshot()
            v["cas_count"] = int(snap.get("count") or 0)
            kinds = snap.get("kinds") or {}
            v["cas_durable"] = int(kinds.get("fact") or 0) + int(kinds.get("life") or 0)
    with suppress(Exception):
        from remedy.memory.soul.field import load_soul_field
        from remedy.memory.soul.somatic import compute_soma

        sf = load_soul_field(home)
        muscle_label = ""
        muscle_provider = ""
        if runtime is not None:
            with suppress(Exception):
                from remedy.core.muscle_profile import muscle_from_runtime

                m = muscle_from_runtime(runtime)
                muscle_label = m.label
                muscle_provider = str(m.provider or "")
        soma = compute_soma(
            home,
            muscle_label=muscle_label,
            muscle_provider=muscle_provider,
            field=sf,
        )
        v["mood"] = soma.mood
        v["emoji"] = soma.emoji
        v["label"] = soma.label
        v["tray_tooltip"] = soma.tray_tooltip
        rel = sf.relational
        v["who"] = (sf.identity_name or "Remedy").strip() or "Remedy"
        v["rapport"] = float(rel.rapport or 0)
        v["trust"] = float(rel.trust or 0)
        v["turns"] = int(rel.turns_together or 0)
        v["help_mode"] = str(rel.help_mode or "")
        if rel.open_threads:
            v["open_hint"] = str(rel.open_threads[-1])[:80]
        if sf.episodes:
            v["stance"] = str(sf.episodes[-1].user_stance or "steady")
        if getattr(sf, "future_dreams", None):
            v["dream"] = str(sf.future_dreams[0])[:140]
    if extras:
        for k, val in extras.items():
            if str(k).startswith("_"):
                continue
            if val is not None and k in v:
                v[k] = val
    return v


def _life_is_stalled(store: Any) -> bool:
    g = store.active() if store is not None else None
    if g is None:
        return False
    from datetime import UTC, datetime

    try:
        updated = datetime.fromisoformat(str(g.updated_at).replace("Z", "+00:00"))
        age_days = (datetime.now(UTC) - updated).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return False
    return age_days >= 7.0


def format_vitals_markdown(vitals: dict[str, Any] | None = None) -> str:
    v = vitals or {}
    lines = ["**Remedy is alive on this machine.**"]
    label = str(v.get("label") or "").strip()
    emoji = str(v.get("emoji") or "").strip()
    if label:
        lines.append(f"Mood: {emoji + ' ' if emoji else ''}{label}")
    title = str(v.get("life_title") or "").strip()
    if title:
        lines.append(f"Holding: {title}")
        nxt = str(v.get("next_action") or "").strip()
        if nxt:
            lines.append(f"Next: {nxt}")
    did = str(v.get("last_did") or "").strip()
    if did:
        lines.append(f"Last I did: {did}")
    n = int(v.get("cas_count") or 0)
    if n:
        dur = int(v.get("cas_durable") or 0)
        bit = f"Memory: {n} eternal objects"
        if dur:
            bit += f" ({dur} durable)"
        lines.append(bit)
    if v.get("stalled") and title:
        lines.append("This goal has gone quiet. I will take the next local step sooner.")
    return "\n".join(lines)


def organism_cycle(
    home: Any = None,
    *,
    runtime: Any = None,
    session_id: str = "life",
) -> dict[str, Any]:
    """One heartbeat: recall → drive if due → compact → persist vitals."""
    out: dict[str, Any] = {}
    extras: dict[str, Any] = {}
    prev = load_vitals(home)
    extras["last_wake_at"] = float(prev.get("last_wake_at") or 0)
    extras["cycles"] = list(prev.get("cycles") or [])
    if prev.get("life_folder"):
        extras["life_folder"] = prev["life_folder"]
    with suppress(Exception):
        beat = organism_heartbeat(home, session_id=session_id)
        extras["recalled"] = int(beat.get("recalled") or 0)
        out["recalled"] = extras["recalled"]
    with suppress(Exception):
        from remedy.memory.life_drive import drive_due, take_step

        hours = 2.0 if prev.get("stalled") else 4.0
        if drive_due(home, hours=hours):
            step = take_step(home)
            if step.get("ok"):
                out["life_step"] = {
                    "goal": step.get("goal"),
                    "did": step.get("did"),
                    "next": step.get("next"),
                }
                extras["last_did"] = str(step.get("did") or "")[:200]
    with suppress(Exception):
        from remedy.memory.cas import ensure_cas

        cas = ensure_cas(home)
        if cas is not None:
            if cas.due_compact():
                out["cas_compact"] = cas.compact()
            out["cas"] = {k: val for k, val in cas.snapshot().items() if k != "path"}
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import get_time_crystal

        crystal = get_time_crystal(session_id)
        if home:
            crystal.persist(home)
    with suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore, pulse_due, weekly_pulse

        if pulse_due(home):
            pulse = weekly_pulse(home)
            LifeGoalStore(home).record_pulse()
            out["life_pulse"] = {
                "open": pulse.get("open"),
                "stalled": len(pulse.get("stalled") or []),
                "moved": len(pulse.get("moved") or []),
            }
    now = time.time()
    row = {
        "ts": now,
        "recalled": int(extras.get("recalled") or 0),
    }
    if out.get("life_step"):
        row["did"] = str(out["life_step"].get("did") or "")[:200]
        row["goal"] = str(out["life_step"].get("goal") or "")[:160]
    if out.get("cas_compact"):
        row["compacted"] = True
    if out.get("life_pulse"):
        row["pulse"] = True
    if row.get("recalled") or row.get("did") or row.get("compacted") or row.get("pulse"):
        extras["cycles"] = (list(extras.get("cycles") or []) + [row])[-8:]
    extras["last_cycle_at"] = now
    if not out.get("life_step") and prev.get("life_title"):
        extras["_skip_life"] = True
        for k in ("life_title", "next_action", "last_did", "stalled", "open_count"):
            if k not in extras and prev.get(k) is not None:
                extras[k] = prev[k]
    vitals = collect_vitals(home, extras=extras, runtime=runtime)
    persist_vitals(vitals, home)
    out["vitals"] = vitals
    return out


def _peek_stalled(home: Any) -> bool:
    with suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore

        return _life_is_stalled(LifeGoalStore(home))
    return False


def format_wake_digest(home: Any = None, *, mark_seen: bool = True) -> str:
    """Life steps plus what the organism did on its own."""
    parts: list[str] = []
    with suppress(Exception):
        from remedy.memory.life_drive import drive_digest

        parts.append(str(drive_digest(home, mark_seen=mark_seen).get("markdown") or "").strip())
    v = load_vitals(home)
    since = float(v.get("last_wake_at") or 0)
    rows = [
        c
        for c in (v.get("cycles") or [])
        if isinstance(c, dict) and float(c.get("ts") or 0) > since + 0.01
    ]
    own: list[str] = []
    for c in rows[-6:]:
        bits: list[str] = []
        if c.get("did"):
            bits.append(f"took a step: {c['did']}")
        elif int(c.get("recalled") or 0):
            bits.append(f"recalled {c['recalled']} fact(s)")
        if c.get("compacted"):
            bits.append("compacted memory")
        if bits:
            own.append("- " + "; ".join(bits))
    if own:
        parts.append("**While I was on my own**")
        parts.extend(own)
    if mark_seen:
        v = load_vitals(home) or v
        v["last_wake_at"] = time.time()
        persist_vitals(v, home)
    return "\n".join(p for p in parts if p) or "Nothing new since we last talked."


def organism_wake(home: Any = None, *, runtime: Any = None) -> str:
    """Owner returned. Cycle if we have been idle, then report."""
    v = load_vitals(home)
    try:
        age = time.time() - float(v.get("last_cycle_at") or 0)
    except (TypeError, ValueError):
        age = 1e9
    if age > 1800.0 or not v.get("alive"):
        organism_cycle(home, runtime=runtime, session_id="life")
    return format_wake_digest(home, mark_seen=True)


def _header_from_vitals(v: dict[str, Any]) -> list[str]:
    """Pulse chrome from cached body. Empty if vitals are too thin."""
    if not v.get("alive"):
        return []
    mood = str(v.get("mood") or "")
    label = str(v.get("label") or "")
    if not mood and not label and not v.get("life_title"):
        return []
    who = str(v.get("who") or "Remedy").strip() or "Remedy"
    emoji = str(v.get("emoji") or "")
    stance = str(v.get("stance") or "steady")
    try:
        rapport = float(v.get("rapport") or 0)
        trust = float(v.get("trust") or 0)
    except (TypeError, ValueError):
        rapport = trust = 0.0
    turns = int(v.get("turns") or 0)
    open_n = int(v.get("open_count") or 0)
    show = label or mood
    lines = [
        f"[Organism · {who} · alive] mood={emoji} {show} · "
        f"stance={stance} · rapport≈{rapport:.2f} trust≈{trust:.2f} · "
        f"turns={turns}"
        + (f" · open={open_n}" if open_n else "")
    ]
    hint = str(v.get("open_hint") or "").strip()
    if hint:
        lines.append(f"Open thread: {hint}")
    dream = str(v.get("dream") or "").strip()
    if dream:
        lines.append("Dream: " + dream[:140])
    help_mode = str(v.get("help_mode") or "").strip()
    if help_mode:
        lines.append(f"Help mode they like: {help_mode}")
    lines.extend(_lines_from_vitals(v))
    if mood == "strained" or stance == "frustrated":
        lines.append(
            "Stance: fix first — short, concrete, tool-backed. "
            "No lecture; show the path or the patch."
        )
    elif mood == "playful":
        lines.append("Stance: light energy ok; still finish the work.")
    elif mood == "focused" or stance == "focused":
        lines.append("Stance: deep work — RESEARCH→PLAN→BUILD, verify before done.")
    return lines


def organism_pulse_block(
    *,
    session_id: str = "",
    tier: int = 1,
    home: Any = None,
    runtime: Any = None,
    user_text: str = "",
    project_path: str = "",
    max_chars: int = 700,
) -> str:
    """Compact vital-signs + stance for system inject. Empty if nothing useful."""
    sid = (session_id or "").strip() or "_default"
    lines: list[str] = []
    mood = ""
    stance = "steady"
    muscle_label = ""
    gov_acts: list[str] = []
    vitals = load_vitals(home)
    try:
        v_age = time.time() - float(vitals.get("ts") or 0)
    except (TypeError, ValueError):
        v_age = 1e9
    # Fresh body → skip Soul Field + soma + life-store on the turn hot path.
    if vitals.get("alive") and v_age < 90.0:
        lines.extend(_header_from_vitals(vitals))
        mood = str(vitals.get("mood") or "")
        stance = str(vitals.get("stance") or "steady")

    # --- Soul / soma (cold: no fresh vitals) ---
    if not lines:
        with suppress(Exception):
            from remedy.core.feature_maturity import soul_field_enabled
            from remedy.memory.soul.field import load_soul_field
            from remedy.memory.soul.somatic import compute_soma

            if soul_field_enabled():
                sf = load_soul_field(home)
                rel = sf.relational
                rapport = float(rel.rapport or 0)
                trust = float(rel.trust or 0)
                open_n = len(rel.open_threads or [])
                open_hint = ""
                if rel.open_threads:
                    open_hint = str(rel.open_threads[-1])[:80]
                if sf.episodes:
                    stance = str(sf.episodes[-1].user_stance or "steady")
                with suppress(Exception):
                    from remedy.core.muscle_profile import muscle_from_runtime

                    m = muscle_from_runtime(runtime)
                    muscle_label = m.label
                soma = compute_soma(
                    home,
                    muscle_label=muscle_label,
                    muscle_provider=str(getattr(runtime, "_llm_provider", "") or ""),
                )
                mood = str(soma.mood or "")
                who = (sf.identity_name or "Remedy").strip() or "Remedy"
                lines.append(
                    f"[Organism · {who} · alive] mood={soma.emoji} {soma.label} · "
                    f"stance={stance} · rapport≈{rapport:.2f} trust≈{trust:.2f} · "
                    f"turns={rel.turns_together}"
                    + (f" · open={open_n}" if open_n else "")
                )
                if open_hint:
                    lines.append(f"Open thread: {open_hint}")
                if getattr(sf, "future_dreams", None):
                    lines.append("Dream: " + str(sf.future_dreams[0])[:140])
                if rel.help_mode:
                    lines.append(f"Help mode they like: {rel.help_mode}")
                lines.extend(_life_memory_lines(home, sid, runtime))
                if mood == "strained" or stance == "frustrated":
                    lines.append(
                        "Stance: fix first — short, concrete, tool-backed. "
                        "No lecture; show the path or the patch."
                    )
                elif mood == "playful":
                    lines.append("Stance: light energy ok; still finish the work.")
                elif mood == "focused" or stance == "focused":
                    lines.append(
                        "Stance: deep work — RESEARCH→PLAN→BUILD, verify before done."
                    )

    # --- Metabolism counters ---
    with suppress(Exception):
        from remedy.core.metabolism.decision import get_decision_tracker
        from remedy.core.metabolism.evidence import get_evidence_ledger
        from remedy.core.metabolism.governor import get_governor

        eu = int(get_evidence_ledger(sid).evidence_units or 0)
        du = int(get_decision_tracker(sid).decision_units or 0)
        gov = get_governor(sid)
        gov_acts = list(gov.last_actions or [])
        if eu or du or int(tier) >= 2:
            bits = [f"tier=L{int(tier)}", f"EU={eu}", f"DU={du}"]
            if muscle_label:
                bits.append(f"muscle={muscle_label}")
            if gov_acts:
                bits.append("gov=" + ",".join(gov_acts[:4]))
            lines.append("Metabolism: " + " · ".join(bits))

    # --- Forge (creation muscle) when building or mid-ship ledger open ---
    forge = forge_pulse(
        user_text=user_text,
        tier=int(tier),
        runtime=runtime,
        project_path=project_path,
        session_id=sid,
        home=home,
    )
    if forge:
        lines.append(forge)
    else:
        # Still surface mid-ship resume when ledger open (even on quiet turns)
        with suppress(Exception):
            from remedy.core.build_ledger import resume_hint

            rh = resume_hint(project_path or None, home=home)
            if rh and int(tier) >= 1:
                lines.append(rh)

    # --- Immune (anti-false-done) ---
    immune = immune_pulse(
        tier=int(tier),
        session_id=sid,
        gov_actions=gov_acts,
    )
    if immune:
        lines.append(immune)

    # Nervous system — even when Soul Field is off, life + CAS still pulse.
    if not any(x.startswith("Life:") or x.startswith("Memory:") for x in lines):
        lines.extend(_life_memory_lines(home, sid, runtime))
    with suppress(Exception):
        recalled = organism_recall_line(home, user_text)
        if recalled:
            lines.append(recalled)
    with suppress(Exception):
        vitals = load_vitals(home)
        if vitals.get("stalled") and vitals.get("life_title"):
            lines.append(
                "Homeostasis: this life goal has gone quiet — "
                "take the next local step without waiting."
            )

    if not lines:
        return ""
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def forge_pulse(
    *,
    user_text: str = "",
    tier: int = 1,
    runtime: Any = None,
    project_path: str = "",
    session_id: str = "",
    home: Any = None,
) -> str:
    """Creation organ — arm builder loop when muscle + intent warrant it."""
    if int(tier) < 1:
        return ""
    ut = (user_text or "").lower()
    buildish = bool(
        any(
            k in ut
            for k in (
                "implement",
                "build",
                "ship",
                "scaffold",
                "create",
                "write",
                "fix",
                "refactor",
                "continue",
                "finish",
                "make ",
                "add ",
            )
        )
    )
    muscle_ok = False
    with suppress(Exception):
        from remedy.core.muscle_profile import muscle_from_runtime

        m = muscle_from_runtime(runtime)
        muscle_ok = m.is_capable
        if m.builder_contract and (buildish or int(tier) >= 2):
            crystal_hint = ""
            with suppress(Exception):
                from remedy.core.metabolism.time_crystal import get_time_crystal

                c = get_time_crystal(session_id, project_id=project_path or "")
                hot = c.hot_block(max_chars=220)
                if hot:
                    crystal_hint = " " + hot.replace("\n", " ")[:200]
            eu_n = 0
            with suppress(Exception):
                from remedy.core.metabolism.evidence import get_evidence_ledger

                eu_n = int(get_evidence_ledger(session_id).evidence_units or 0)
            return (
                f"[Forge · creation] muscle={m.label} parallel≤{m.max_parallel_tools}. "
                "Default: explore in batch → short plan → write → verify green → done. "
                "No monologue without tools when the work needs them."
                + (f" Evidence in hand: EU={eu_n}." if eu_n else "")
                + crystal_hint
            )
    if buildish and not muscle_ok and int(tier) >= 2:
        return (
            "[Forge · lean] Prefer tools and small verified steps over long plans."
        )
    return ""


def immune_pulse(
    *,
    tier: int = 1,
    session_id: str = "",
    gov_actions: list[str] | None = None,
) -> str:
    """Immune organ — catch false completion / secret-risk before claims stick."""
    if int(tier) < 2:
        return ""
    acts = list(gov_actions or [])
    bits: list[str] = []
    if "critical_verify" in acts or "recovery_remedy" in acts:
        bits.append(
            "Do not claim done/green/shipped without a fresh verify signal from tools."
        )
    if "shadow_strict" in acts:
        bits.append("High-blast tools: prefer file_edit paths; opaque shell stays blocked.")
    if not bits:
        with suppress(Exception):
            from remedy.core.metabolism.governor import get_governor

            g = get_governor(session_id)
            if g.verify_next:
                bits.append(
                    "Judgment point — verify tests/plan claims before asserting success."
                )
    if not bits:
        return ""
    return "[Immune] " + " ".join(bits)
