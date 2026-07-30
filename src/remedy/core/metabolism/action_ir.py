"""Deterministic Action IR — replayable agency/CUA traces (secrets redacted)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bound IR step list growth on long agency turns (memory + to_public cost).
MAX_IR_STEPS = 96


def _redact_obj(obj: Any) -> Any:
    from remedy.core.metabolism.redact import redact_obj

    return redact_obj(obj)


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
        from remedy.core.metabolism.redact import redact_text

        raw_args = dict(arguments or {})
        # Never persist full file bodies / shell commands in IR — path + hashes only
        body_tools = {
            "file_write",
            "file_edit",
            "file_edit_batch",
            "bash_exec",
            "computer_type",
        }
        if (tool or "") in body_tools:
            slim: dict[str, Any] = {}
            for k in ("path", "workdir", "timeout_seconds", "ref", "url", "monitor"):
                if k in raw_args:
                    slim[k] = raw_args[k]
            if "command" in raw_args:
                cmd = str(raw_args.get("command") or "")
                slim["command_sha16"] = _hash_obj(cmd[:2000])
                slim["command_chars"] = len(cmd)
            if "content" in raw_args:
                c = str(raw_args.get("content") or "")
                slim["content_sha16"] = _hash_obj(c[:8000])
                slim["content_chars"] = len(c)
            if "edits" in raw_args and isinstance(raw_args["edits"], list):
                slim["edits_count"] = len(raw_args["edits"])
            if "text" in raw_args:
                slim["text"] = "[omitted]"
            args_r = _redact_obj(slim)
        else:
            args_r = _redact_obj(raw_args)
        # Strip URL credentials/query from any remaining url field (CUA/browse)
        if isinstance(args_r, dict) and "url" in args_r and args_r["url"]:
            try:
                from remedy.core.metabolism.cua_macros import _sanitize_url

                args_r["url"] = _sanitize_url(str(args_r["url"]))
            except Exception:
                u = str(args_r["url"])
                if "?" in u:
                    u = u.split("?", 1)[0]
                args_r["url"] = u[:200]
        if not isinstance(args_r, dict):
            args_r = {}
        res_r = redact_text(result or "")[:2000]
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
            if len(self.steps) > MAX_IR_STEPS:
                self.steps = self.steps[-MAX_IR_STEPS:]
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
