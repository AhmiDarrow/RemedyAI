"""Deterministic Action IR — replayable agency/CUA traces (secrets redacted)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SECRETISH = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|bearer\s+[a-z0-9._-]{12,}|"
    r"sk-[a-z0-9]{10,}|xai-[a-z0-9]{10,})"
)


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return _SECRETISH.sub("[redacted]", obj)[:4000]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in (
                "password",
                "token",
                "api_key",
                "apikey",
                "authorization",
                "secret",
                "cookie",
            ):
                out[k] = "[redacted]"
            else:
                out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj[:50]]
    return obj


def _hash_obj(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class IrStep:
    tool: str
    args_hash: str
    args_redacted: dict[str, Any]
    result_hash: str
    eu_delta: int = 0
    map_snapshot_id: str = ""
    shadow_outcome: str = ""
    ts: float = field(default_factory=time.time)
    ok: bool = True

    def to_public(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args_hash": self.args_hash,
            "args": self.args_redacted,
            "result_hash": self.result_hash,
            "eu_delta": self.eu_delta,
            "map_snapshot_id": self.map_snapshot_id,
            "shadow_outcome": self.shadow_outcome,
            "ts": self.ts,
            "ok": self.ok,
        }


@dataclass
class ActionIR:
    turn_id: str
    session_id: str = ""
    tier: int = 2
    steps: list[IrStep] = field(default_factory=list)
    decision_units: list[str] = field(default_factory=list)
    brief_head: str = ""
    terminal_status: str = "open"  # open | done | failed | aborted
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add_step(
        self,
        *,
        tool: str,
        arguments: dict[str, Any] | None = None,
        result: str = "",
        eu_delta: int = 0,
        map_snapshot_id: str = "",
        shadow_outcome: str = "",
        ok: bool = True,
    ) -> IrStep:
        args_r = _redact_obj(dict(arguments or {}))
        if not isinstance(args_r, dict):
            args_r = {}
        res_r = _SECRETISH.sub("[redacted]", result or "")
        step = IrStep(
            tool=tool,
            args_hash=_hash_obj(args_r),
            args_redacted=args_r,
            result_hash=_hash_obj(res_r[:8000]),
            eu_delta=eu_delta,
            map_snapshot_id=map_snapshot_id,
            shadow_outcome=shadow_outcome,
            ok=ok,
        )
        with self._lock:
            self.steps.append(step)
            if len(self.steps) > 200:
                self.steps = self.steps[-200:]
        return step

    def finish(self, status: str = "done") -> None:
        with self._lock:
            self.terminal_status = status
            self.ended_at = time.time()

    def to_public(self) -> dict[str, Any]:
        with self._lock:
            return {
                "turn_id": self.turn_id,
                "session_id": self.session_id,
                "tier": self.tier,
                "step_count": len(self.steps),
                "steps": [s.to_public() for s in self.steps],
                "decision_units": list(self.decision_units)[:32],
                "brief_head": self.brief_head[:400],
                "terminal_status": self.terminal_status,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
            }

    def persist(self, home: Path | str | None = None) -> Path | None:
        """Write redacted IR to disk for soak/replay. Fail soft."""
        try:
            root = Path(home).expanduser() if home else Path.home() / ".remedy"
            d = root / "action_ir"
            d.mkdir(parents=True, exist_ok=True)
            sid = "".join(
                c for c in (self.session_id or "default") if c.isalnum() or c in "-_"
            )[:40]
            path = d / f"{sid}_{self.turn_id}.json"
            path.write_text(
                json.dumps(self.to_public(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return path
        except Exception:
            return None


_active: dict[str, ActionIR] = {}
_lock = threading.Lock()
_ir_recorded = 0
_ir_lock = threading.Lock()


def start_action_ir(
    *,
    session_id: str = "",
    tier: int = 2,
    brief_head: str = "",
) -> ActionIR:
    turn_id = uuid.uuid4().hex[:12]
    ir = ActionIR(
        turn_id=turn_id,
        session_id=session_id or "_default",
        tier=tier,
        brief_head=(brief_head or "")[:400],
    )
    key = f"{session_id or '_default'}:{turn_id}"
    with _lock:
        _active[key] = ir
        # prune old
        if len(_active) > 64:
            for k in list(_active.keys())[:16]:
                _active.pop(k, None)
    with _ir_lock:
        global _ir_recorded
        _ir_recorded += 1
    return ir


def get_action_ir(session_id: str, turn_id: str) -> ActionIR | None:
    return _active.get(f"{session_id or '_default'}:{turn_id}")


def ir_coverage_count() -> int:
    with _ir_lock:
        return _ir_recorded
