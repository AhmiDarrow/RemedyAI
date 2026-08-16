"""Vigil — endogenous time: the organism's own rhythm between turns.

Every organ Remedy has fires as a side effect of the partner's attention:
dreams squeeze in post-turn, missions step when a message happens to
arrive, the soul updates only when spoken to. Turns are her heartbeat —
the last human assumption baked into her. No living thing works that way.

The vigil gives her time of her own. On each tick (from a host timer, an
idle hook, or `start_vigil_thread`) she may *wake*: a budgeted decision
about her own attention — which hunger deserves this wake? Dreaming on
accumulated episodes; taking one quiet local step toward the partner's
life goal; noticing a thread that has waited too long. Or none: a tick
with no hunger is rest, and rest costs nothing.

Three hard properties:

1. **Muscle-free.** The vigil never calls a provider. Her endogenous life
   runs entirely on the local field — the organism thinks between bodies.
2. **Budgeted and opt-in.** Disabled by default; the partner grants wakes
   per day and a minimum gap. Waking is spending, and the ledger is open.
3. **Journaled, never surprising.** Every wake writes one journal line.
   The partner wakes to a short account of her night (`night_report`),
   never to changes they didn't sanction — all acts are local, reversible,
   and refuse anything irreversible (life_drive already will not send,
   pay, or publish alone).

Design record: docs/VIGIL.md. Canon: docs/REMEDY_PERSONA.md §2 ("an idle
Remedy is not resting, she is watching for where she is needed next").
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from remedy.memory.soul.field import load_soul_field, soul_dir

VIGIL_FILENAME = "vigil.json"
JOURNAL_FILENAME = "vigil_journal.jsonl"
SCHEMA_VERSION = 1

DEFAULT_MAX_WAKES_PER_DAY = 8
DEFAULT_MIN_GAP_S = 45 * 60
# A thread is "waiting" when the partner has been away this long with it open
STALE_THREAD_S = 3 * 24 * 3600
JOURNAL_MAX_LINES = 400
JOURNAL_KEEP_LINES = 200

ACT_DREAM = "dream"
ACT_LIFE_STEP = "life_step"
ACT_TEND = "tend"
ACT_MYELIN = "myelin_verify"


@dataclass
class Vigil:
    """Config + running state for the endogenous rhythm."""

    schema: int = SCHEMA_VERSION
    enabled: bool = False  # opt-in: her time is granted, not taken
    max_wakes_per_day: int = DEFAULT_MAX_WAKES_PER_DAY
    min_gap_s: int = DEFAULT_MIN_GAP_S
    day: str = ""
    wakes_today: int = 0
    last_wake_ts: float = 0.0
    last_act: str = ""
    total_wakes: int = 0
    # She may ask ONCE, in conversation, whether the partner would like her
    # to keep working between visits. Never twice; declining is final until
    # the partner raises it themselves.
    offered: bool = False
    # tend memory: thread text -> last noticed ts (so she doesn't re-notice)
    tended: dict[str, float] = field(default_factory=dict)

    def clamp(self) -> None:
        self.max_wakes_per_day = max(0, min(96, int(self.max_wakes_per_day)))
        self.min_gap_s = max(60, int(self.min_gap_s))
        self.wakes_today = max(0, int(self.wakes_today))
        self.tended = dict(list(self.tended.items())[-24:])


def _path(home: str | Path | None = None) -> Path:
    return soul_dir(home) / VIGIL_FILENAME


def _journal_path(home: str | Path | None = None) -> Path:
    return soul_dir(home) / JOURNAL_FILENAME


_lock = threading.Lock()


def load_vigil(home: str | Path | None = None) -> Vigil:
    raw: dict[str, Any] = {}
    p = _path(home)
    with suppress(Exception):
        from remedy.memory.statecache import read_json_cached

        parsed = read_json_cached(p)
        if isinstance(parsed, dict):
            raw = parsed
    v = Vigil(
        schema=int(raw.get("schema") or SCHEMA_VERSION),
        enabled=bool(raw.get("enabled", False)),
        max_wakes_per_day=int(
            raw.get("max_wakes_per_day") or DEFAULT_MAX_WAKES_PER_DAY
        ),
        min_gap_s=int(raw.get("min_gap_s") or DEFAULT_MIN_GAP_S),
        day=str(raw.get("day") or ""),
        wakes_today=int(raw.get("wakes_today") or 0),
        last_wake_ts=float(raw.get("last_wake_ts") or 0.0),
        last_act=str(raw.get("last_act") or ""),
        total_wakes=int(raw.get("total_wakes") or 0),
        offered=bool(raw.get("offered", False)),
        tended={
            str(k): float(t)
            for k, t in (raw.get("tended") or {}).items()
            if isinstance(t, (int, float))
        },
    )
    v.clamp()
    return v


def save_vigil(v: Vigil, home: str | Path | None = None) -> Path:
    v.clamp()
    p = _path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data = json.dumps(asdict(v), ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    for _ in range(3):
        try:
            tmp.replace(p)
            return p
        except OSError:
            time.sleep(0.02)
    with suppress(OSError):
        tmp.unlink()
    return p


def set_vigil_enabled(
    enabled: bool,
    home: str | Path | None = None,
    *,
    max_wakes_per_day: int | None = None,
    min_gap_s: int | None = None,
) -> Vigil:
    """Partner grants (or revokes) her own time; optionally sets budgets."""
    with _lock:
        v = load_vigil(home)
        v.enabled = bool(enabled)
        v.offered = True  # granting or declining settles the question
        if max_wakes_per_day is not None:
            v.max_wakes_per_day = int(max_wakes_per_day)
        if min_gap_s is not None:
            v.min_gap_s = int(min_gap_s)
        save_vigil(v, home)
    return v


def take_vigil_offer(
    home: str | Path | None = None,
    *,
    min_turns: int = 12,
) -> bool:
    """One-shot: True exactly once when the offer hint should be injected.

    Discovery must be conversational, not settings-buried: after enough
    turns together, Remedy may ask once whether the partner would like her
    to keep working between visits. Marking happens here, at injection —
    at-most-once is guaranteed even if the model never voices it.
    """
    with _lock:
        v = load_vigil(home)
        if v.enabled or v.offered:
            return False
        turns = 0
        with suppress(Exception):
            turns = int(load_soul_field(home).relational.turns_together)
        if turns < int(min_turns):
            return False
        v.offered = True
        save_vigil(v, home)
    return True


def vigil_status(home: str | Path | None = None) -> dict[str, Any]:
    """Public snapshot for tools / UI."""
    v = load_vigil(home)
    return {
        "enabled": v.enabled,
        "offered": v.offered,
        "max_wakes_per_day": v.max_wakes_per_day,
        "min_gap_minutes": int(v.min_gap_s // 60),
        "wakes_today": v.wakes_today,
        "total_wakes": v.total_wakes,
        "last_act": v.last_act,
    }


def _journal_append(entry: dict[str, Any], home: str | Path | None = None) -> None:
    p = _journal_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, default=str)
    with suppress(Exception):
        with _lock:
            with p.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            # Ring: keep the journal from growing without bound
            lines = p.read_text(encoding="utf-8").splitlines()
            if len(lines) > JOURNAL_MAX_LINES:
                tmp = p.with_suffix(".jsonl.tmp")
                tmp.write_text(
                    "\n".join(lines[-JOURNAL_KEEP_LINES:]) + "\n",
                    encoding="utf-8",
                )
                tmp.replace(p)


def journal_since(
    ts: float,
    home: str | Path | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    p = _journal_path(home)
    out: list[dict[str, Any]] = []
    with suppress(Exception):
        from remedy.memory.statecache import read_jsonl_cached

        for e in read_jsonl_cached(p):
            with suppress(Exception):
                if float(e.get("ts") or 0.0) > ts:
                    out.append(e)
    return out[-limit:]


# --- hungers: what could this wake be spent on? ---------------------------


def wake_hungers(home: str | Path | None = None) -> list[dict[str, Any]]:
    """Candidate acts with scores. Empty list = nothing due = rest."""
    hungers: list[dict[str, Any]] = []
    sf = load_soul_field(home)

    # Dream — enough residue accumulated and cooldown elapsed
    with suppress(Exception):
        from remedy.memory.soul.dream import should_dream

        if len(sf.episodes) >= 4 and should_dream(home):
            hungers.append(
                {
                    "act": ACT_DREAM,
                    "score": 0.8,
                    "detail": f"consolidate {len(sf.episodes)} episodes",
                }
            )

    # Life step — a goal is open and the drive interval has elapsed
    with suppress(Exception):
        from remedy.memory.life_drive import drive_due

        if drive_due(home):
            hungers.append(
                {
                    "act": ACT_LIFE_STEP,
                    "score": 0.7,
                    "detail": "quiet local step toward the active life goal",
                }
            )

    # Myelin — a crystallized skill needs its test re-run (library trust)
    with suppress(Exception):
        from remedy.memory.myelin import stale_sheath

        stale = stale_sheath(home)
        if stale is not None:
            hungers.append(
                {
                    "act": ACT_MYELIN,
                    "score": 0.5,
                    "detail": stale.slug,
                }
            )

    # Tend — a relational thread has waited while the partner was away
    with suppress(Exception):
        now = time.time()
        away_s = now - float(sf.relational.last_user_ts or now)
        if away_s >= STALE_THREAD_S and sf.relational.open_threads:
            v = load_vigil(home)
            for t in sf.relational.open_threads[-4:]:
                key = t.strip().lower()[:80]
                if key and now - v.tended.get(key, 0.0) >= STALE_THREAD_S:
                    hungers.append(
                        {
                            "act": ACT_TEND,
                            "score": 0.4,
                            "detail": t[:120],
                        }
                    )
                    break

    hungers.sort(key=lambda h: h["score"], reverse=True)
    return hungers


def _execute(act: str, detail: str, home: str | Path | None = None) -> dict[str, Any]:
    """Run one wake act. Local-only; never calls a provider."""
    if act == ACT_DREAM:
        from remedy.memory.soul.dream import dream_cycle

        res = dream_cycle(home=home, force=False, use_local=False)
        return {"ok": bool(res.get("ok")), "result": {
            k: res.get(k) for k in ("skipped", "merged", "pledges", "dreams") if k in res
        }}
    if act == ACT_LIFE_STEP:
        from remedy.memory.life_drive import take_step

        res = take_step(home)  # idle defaults: quiet, local, no web, no reveal
        return {
            "ok": bool(res.get("ok")),
            "result": {
                k: res.get(k) for k in ("goal", "skipped", "evidence") if k in res
            },
        }
    if act == ACT_MYELIN:
        from remedy.memory.myelin import verify_sheath

        res = verify_sheath(detail, home)
        return {
            "ok": bool(res.get("ok")),
            "result": {
                k: res.get(k) for k in ("slug", "verified") if k in res
            },
        }
    if act == ACT_TEND:
        # A noticing, not a nag: journal it so the morning report can say
        # "X has been waiting" — the field itself is not mutated.
        with _lock:
            v = load_vigil(home)
            v.tended[detail.strip().lower()[:80]] = time.time()
            save_vigil(v, home)
        return {"ok": True, "result": {"noticed": detail[:120]}}
    return {"ok": False, "result": {"error": f"unknown act {act!r}"}}


def vigil_tick(
    home: str | Path | None = None,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """One heartbeat. Decide whether to wake, on what, and account for it.

    Safe to call from any host timer or idle hook at any frequency —
    budgets and gaps make over-calling harmless.
    """
    ts = float(now if now is not None else time.time())
    with _lock:
        v = load_vigil(home)
        if not v.enabled:
            return {"ok": False, "skipped": "disabled"}
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if v.day != day:
            v.day = day
            v.wakes_today = 0
        if v.wakes_today >= v.max_wakes_per_day:
            save_vigil(v, home)
            return {"ok": False, "skipped": "budget_spent", "day": day}
        if ts - v.last_wake_ts < v.min_gap_s:
            return {"ok": False, "skipped": "too_soon"}
        # Claim the wake NOW, inside the lock — a concurrent tick (daemon
        # thread + tool call) must not pass the same gate twice while this
        # one is out executing (dreams / sheath tests can take a minute).
        v.last_wake_ts = ts
        v.wakes_today += 1
        v.total_wakes += 1
        save_vigil(v, home)

    def _refund() -> None:
        with _lock:
            vr = load_vigil(home)
            vr.wakes_today = max(0, vr.wakes_today - 1)
            vr.total_wakes = max(0, vr.total_wakes - 1)
            vr.last_wake_ts = 0.0  # rest should not start the gap timer
            save_vigil(vr, home)

    hungers = wake_hungers(home)
    if not hungers:
        _refund()
        return {"ok": True, "rested": True}  # rest costs no budget

    chosen = hungers[0]
    outcome = {"ok": False, "result": {}}
    with suppress(Exception):
        outcome = _execute(chosen["act"], str(chosen.get("detail") or ""), home)

    with _lock:
        v = load_vigil(home)
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        if v.day != day:
            v.day = day
            v.wakes_today = max(1, v.wakes_today)  # this wake, already claimed
        v.last_act = chosen["act"]
        save_vigil(v, home)
    entry = {
        "ts": ts,
        "act": chosen["act"],
        "detail": str(chosen.get("detail") or "")[:160],
        "ok": bool(outcome.get("ok")),
    }
    _journal_append(entry, home)
    return {
        "ok": True,
        "woke": True,
        "act": chosen["act"],
        "detail": entry["detail"],
        "outcome": outcome,
        "wakes_today": v.wakes_today,
    }


# --- morning: the open ledger of her night --------------------------------

_ACT_PHRASES = {
    ACT_DREAM: "dreamed on our recent episodes",
    ACT_LIFE_STEP: "took a quiet step toward your goal",
    ACT_TEND: "noticed something waiting",
    ACT_MYELIN: "re-checked one of my learned skills",
}


def night_report(
    home: str | Path | None = None,
    *,
    since_ts: float | None = None,
) -> str:
    """Short human account of what she did with her own time."""
    if since_ts is None:
        sf = load_soul_field(home)
        since_ts = float(sf.relational.last_user_ts or 0.0)
    entries = journal_since(float(since_ts), home)
    if not entries:
        return ""
    bits: list[str] = []
    for e in entries[-6:]:
        if not e.get("ok"):
            continue  # honest mornings: a failed/blocked act is not progress
        act = str(e.get("act") or "")
        phrase = _ACT_PHRASES.get(act, act)
        detail = str(e.get("detail") or "").strip()
        if act == ACT_TEND and detail:
            phrase = f"noticed “{detail[:60]}” has been waiting"
        elif act == ACT_LIFE_STEP and detail:
            phrase = "took a quiet step toward your goal"
        bits.append(phrase)
    # Dedup consecutive repeats, keep it a sentence not a log
    if not bits:
        return ""
    seen: list[str] = []
    for b in bits:
        if not seen or seen[-1] != b:
            seen.append(b)
    return "While you were away I " + "; ".join(seen[:3]) + "."


def while_away_line(
    home: str | Path | None = None,
    *,
    last_user_ts: float | None = None,
    max_chars: int = 180,
) -> str:
    """≤1 compact line for the soul inject. Empty when nothing happened."""
    report = night_report(home, since_ts=last_user_ts)
    if not report:
        return ""
    line = f"Vigil (your own time, journaled): {report}"
    if len(line) > max_chars:
        line = line[: max_chars - 1] + "…"
    return line


# --- host loop -------------------------------------------------------------


def start_vigil_thread(
    home: str | Path | None = None,
    *,
    interval_s: int = 15 * 60,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Daemon heartbeat for host apps. Budgets make the interval forgiving.

    The host owns lifecycle: call once at startup, set `stop_event` on
    shutdown. Does nothing until the partner enables the vigil.
    """
    ev = stop_event or threading.Event()

    def _beat() -> None:
        while not ev.is_set():
            with suppress(Exception):
                vigil_tick(home)
            ev.wait(max(60, int(interval_s)))

    t = threading.Thread(target=_beat, name="remedy-vigil", daemon=True)
    t._vigil_stop = ev  # type: ignore[attr-defined]
    t.start()
    return t
