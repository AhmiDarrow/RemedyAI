"""On-disk build ledger — resume mid-ship across sessions/tabs.

Stores under ``{project}/.remedy-build/ledger.json`` when a project is bound,
else ``~/.remedy/builds/{fingerprint}/ledger.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# The ledger is written from the per-turn path AND the vigil thread — serialize
# read-modify-write so a concurrent save can't drop an appended hop or corrupt
# the file via a shared temp name.
_ledger_lock = threading.RLock()


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
    # Body memory — what just failed / what is still unverified
    write_set: list[str] = field(default_factory=list)
    last_error_vector: dict[str, Any] | None = None
    last_scoped_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BuildLedgerEntry:
        raw = raw or {}
        vec_raw = raw.get("last_error_vector")
        vec = vec_raw if isinstance(vec_raw, dict) and vec_raw else None
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
            write_set=_clean_path_list(raw.get("write_set") or []),
            last_error_vector=_compact_error_vector(vec) if vec else None,
            last_scoped_command=str(raw.get("last_scoped_command") or "")[:400],
        )


def _clean_path_list(raw: Any, *, limit: int = 40) -> list[str]:
    """Filesystem paths only — drop shell blobs leftover from older ledgers."""
    out: list[str] = []
    for item in raw or []:
        p = str(item or "").strip()
        if not p:
            continue
        if p.startswith(("git ", "gh ", "pytest", "python ")):
            continue
        if " && " in p or "\n" in p:
            continue
        if p not in out:
            out.append(p)
    return out[-limit:]


def _compact_error_vector(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the fields the next wake needs to act."""
    if not isinstance(raw, dict) or not raw:
        return None
    nodes = [str(x) for x in (raw.get("failing_nodes") or []) if x][:16]
    path_lines = [str(x) for x in (raw.get("path_lines") or []) if x][:20]
    snippets = [str(s)[:200] for s in (raw.get("snippets") or []) if s][:8]
    repair = str(raw.get("repair_command") or "")[:400]
    if not nodes and not path_lines and not repair and not snippets:
        return None
    return {
        "ok": bool(raw.get("ok")),
        "command": str(raw.get("command") or "")[:400],
        "exit_hint": str(raw.get("exit_hint") or "")[:80],
        "failing_nodes": nodes,
        "path_lines": path_lines,
        "snippets": snippets,
        "repair_command": repair,
    }


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


