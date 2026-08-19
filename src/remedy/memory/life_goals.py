"""Durable life goals — arcs the partner holds across chats.

Session tasks tagged ``goal`` remain a chapter checklist. This store is the
source of truth for *their* life: horizon, next action, evidence, status.
No secrets. Local ``~/.remedy/life_goals.json`` only.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from remedy.core.atomic_json import scratch_path

HORIZONS = ("week", "season", "life")
STATUSES = ("open", "active", "paused", "done", "dropped")
OPEN_STATUSES = frozenset({"open", "active"})
_MAX_GOALS = 80
_MAX_EVIDENCE = 24
_lock = threading.Lock()

# Command/operational shapes that are NOT life goals — keep them off the board.
_OPERATIONAL_RE = re.compile(
    r"(?i)(^\s*host:|serve\.py|\bport\s+\d|\brun\s+python\b|\bnpm\s+(run|install)\b|"
    r"\bpython\s+\S+\.py\b|\bgit\s+(push|commit|status)\b|open\s+browser|"
    r"localhost|127\.0\.0\.1|:\d{4}\b|--build\b|dev\s+build)"
)


def _looks_operational(text: str) -> bool:
    return bool(_OPERATIONAL_RE.search(text or ""))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _home(home_dir: str | Path | None) -> Path:
    if home_dir:
        return Path(home_dir).expanduser()
    try:
        from remedy.interfaces.config import load_config

        h = (load_config() or {}).get("home_dir")
        if h:
            return Path(str(h)).expanduser()
    except Exception:
        pass
    return Path.home() / ".remedy"


@dataclass
class LifeGoal:
    id: str
    title: str
    why: str = ""
    horizon: str = "season"
    done_looks_like: str = ""
    next_action: str = ""
    next_by: str = ""
    status: str = "open"
    evidence: list[str] = field(default_factory=list)
    source: str = "chat"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_public(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LifeGoal:
        hz = str(raw.get("horizon") or "season").strip().lower()
        if hz not in HORIZONS:
            hz = "season"
        st = str(raw.get("status") or "open").strip().lower()
        if st not in STATUSES:
            st = "open"
        ev = [str(x).strip()[:240] for x in (raw.get("evidence") or []) if str(x).strip()]
        return cls(
            id=str(raw.get("id") or uuid4().hex[:12]),
            title=str(raw.get("title") or "Untitled").strip()[:200],
            why=str(raw.get("why") or raw.get("description") or "").strip()[:400],
            horizon=hz,
            done_looks_like=str(raw.get("done_looks_like") or "").strip()[:280],
            next_action=str(raw.get("next_action") or "").strip()[:280],
            next_by=str(raw.get("next_by") or "").strip()[:40],
            status=st,
            evidence=ev[:_MAX_EVIDENCE],
            source=str(raw.get("source") or "chat").strip()[:32] or "chat",
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
        )


class LifeGoalStore:
    """Single JSON file of life goals under the Remedy home."""

    def __init__(self, home_dir: str | Path | None = None) -> None:
        self.home = _home(home_dir)
        self.path = self.home / "life_goals.json"
        self.last_pulse_at: float = 0.0
        self.last_drive_at: float = 0.0
        self.last_digest_at: float = 0.0
        self.last_steps: list[dict[str, Any]] = []
        self._disk_corrupt = False

    def _read_raw(self) -> dict[str, Any]:
        empty: dict[str, Any] = {
            "goals": [],
            "last_pulse_at": 0.0,
            "last_drive_at": 0.0,
            "last_digest_at": 0.0,
            "last_steps": [],
        }
        if not self.path.is_file():
            return empty
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError:
            return empty
        except json.JSONDecodeError:
            # Back up the unreadable file once and start fresh — a corrupt
            # read must NOT permanently disable all future saves (the old
            # behavior silently dropped every add/complete/patch until
            # restart, reporting success to the caller).
            with contextlib.suppress(OSError):
                bad = self.path.with_suffix(".corrupt")
                if not bad.exists():
                    self.path.replace(bad)
            self._disk_corrupt = False
            return empty
        self._disk_corrupt = False
        if not isinstance(raw, dict):
            return {
                "goals": raw if isinstance(raw, list) else [],
                "last_pulse_at": 0.0,
                "last_drive_at": 0.0,
                "last_digest_at": 0.0,
                "last_steps": [],
            }
        return raw

    def _coerce_steps(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            did = str(row.get("did") or "").strip()
            goal = str(row.get("goal") or "").strip()
            if not did and not goal:
                continue
            try:
                ts = float(row.get("ts") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            out.append(
                {
                    "ts": ts,
                    "goal": goal[:200],
                    "did": did[:280],
                    "next": str(row.get("next") or "")[:280],
                    "path": str(row.get("path") or "")[:400],
                    "kind": str(row.get("kind") or "")[:32],
                }
            )
        return out[-12:]

    def _load(self) -> list[LifeGoal]:
        raw = self._read_raw()
        try:
            self.last_pulse_at = float(raw.get("last_pulse_at") or 0)
        except (TypeError, ValueError):
            self.last_pulse_at = 0.0
        try:
            self.last_drive_at = float(raw.get("last_drive_at") or 0)
        except (TypeError, ValueError):
            self.last_drive_at = 0.0
        try:
            self.last_digest_at = float(raw.get("last_digest_at") or 0)
        except (TypeError, ValueError):
            self.last_digest_at = 0.0
        self.last_steps = self._coerce_steps(raw.get("last_steps"))
        rows = raw.get("goals") if isinstance(raw, dict) else []
        if not isinstance(rows, list):
            return []
        out: list[LifeGoal] = []
        for row in rows:
            if isinstance(row, dict) and str(row.get("title") or "").strip():
                out.append(LifeGoal.from_dict(row))
        return out[:_MAX_GOALS]

    def _save(self, goals: list[LifeGoal]) -> None:
        if self._disk_corrupt:
            return
        self.home.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": _now(),
            "last_pulse_at": self.last_pulse_at,
            "last_drive_at": self.last_drive_at,
            "last_digest_at": self.last_digest_at,
            "last_steps": self.last_steps[-12:],
            "goals": [g.to_public() for g in goals[:_MAX_GOALS]],
        }
        tmp = scratch_path(self.path)
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        last: OSError | None = None
        for i in range(16):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError as e:
                # Windows: dest briefly locked by a concurrent read / indexer.
                last = e
                time.sleep(0.015 * (i + 1))
            except OSError as e:
                last = e
                if getattr(e, "winerror", None) not in (5, 32):
                    raise
                time.sleep(0.015 * (i + 1))
        if last is not None:
            raise last

    def list(self, *, include_closed: bool = False) -> list[LifeGoal]:
        with _lock:
            goals = self._load()
        if include_closed:
            return goals
        return [g for g in goals if g.status in OPEN_STATUSES]

    def get(self, goal_id: str) -> LifeGoal | None:
        needle = (goal_id or "").strip()
        if not needle:
            return None
        for g in self.list(include_closed=True):
            if g.id == needle:
                return g
        return None

    def find(self, title: str) -> LifeGoal | None:
        needle = (title or "").strip().lower()
        if not needle:
            return None
        open_first = self.list(include_closed=False) + [
            g for g in self.list(include_closed=True) if g.status not in OPEN_STATUSES
        ]
        for g in open_first:
            if needle in g.title.lower() or needle == g.id.lower():
                return g
        return None

    def add(
        self,
        title: str,
        *,
        why: str = "",
        horizon: str = "season",
        done_looks_like: str = "",
        next_action: str = "",
        next_by: str = "",
        source: str = "chat",
    ) -> LifeGoal:
        t = (title or "").strip()
        if not t:
            raise ValueError("title required")
        existing = self.find(t)
        if existing and existing.status in OPEN_STATUSES:
            if why and not existing.why:
                existing.why = why[:400]
            if next_action:
                existing.next_action = next_action[:280]
            if done_looks_like and not existing.done_looks_like:
                existing.done_looks_like = done_looks_like[:280]
            existing.updated_at = _now()
            with _lock:
                goals = self._load()
                goals = [existing if g.id == existing.id else g for g in goals]
                self._save(goals)
            return existing
        goal = LifeGoal(
            id=uuid4().hex[:12],
            title=t[:200],
            why=(why or "").strip()[:400],
            horizon=horizon if horizon in HORIZONS else "season",
            done_looks_like=(done_looks_like or "").strip()[:280],
            next_action=(next_action or "").strip()[:280],
            next_by=(next_by or "").strip()[:40],
            status="active" if not self.list() else "open",
            source=source,
        )
        with _lock:
            goals = self._load()
            goals.insert(0, goal)
            self._save(goals)
        return goal

    def complete(self, title: str, *, evidence: str = "") -> LifeGoal | None:
        g = self.find(title)
        if g is None or g.status in ("done", "dropped"):
            return None
        if evidence.strip():
            g.evidence = (g.evidence + [evidence.strip()[:240]])[:_MAX_EVIDENCE]
        g.status = "done"
        g.updated_at = _now()
        with _lock:
            goals = self._load()
            goals = [g if x.id == g.id else x for x in goals]
            self._save(goals)
        return g

    def delete(self, goal_id: str) -> bool:
        """Hard-remove ONE goal by exact id (or exact title) — never a
        substring, so 'Ship app' can't also wipe 'Ship app v2'."""
        needle = (goal_id or "").strip().lower()
        if not needle:
            return False
        with _lock:
            goals = self._load()
            target = next(
                (g for g in goals if g.id.lower() == needle), None
            ) or next(
                (g for g in goals if g.title.strip().lower() == needle), None
            )
            if target is None:
                return False
            self._save([g for g in goals if g.id != target.id])
        return True

    def set_next(self, title: str, action: str, *, next_by: str = "") -> LifeGoal | None:
        g = self.find(title)
        if g is None or g.status not in OPEN_STATUSES:
            return None
        g.next_action = (action or "").strip()[:280]
        if next_by.strip():
            g.next_by = next_by.strip()[:40]
        g.status = "active"
        g.updated_at = _now()
        with _lock:
            goals = [g if x.id == g.id else x for x in self._load()]
            self._save(goals)
        return g

    def active(self) -> LifeGoal | None:
        open_g = self.list()
        for g in open_g:
            if g.status == "active":
                return g
        return open_g[0] if open_g else None

    def open_count(self) -> int:
        return len(self.list())

    def pause(self, title: str) -> LifeGoal | None:
        g = self.find(title)
        if g is None or g.status not in OPEN_STATUSES:
            return None
        g.status = "paused"
        g.updated_at = _now()
        with _lock:
            goals = [g if x.id == g.id else x for x in self._load()]
            self._save(goals)
        return g

    def patch(self, goal_id: str, **fields: Any) -> LifeGoal | None:
        g = self.get(goal_id) or self.find(goal_id)
        if g is None:
            return None
        if "title" in fields and str(fields.get("title") or "").strip():
            g.title = str(fields["title"]).strip()[:200]
        if "status" in fields and str(fields["status"] or "") in STATUSES:
            g.status = str(fields["status"])
        if "next_action" in fields and fields["next_action"] is not None:
            g.next_action = str(fields["next_action"]).strip()[:280]
            if g.next_action and g.status == "open":
                g.status = "active"
        if "next_by" in fields and fields["next_by"] is not None:
            g.next_by = str(fields["next_by"]).strip()[:40]
        if "done_looks_like" in fields and fields["done_looks_like"] is not None:
            g.done_looks_like = str(fields["done_looks_like"]).strip()[:280]
        if "why" in fields and fields["why"] is not None:
            g.why = str(fields["why"]).strip()[:400]
        ev = str(fields.get("evidence") or "").strip()
        if ev:
            g.evidence = (g.evidence + [ev[:240]])[:_MAX_EVIDENCE]
        if g.status == "done" and ev:
            pass
        g.updated_at = _now()
        with _lock:
            goals = [g if x.id == g.id else x for x in self._load()]
            self._save(goals)
        return g

    def record_pulse(self) -> None:
        import time as _time

        stamp = float(_time.time())
        with _lock:
            goals = self._load()
            self.last_pulse_at = stamp
            self._save(goals)

    def record_drive(self, step: dict[str, Any] | None = None) -> None:
        import time as _time

        stamp = float(_time.time())
        with _lock:
            goals = self._load()
            self.last_drive_at = stamp
            if isinstance(step, dict) and (step.get("did") or step.get("goal")):
                # Don't surface operational/command instructions as life
                # "activity" — that clutters the partner's board with things
                # like "HOST: start serve.py" (not a life goal step).
                blob = f"{step.get('goal') or ''} {step.get('did') or ''}"
                if not _looks_operational(blob):
                    row = {
                        "ts": stamp,
                        "goal": str(step.get("goal") or "")[:200],
                        "did": str(step.get("did") or "")[:280],
                        "next": str(step.get("next") or "")[:280],
                        "path": str(step.get("path") or "")[:400],
                        "kind": str(step.get("kind") or "")[:32],
                    }
                    self.last_steps = (list(self.last_steps) + [row])[-12:]
            self._save(goals)

    def clear_activity(self) -> None:
        """Wipe the recorded drive steps (the 'Last:' board pill history)."""
        with _lock:
            goals = self._load()
            self.last_steps = []
            self._save(goals)

    def record_digest(self) -> None:
        import time as _time

        stamp = float(_time.time())
        with _lock:
            goals = self._load()
            self.last_digest_at = stamp
            self._save(goals)

    def last_step(self) -> dict[str, Any] | None:
        with _lock:
            self._load()
            if not self.last_steps:
                return None
            return dict(self.last_steps[-1])


