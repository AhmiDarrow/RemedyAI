"""On-disk build ledger — resume mid-ship across sessions/tabs.

Stores under ``{project}/.remedy-build/ledger.json`` when a project is bound,
else ``~/.remedy/builds/{fingerprint}/ledger.json``.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BuildLedgerEntry:
    """Durable machine build progress for one goal/project."""

    goal: str = ""
    phase: str = "scout"
    project_path: str = ""
    verify_command: str = ""
    oracle_ok: bool | None = None
    last_verify_ok: bool | None = None
    last_verify_summary: str = ""
    paths_touched: list[str] = field(default_factory=list)
    explore_steps: int = 0
    write_steps: int = 0
    verify_steps: int = 0
    repair_steps: int = 0
    muscle_tier: str = ""
    session_id: str = ""
    updated_ts: float = field(default_factory=time.time)
    created_ts: float = field(default_factory=time.time)
    hops: list[dict[str, Any]] = field(default_factory=list)  # reducer hops

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BuildLedgerEntry:
        raw = raw or {}
        return cls(
            goal=str(raw.get("goal") or "")[:400],
            phase=str(raw.get("phase") or "scout"),
            project_path=str(raw.get("project_path") or ""),
            verify_command=str(raw.get("verify_command") or ""),
            oracle_ok=raw.get("oracle_ok"),
            last_verify_ok=raw.get("last_verify_ok"),
            last_verify_summary=str(raw.get("last_verify_summary") or "")[:2000],
            paths_touched=list(raw.get("paths_touched") or [])[:40],
            explore_steps=int(raw.get("explore_steps") or 0),
            write_steps=int(raw.get("write_steps") or 0),
            verify_steps=int(raw.get("verify_steps") or 0),
            repair_steps=int(raw.get("repair_steps") or 0),
            muscle_tier=str(raw.get("muscle_tier") or ""),
            session_id=str(raw.get("session_id") or ""),
            updated_ts=float(raw.get("updated_ts") or time.time()),
            created_ts=float(raw.get("created_ts") or time.time()),
            hops=list(raw.get("hops") or [])[-40:],
        )


def _home(home: str | Path | None = None) -> Path:
    import os

    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def ledger_dir_for_project(
    project_path: str | Path | None,
    *,
    home: str | Path | None = None,
) -> Path:
    """Prefer project-local .remedy-build; else home/builds/<hash>."""
    if project_path:
        p = Path(project_path).expanduser()
        with suppress(Exception):
            if p.is_file():
                p = p.parent
            if p.is_dir():
                d = p / ".remedy-build"
                d.mkdir(parents=True, exist_ok=True)
                return d
    key = hashlib.sha256(str(project_path or "none").encode("utf-8")).hexdigest()[:16]
    d = _home(home) / "builds" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path(
    project_path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    return ledger_dir_for_project(project_path, home=home) / "ledger.json"


def load_ledger(
    project_path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> BuildLedgerEntry | None:
    path = ledger_path(project_path, home=home)
    if not path.is_file():
        return None
    with suppress(Exception):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return BuildLedgerEntry.from_dict(raw)
    return None


def save_ledger(
    entry: BuildLedgerEntry,
    *,
    home: str | Path | None = None,
) -> Path:
    entry.updated_ts = time.time()
    path = ledger_path(entry.project_path or None, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    data = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding="utf-8")
    with suppress(Exception):
        tmp.replace(path)
        return path
    path.write_text(data, encoding="utf-8")
    return path


def merge_turn_into_ledger(
    state: Any,
    *,
    project_path: str = "",
    session_id: str = "",
    home: str | Path | None = None,
) -> BuildLedgerEntry:
    """Upsert ledger from live BuildTurnState."""
    existing = load_ledger(project_path or getattr(state, "project_path", None), home=home)
    entry = existing or BuildLedgerEntry()
    if not entry.created_ts:
        entry.created_ts = time.time()
    goal = str(getattr(state, "goal", "") or entry.goal)
    if goal:
        entry.goal = goal[:400]
    entry.phase = str(getattr(state, "phase", None) or entry.phase)
    if project_path:
        entry.project_path = str(project_path)
    vcmd = str(getattr(state, "verify_command", "") or "")
    if vcmd:
        entry.verify_command = vcmd
    entry.oracle_ok = getattr(state, "oracle_ok", entry.oracle_ok)
    entry.last_verify_ok = getattr(state, "last_verify_ok", entry.last_verify_ok)
    summ = str(getattr(state, "last_verify_summary", "") or "")
    if summ:
        entry.last_verify_summary = summ[:2000]
    paths = list(getattr(state, "paths_touched", None) or [])
    for p in paths:
        if p and p not in entry.paths_touched:
            entry.paths_touched.append(p)
    entry.paths_touched = entry.paths_touched[-40:]
    entry.explore_steps = max(entry.explore_steps, int(getattr(state, "explore_steps", 0) or 0))
    entry.write_steps = max(entry.write_steps, int(getattr(state, "write_steps", 0) or 0))
    entry.verify_steps = max(entry.verify_steps, int(getattr(state, "verify_steps", 0) or 0))
    entry.repair_steps = max(entry.repair_steps, int(getattr(state, "repair_steps", 0) or 0))
    mt = str(getattr(state, "muscle_tier", "") or "")
    if mt:
        entry.muscle_tier = mt
    if session_id:
        entry.session_id = session_id
    save_ledger(entry, home=home)
    return entry


def append_hop(
    project_path: str,
    hop: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> BuildLedgerEntry:
    entry = load_ledger(project_path, home=home) or BuildLedgerEntry(
        project_path=project_path
    )
    entry.project_path = project_path or entry.project_path
    h = dict(hop)
    h["ts"] = time.time()
    entry.hops.append(h)
    entry.hops = entry.hops[-40:]
    save_ledger(entry, home=home)
    return entry


def resume_hint(project_path: str | Path | None = None, *, home: str | Path | None = None) -> str:
    """Human/machine inject line for continuing a mid-ship build."""
    entry = load_ledger(project_path, home=home)
    if entry is None:
        return ""
    if entry.phase == "done" and entry.last_verify_ok is True:
        return ""
    # Stale ledger with no writes and ancient ts — skip noise
    age_h = (time.time() - float(entry.updated_ts or 0)) / 3600.0
    if age_h > 72 and int(entry.write_steps or 0) <= 0:
        return ""
    lines = [
        "[Build ledger — resume mid-ship]",
        f"phase={entry.phase} goal={entry.goal[:160] or '—'}",
        f"verify_command={entry.verify_command or '(none discovered)'}",
        f"write_steps={entry.write_steps} verify_steps={entry.verify_steps} "
        f"last_verify_ok={entry.last_verify_ok}",
    ]
    if entry.paths_touched:
        lines.append("paths: " + ", ".join(entry.paths_touched[-8:]))
    if entry.last_verify_summary:
        lines.append("last_verify: " + entry.last_verify_summary[:300])
    if entry.hops:
        last = entry.hops[-1]
        lines.append(
            f"last_hop: unit={last.get('unit_id') or last.get('path')} "
            f"ok={last.get('ok')}"
        )
    # Explicit next action by phase
    phase = (entry.phase or "").lower()
    if phase in ("scout", "explore", "research"):
        lines.append(
            "Next: batch-read key paths, then PLAN a short checklist, then BUILD."
        )
    elif phase in ("plan",):
        lines.append("Next: BUILD with file_write/file_edit — no more explore-only turns.")
    elif phase in ("build", "repair", "write"):
        lines.append(
            "Next: finish remaining writes, then VERIFY (oracle/tests). "
            "Do not claim done until last_verify_ok is true."
        )
    elif phase in ("verify",):
        if entry.last_verify_ok is False:
            lines.append("Next: REPAIR failing verify, then re-run verify.")
        else:
            lines.append("Next: run verify_command if not green; only then mark done.")
    else:
        lines.append("Continue from this state — do not restart the whole build.")
    return "\n".join(lines)