def build_tmp_dir(
    project_path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    """Return ``{project}/.remedy-build/tmp`` (created) for one-off helper scripts."""
    d = ledger_dir_for_project(project_path, home=home) / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_tmp_script_path(
    name: str,
    project_path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    """Safe path under ``.remedy-build/tmp/`` for temp agent scripts."""
    raw = (name or "helper.py").strip().replace("\\", "/").split("/")[-1]
    if not raw or raw in (".", ".."):
        raw = "helper.py"
    # Force a simple filename
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw)[:80]
    if not safe.endswith((".py", ".ps1", ".sh", ".cmd", ".bat")):
        safe = safe + ".py"
    return build_tmp_dir(project_path, home=home) / safe


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
    data = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
    # Unique temp per writer (pid+tid) so two concurrent saves never interleave
    # bytes into one ledger.tmp and promote torn JSON via replace.
    tmp = path.with_suffix(f".tmp{os.getpid()}.{threading.get_ident()}")
    with _ledger_lock:
        tmp.write_text(data, encoding="utf-8")
        # Atomic-or-nothing: keep the old file on a failed replace rather than
        # corrupting it (a torn write would make load_ledger drop all state).
        # Widen the retry budget with backoff, and RAISE if it never lands —
        # a silent drop made callers believe resume state persisted when it did
        # not (a build's progress would be lost with no signal).
        last_err: OSError | None = None
        delay = 0.02
        for _ in range(8):
            try:
                tmp.replace(path)
                return path
            except OSError as e:
                last_err = e
                time.sleep(delay)
                delay = min(delay * 2, 0.5)
        with suppress(OSError):
            tmp.unlink()
        raise OSError(
            f"save_ledger: could not persist {path} after retries: {last_err}"
        )


def merge_turn_into_ledger(
    state: Any,
    *,
    project_path: str = "",
    session_id: str = "",
    home: str | Path | None = None,
) -> BuildLedgerEntry:
    """Upsert ledger from live BuildTurnState."""
    with _ledger_lock:
        return _merge_turn_into_ledger_locked(
            state, project_path=project_path, session_id=session_id, home=home
        )


def _merge_turn_into_ledger_locked(
    state: Any,
    *,
    project_path: str = "",
    session_id: str = "",
    home: str | Path | None = None,
) -> BuildLedgerEntry:
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
    # Files only — never shell command blobs (ledger path hygiene)
    paths = list(getattr(state, "paths_touched", None) or [])
    with suppress(Exception):
        from remedy.core.build_engine import _is_filesystem_path

        paths = [p for p in paths if _is_filesystem_path(str(p))]
    for p in paths:
        if p and p not in entry.paths_touched:
            # Drop accidental shell-looking leftovers from older ledgers
            sp = str(p)
            if sp.startswith(("git ", "gh ", "pytest", "python ")):
                continue
            if " && " in sp or "\n" in sp:
                continue
            entry.paths_touched.append(p)
    entry.paths_touched = [
        p
        for p in entry.paths_touched
        if p
        and not str(p).startswith(("git ", "gh ", "pytest", "python "))
        and " && " not in str(p)
        and "\n" not in str(p)
    ][-40:]
    entry.explore_steps = max(entry.explore_steps, int(getattr(state, "explore_steps", 0) or 0))
    entry.write_steps = max(entry.write_steps, int(getattr(state, "write_steps", 0) or 0))
    entry.verify_steps = max(entry.verify_steps, int(getattr(state, "verify_steps", 0) or 0))
    entry.repair_steps = max(entry.repair_steps, int(getattr(state, "repair_steps", 0) or 0))
    mt = str(getattr(state, "muscle_tier", "") or "")
    if mt:
        entry.muscle_tier = mt
    if session_id:
        entry.session_id = session_id
    # Body: unverified writes + last red ticket (or clear on green)
    if entry.last_verify_ok is True:
        entry.write_set = []
        entry.last_error_vector = None
        entry.last_scoped_command = ""
    else:
        writes = _clean_path_list(getattr(state, "write_set", None) or [])
        if writes:
            entry.write_set = writes
        vec = _compact_error_vector(getattr(state, "last_error_vector", None))
        if vec is not None:
            entry.last_error_vector = vec
        scoped = str(getattr(state, "last_scoped_command", "") or "")
        if scoped:
            entry.last_scoped_command = scoped[:400]
        elif vec and vec.get("repair_command"):
            entry.last_scoped_command = str(vec["repair_command"])[:400]
    save_ledger(entry, home=home)
    _note_body_did(entry, home=home)
    return entry


def _note_body_did(entry: BuildLedgerEntry, *, home: str | Path | None = None) -> None:
    """Tell the organism what this body just did. Best-effort; never raise."""
    did = ""
    if entry.last_verify_ok is False:
        vec = entry.last_error_vector if isinstance(entry.last_error_vector, dict) else {}
        nodes = [str(x) for x in (vec.get("failing_nodes") or []) if x]
        did = f"verify red: {nodes[0]}" if nodes else "verify red"
    elif entry.last_verify_ok is True:
        did = "verify green"
    if not did:
        return
    with suppress(Exception):
        from remedy.core.metabolism.organism import note_last_did

        note_last_did(did, home)


def append_hop(
    project_path: str,
    hop: dict[str, Any],
    *,
    home: str | Path | None = None,
) -> BuildLedgerEntry:
    with _ledger_lock:
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


def body_next_lines(entry: BuildLedgerEntry) -> list[str]:
    """Concrete next action from persisted body. Empty if nothing to act on."""
    vec = entry.last_error_vector if isinstance(entry.last_error_vector, dict) else {}
    nodes = [str(x) for x in (vec.get("failing_nodes") or []) if x]
    path_lines = [str(x) for x in (vec.get("path_lines") or []) if x]
    repair = str(
        entry.last_scoped_command or vec.get("repair_command") or ""
    ).strip()
    writes = list(entry.write_set or [])
    lines: list[str] = []

    if entry.last_verify_ok is False:
        if nodes:
            lines.append("last_red: " + ", ".join(nodes[:4]))
        read_first = ""
        if path_lines:
            raw_pl = str(path_lines[0])
            read_first = (
                raw_pl.split("::", 1)[0] if "::" in raw_pl else re.sub(r":\d+$", "", raw_pl)
            )
        elif nodes:
            read_first = nodes[0].split("::", 1)[0]
        elif writes:
            read_first = writes[-1]
        if read_first:
            lines.append(f"READ FIRST: `{read_first}`")
        next_cmd = repair or (entry.verify_command or "").strip()
        if next_cmd:
            lines.append(f"NEXT VERIFY: `{next_cmd}`")
        lines.append(
            "Next: file_read READ FIRST → file_edit the fail → re-run NEXT VERIFY. "
            "Do not restart the build."
        )
        return lines

    if writes and entry.last_verify_ok is not True:
        lines.append("unverified: " + ", ".join(writes[-6:]))
        vcmd = repair or (entry.verify_command or "").strip()
        if vcmd:
            lines.append(f"Next: VERIFY `{vcmd}` — do not claim done.")
        else:
            lines.append("Next: run or discover verify, then finish remaining writes.")
        return lines
    return lines


def body_next_line(entry: BuildLedgerEntry) -> str:
    return " ".join(body_next_lines(entry))


def needs_resume_drive(entry: BuildLedgerEntry | None) -> bool:
    """True when a build was left mid-ship and RED — she should keep driving.

    A red build with writes on disk is unfinished work she owns; the next
    turn should resume and drive it to green, not abandon it. Green or
    write-less ledgers do not qualify (nothing to persist toward).
    """
    if entry is None:
        return False
    if entry.phase == "done" and entry.last_verify_ok is True:
        return False
    if entry.last_verify_ok is not False:
        return False
    return bool(entry.write_set)


_CONTINUE_RE = re.compile(
    r"(?is)^\s*(continue|keep going|carry on|go on|proceed|resume|"
    r"finish (it|that|up)|keep at it|carry it out|and\?|next|more|go)\s*[.!?]*\s*$"
)


def looks_like_continue(message: str) -> bool:
    """A bare continue signal (not a fresh, unrelated request)."""
    m = (message or "").strip()
    return not m or bool(_CONTINUE_RE.match(m))


def should_auto_resume_drive(
    message: str,
    project_path: str | Path | None = None,
    *,
    home: str | Path | None = None,
) -> bool:
    """Auto-resume the drive-to-green loop only on a continue + red build.

    Deliberately conservative — a new, unrelated request must NOT hijack the
    turn into an old build ("latest message wins"). Empty / "continue" /
    "keep going" over a red mid-ship ledger is the one case she resumes on
    her own.
    """
    if not looks_like_continue(message):
        return False
    return needs_resume_drive(load_ledger(project_path, home=home))


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
    body = body_next_lines(entry)
    lines = [
        "[Build ledger — resume mid-ship]",
        f"phase={entry.phase} goal={entry.goal[:160] or '—'}",
    ]
    # Red build left mid-ship = unfinished work she owns. Direct her to keep
    # driving to green (build_drive loops verify→repair), not to abandon it.
    if needs_resume_drive(entry):
        lines.append(
            "This build is RED with writes on disk — do not leave it unfinished. "
            "Continue driving it to green: call build_drive (it loops "
            "verify→repair→re-verify until tests pass), or read the failure and "
            "file_edit directly, then re-verify. Do not claim done until green."
        )
    if not body:
        lines.append(
            f"verify_command={entry.verify_command or '(none discovered)'} "
            f"write_steps={entry.write_steps} last_verify_ok={entry.last_verify_ok}"
        )
        if entry.paths_touched:
            lines.append("paths: " + ", ".join(entry.paths_touched[-8:]))
    else:
        lines.extend(body)
        return "\n".join(lines)
    # Phase fallback only when the body has no ticket
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
    elif phase in ("ship",):
        lines.append(
            "Next: SHIP only — git_status → git_push → gh_release if needed. "
            "Do not re-run tests unless source changed. "
            "Temp scripts under .remedy-build/tmp/."
        )
    else:
        lines.append("Continue from this state — do not restart the whole build.")
    return "\n".join(lines)