def store_for(home_dir: str | Path | None = None) -> LifeGoalStore:
    return LifeGoalStore(home_dir)


def format_goals_markdown(goals: list[LifeGoal]) -> str:
    if not goals:
        return "No life goals yet. Say what you want to finish, or `/goal <title>`."
    lines = ["**Life goals**"]
    for g in goals[:20]:
        bits = [f"- [{g.status}] **{g.title}** ({g.horizon})"]
        if g.next_action:
            due = f" by {g.next_by}" if g.next_by else ""
            bits.append(f"  next: {g.next_action}{due}")
        elif g.status in OPEN_STATUSES:
            bits.append("  next: *(none yet — name one concrete move)*")
        if g.done_looks_like:
            bits.append(f"  done looks like: {g.done_looks_like}")
        lines.extend(bits)
    return "\n".join(lines)


_LIFE_GOAL_STATE_RE = re.compile(
    r"(?i)\b(i want to|i'm going to|my goal is|this year i(?:'ll| will)|"
    r"help me (?:finish|get|become|write|ship))\b"
)


def looks_like_life_goal_statement(message: str) -> bool:
    """True when the user is stating a durable life aim, not coding work."""
    msg = (message or "").strip()
    if not msg or "\n" in msg or len(msg) > 200:
        return False
    try:
        from remedy.memory.living import turn_kind

        kind = turn_kind(msg)
    except Exception:
        kind = "general"
    if kind not in ("life", "goal"):
        if not _LIFE_GOAL_STATE_RE.search(msg):
            return False
    try:
        from remedy.core.local_agent_optimize import message_wants_implement

        if message_wants_implement(msg):
            return False
    except Exception:
        pass
    try:
        from remedy.core.build_engine import looks_like_build_request

        if looks_like_build_request(msg):
            return False
    except Exception:
        pass
    return bool(_LIFE_GOAL_STATE_RE.search(msg) or kind in ("life", "goal"))


