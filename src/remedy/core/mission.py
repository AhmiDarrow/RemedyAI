"""Mission controller — durable goal/checklist/verify state for work-alone agency."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.home import default_home


@dataclass
class MissionStep:
    id: str
    title: str
    status: str = "pending"  # pending | active | done | failed | skipped
    note: str = ""


@dataclass
class Mission:
    id: str
    goal: str
    session_id: str | None = None
    status: str = "active"  # active | completed | blocked | cancelled
    steps: list[MissionStep] = field(default_factory=list)
    verify_command: str | None = None
    verify_status: str | None = None  # None | passed | failed | skipped
    last_verify_output: str = ""
    retries: int = 0
    max_retries: int = 5
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Mission:
        steps = [MissionStep(**s) for s in (data.get("steps") or [])]
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            goal=str(data.get("goal") or ""),
            session_id=data.get("session_id"),
            status=str(data.get("status") or "active"),
            steps=steps,
            verify_command=data.get("verify_command"),
            verify_status=data.get("verify_status"),
            last_verify_output=str(data.get("last_verify_output") or ""),
            retries=int(data.get("retries") or 0),
            max_retries=int(data.get("max_retries") or 5),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )


class MissionStore:
    """JSON missions under ~/.remedy/missions/."""

    def __init__(self, home: str | Path | None = None) -> None:
        if home is None:
            home = default_home()
        self.root = Path(home).expanduser() / "missions"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, mission_id: str) -> Path:
        """Resolve mission JSON path — never leave ``missions/`` (path jail)."""
        from remedy.core.security import validate_mission_id

        mid = validate_mission_id(mission_id)
        candidate = (self.root / f"{mid}.json").resolve()
        root = self.root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as err:
            from remedy.core.errors import SecurityError

            raise SecurityError(
                "Mission path escaped missions root",
                rule="mission_path_jail",
                detail={"mission_id": mid},
            ) from err
        return candidate

    def save(self, mission: Mission) -> Mission:
        from remedy.core.security import sanitize_mission_session_id, validate_mission_id

        now = datetime.now(UTC).isoformat()
        if not mission.created_at:
            mission.created_at = now
        mission.updated_at = now
        # Validate id before write (UUIDs always pass; blocks forged objects)
        mission.id = validate_mission_id(mission.id)
        write_json_atomic(self._path(mission.id), mission.to_dict())
        # Latest pointer per session (sanitize so session_id cannot path-escape)
        sid = sanitize_mission_session_id(mission.session_id)
        if sid:
            mission.session_id = sid
            ptr = self.root / f"latest-{sid}.txt"
            ptr.write_text(mission.id, encoding="utf-8")
        (self.root / "latest.txt").write_text(mission.id, encoding="utf-8")
        return mission

    def get(self, mission_id: str) -> Mission | None:
        from remedy.core.errors import SecurityError
        from remedy.core.security import validate_mission_id

        mid = (mission_id or "").strip()
        if not mid:
            return None
        try:
            mid = validate_mission_id(mid)
        except SecurityError:
            return None
        try:
            p = self._path(mid)
        except SecurityError:
            return None
        if p.is_file():
            try:
                return Mission.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                return None
        # Prefix match (summaries show first 8 chars of UUID)
        if len(mid) < 4:
            return None
        needle = mid.replace("-", "").lower()
        matches: list[Mission] = []
        for fp in self.root.glob("*.json"):
            # Only basenames under root (glob already scoped)
            stem = fp.stem
            if not (
                stem.startswith(mid)
                or stem.replace("-", "").lower().startswith(needle)
            ):
                continue
            try:
                m = Mission.from_dict(json.loads(fp.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            compact = m.id.replace("-", "").lower()
            if m.id.startswith(mid) or compact.startswith(needle):
                matches.append(m)
        if len(matches) == 1:
            return matches[0]
        return None

    def latest(self, session_id: str | None = None) -> Mission | None:
        from remedy.core.security import sanitize_mission_session_id

        sid = sanitize_mission_session_id(session_id)
        if sid:
            ptr = self.root / f"latest-{sid}.txt"
            if ptr.is_file():
                mid = ptr.read_text(encoding="utf-8").strip()
                m = self.get(mid)
                if m:
                    return m
        ptr = self.root / "latest.txt"
        if ptr.is_file():
            return self.get(ptr.read_text(encoding="utf-8").strip())
        return None


def create_mission(
    goal: str,
    *,
    steps: list[str] | None = None,
    session_id: str | None = None,
    verify_command: str | None = None,
    home: str | Path | None = None,
) -> Mission:
    store = MissionStore(home)
    m = Mission(
        id=str(uuid.uuid4()),
        goal=goal.strip(),
        session_id=session_id,
        verify_command=(verify_command or "").strip() or None,
        steps=[
            MissionStep(id=str(uuid.uuid4())[:8], title=t.strip())
            for t in (steps or [])
            if (t or "").strip()
        ],
    )
    if m.steps:
        m.steps[0].status = "active"
    return store.save(m)


def mission_summary(m: Mission) -> str:
    done = sum(1 for s in m.steps if s.status == "done")
    total = len(m.steps)
    lines = [
        f"Mission {m.id[:8]} [{m.status}]",
        f"Goal: {m.goal}",
        f"Steps: {done}/{total} done · retries {m.retries}/{m.max_retries}",
    ]
    if m.verify_command:
        lines.append(f"Verify: {m.verify_command} ({m.verify_status or 'not run'})")
    for s in m.steps[:20]:
        mark = {
            "done": "✓",
            "active": "→",
            "failed": "✗",
            "skipped": "·",
            "pending": " ",
        }.get(s.status, "?")
        lines.append(f"  [{mark}] {s.title}" + (f" — {s.note}" if s.note else ""))
    return "\n".join(lines)


def advance_step(
    m: Mission,
    *,
    step_id: str | None = None,
    status: str = "done",
    note: str = "",
) -> Mission:
    target = None
    if step_id:
        for s in m.steps:
            if s.id == step_id or s.title == step_id:
                target = s
                break
    if target is None:
        for s in m.steps:
            if s.status == "active":
                target = s
                break
    if target is None:
        for s in m.steps:
            if s.status == "pending":
                target = s
                break
    if target is None:
        return m
    target.status = status
    if note:
        target.note = note[:500]
    # Activate next pending
    if status == "done":
        for s in m.steps:
            if s.status == "pending":
                s.status = "active"
                break
        else:
            if all(s.status in ("done", "skipped") for s in m.steps):
                # A mission that named a verify command is not finished because
                # the last box got ticked — it is finished when that check
                # passes. Completing here moved the mission out of "active",
                # which is the state mission_update and mission_status test
                # before warning, so the gate could never fire: the mission
                # read [completed] with "Verify: pytest -q (not run)".
                # mission_verify still completes it the moment verify passes.
                if not m.verify_command or m.verify_status == "passed":
                    m.status = "completed"
    elif status == "failed":
        m.retries += 1
        if m.retries >= m.max_retries:
            m.status = "blocked"
    return m
