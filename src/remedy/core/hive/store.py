"""Filesystem hive: ~/.remedy/hive/{episodes,posts}/ — not the owner sidebar."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import suppress
from pathlib import Path

from remedy.core.atomic_json import write_json_atomic
from remedy.core.hive.types import (
    CADENCE_FORAGER,
    CADENCE_POST,
    STATUS_PENDING,
    STATUS_RETIRED,
    STATUS_RUNNING,
    HiveDaughter,
    _now,
    hive_session_id,
)
from remedy.home import default_home

logger = logging.getLogger(__name__)

_lock = threading.RLock()


def hive_root(home: Path | str | None = None) -> Path:
    base = Path(home).expanduser() if home else default_home()
    root = base / "hive"
    for sub in ("episodes", "posts"):
        d = root / sub
        d.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            d.chmod(0o700)
    return root


def _subdir(cadence: str) -> str:
    return "posts" if cadence == CADENCE_POST else "episodes"


class HiveStore:
    def __init__(self, home: Path | str | None = None) -> None:
        self.home = Path(home).expanduser() if home else default_home()
        self.root = hive_root(self.home)

    def _path(self, daughter: HiveDaughter) -> Path:
        return self.root / _subdir(daughter.cadence) / f"{daughter.id}.json"

    def _path_id(self, daughter_id: str, cadence: str | None = None) -> Path | None:
        did = str(daughter_id or "").strip()
        if not did:
            return None
        if cadence:
            p = self.root / _subdir(cadence) / f"{did}.json"
            return p if p.is_file() else None
        for sub in ("episodes", "posts"):
            p = self.root / sub / f"{did}.json"
            if p.is_file():
                return p
        return None

    def hire(
        self,
        goal: str,
        *,
        cadence: str = CADENCE_FORAGER,
        parent_session_id: str = "",
        project_path: str = "",
        approval_mode: str = "",
        budget_steps: int = 8,
        pulse_s: int = 0,
    ) -> HiveDaughter:
        did = uuid.uuid4().hex
        d = HiveDaughter(
            id=did,
            cadence=cadence if cadence in (CADENCE_FORAGER, CADENCE_POST) else CADENCE_FORAGER,
            status=STATUS_PENDING,
            goal=str(goal or "")[:800],
            session_id=hive_session_id(did),
            parent_session_id=str(parent_session_id or ""),
            budget_steps=budget_steps,
            project_path=str(project_path or ""),
            approval_mode=str(approval_mode or ""),
            pulse_s=int(pulse_s or 0),
        )
        with _lock:
            self._write(d)
        return d

    def _write(self, daughter: HiveDaughter) -> None:
        daughter.updated_at = _now()
        path = self._path(daughter)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, daughter.to_dict(), mode=0o600)

    def save(self, daughter: HiveDaughter) -> None:
        with _lock:
            self._write(daughter)

    def get(self, daughter_id: str) -> HiveDaughter | None:
        path = self._path_id(daughter_id)
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return HiveDaughter.from_dict(raw)

    def list_all(self) -> list[HiveDaughter]:
        out: list[HiveDaughter] = []
        for sub in ("episodes", "posts"):
            folder = self.root / sub
            if not folder.is_dir():
                continue
            for path in folder.glob("*.json"):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(raw, dict):
                    out.append(HiveDaughter.from_dict(raw))
        out.sort(key=lambda d: d.updated_at, reverse=True)
        return out

    def live(self) -> list[HiveDaughter]:
        dead = {STATUS_RETIRED, "cancelled"}
        return [d for d in self.list_all() if d.status not in dead]

    def live_pulses(self) -> list[HiveDaughter]:
        return [d for d in self.live() if d.status in (STATUS_PENDING, STATUS_RUNNING)]

    def live_posts(self) -> list[HiveDaughter]:
        dead = {STATUS_RETIRED, "cancelled"}
        return [
            d
            for d in self.list_all()
            if d.cadence == CADENCE_POST and d.status not in dead
        ]

    def children_of(self, parent_session_id: str) -> list[HiveDaughter]:
        want = str(parent_session_id or "").strip()
        if not want:
            return []
        return [d for d in self.live() if d.parent_session_id == want]


_stores: dict[str, HiveStore] = {}


def get_hive_store(home: Path | str | None = None) -> HiveStore:
    key = str(Path(home).expanduser() if home else default_home())
    st = _stores.get(key)
    if st is None:
        st = HiveStore(home)
        _stores[key] = st
    return st