def drive_markdown(home_dir: str | Path | None = None) -> str:
    """One active goal + next action — for L0 / greet drive."""
    store = LifeGoalStore(home_dir)
    g = store.active()
    if g is None:
        return ""
    due = f" by {g.next_by}" if g.next_by else ""
    nxt = g.next_action or "name one concrete next move"
    return (
        f"**Toward {g.title}** ({g.horizon})\n"
        f"Next: {nxt}{due}\n"
        "I’ll take the next local step I can (notes in Documents/Remedy Life) without waiting."
    )


def weekly_pulse(home_dir: str | Path | None = None) -> dict[str, Any]:
    """Local review: moved / stalled / no next action. Does not nag."""
    store = LifeGoalStore(home_dir)
    store._load()
    open_g = store.list()
    moved: list[str] = []
    stalled: list[str] = []
    need_next: list[str] = []
    now = datetime.now(UTC)
    for g in open_g:
        try:
            updated = datetime.fromisoformat(g.updated_at.replace("Z", "+00:00"))
            if updated.tzinfo is None:
                # Legacy / externally-edited naive stamp — treat as UTC so the
                # aware `now - updated` below never raises TypeError.
                from datetime import UTC as _UTC

                updated = updated.replace(tzinfo=_UTC)
        except (ValueError, TypeError):
            updated = now
        age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
        if not g.next_action:
            need_next.append(g.title)
        elif age_days >= 7:
            stalled.append(g.title)
        elif g.evidence or age_days < 3:
            moved.append(g.title)
        else:
            stalled.append(g.title)
    lines = ["**This week**"]
    if moved:
        lines.append("Moved: " + ", ".join(moved[:5]))
    if stalled:
        lines.append("Stalled: " + ", ".join(stalled[:5]))
    if need_next:
        lines.append("Need a next action: " + ", ".join(need_next[:5]))
    if not moved and not stalled and not need_next:
        lines.append("No open life goals. Say what you want to finish.")
    active = store.active()
    if active and active.next_action:
        lines.append(f"One move: {active.next_action}" + (f" ({active.next_by})" if active.next_by else ""))
    return {
        "markdown": "\n".join(lines),
        "moved": moved,
        "stalled": stalled,
        "need_next": need_next,
        "open": len(open_g),
    }


def pulse_due(home_dir: str | Path | None = None, *, days: float = 7.0) -> bool:
    import time as _time

    store = LifeGoalStore(home_dir)
    store._load()
    if store.open_count() == 0:
        return False
    if store.last_pulse_at <= 0:
        return True
    return (_time.time() - store.last_pulse_at) >= days * 86400.0


LIFE_GOAL_TOOL_NAMES = frozenset(
    {
        "goal_add",
        "goal_list",
        "goal_complete",
        "goal_verify",
        "goal_set_next",
        "goal_drive",
        "memory_search",
        "memory_save",
        "web_search",
        "web_fetch",
        "companion_context",
        "help_read",
        "help_list",
        "assistant_calendar_list",
        "assistant_calendar_add",
        "assistant_reminder",
    }
)
