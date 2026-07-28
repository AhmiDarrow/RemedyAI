"""Job queue bridging Python tools ↔ desktop host (Tauri WebView2).

Browser-target actions that need the embed are enqueued here. The desktop
app claims jobs via HTTP and posts results. Desktop-target actions do not
use this queue (they run in-process via Win32).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _root(home_dir: Path | str | None = None) -> Path:
    home = Path(home_dir).expanduser() if home_dir else Path.home() / ".remedy"
    p = home / "computer" / "jobs"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class ComputerJob:
    id: str
    action: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending | running | done | error | cancelled
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ComputerJob:
        return cls(
            id=str(raw.get("id") or ""),
            action=str(raw.get("action") or ""),
            payload=dict(raw.get("payload") or {}),
            status=str(raw.get("status") or "pending"),
            result=raw.get("result") if isinstance(raw.get("result"), dict) else None,
            error=raw.get("error"),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
        )


class ComputerHostBridge:
    """Filesystem + in-memory queue for browser host jobs."""

    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home_dir = home_dir
        self.root = _root(home_dir)
        self._lock = threading.Lock()
        self._host_seen_at: float = 0.0
        self._browser_bounds: dict[str, float] | None = None
        self._browser_scale: float = 1.0

    def mark_host_alive(self) -> None:
        self._host_seen_at = time.time()

    def host_connected(self, *, max_age_s: float = 15.0) -> bool:
        if self._host_seen_at <= 0:
            return False
        return (time.time() - self._host_seen_at) <= max_age_s

    def set_browser_bounds(
        self,
        bounds: dict[str, Any] | None,
        *,
        scale: float | None = None,
    ) -> None:
        if not bounds:
            return
        try:
            self._browser_bounds = {
                "x": float(bounds.get("x", 0)),
                "y": float(bounds.get("y", 0)),
                "width": float(bounds.get("width", 0)),
                "height": float(bounds.get("height", 0)),
            }
            if scale is not None and float(scale) > 0:
                self._browser_scale = float(scale)
        except (TypeError, ValueError):
            pass

    def get_browser_bounds(self) -> dict[str, Any] | None:
        if not self._browser_bounds:
            return None
        return {**self._browser_bounds, "scale": self._browser_scale}

    def _path(self, job_id: str) -> Path:
        safe = "".join(c for c in job_id if c.isalnum() or c in "-_") or "job"
        return self.root / f"{safe}.json"

    def _write(self, job: ComputerJob) -> None:
        job.updated_at = _now()
        path = self._path(job.id)
        path.write_text(json.dumps(job.to_dict(), indent=2), encoding="utf-8")

    def _read(self, job_id: str) -> ComputerJob | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return ComputerJob.from_dict(raw)

    def enqueue(self, action: str, payload: dict[str, Any] | None = None) -> ComputerJob:
        job = ComputerJob(
            id=uuid.uuid4().hex[:16],
            action=action,
            payload=dict(payload or {}),
            status="pending",
        )
        with self._lock:
            self._write(job)
        return job

    def claim_next(self) -> ComputerJob | None:
        """Desktop host: claim oldest pending job."""
        with self._lock:
            self.mark_host_alive()
            files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime)
            for path in files:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                job = ComputerJob.from_dict(raw)
                if job.status != "pending":
                    continue
                job.status = "running"
                self._write(job)
                return job
        return None

    def complete(
        self,
        job_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ComputerJob | None:
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            job.status = "done" if ok else "error"
            job.result = result
            job.error = error
            self._write(job)
            return job

    def cancel(self, job_id: str) -> ComputerJob | None:
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            job.status = "cancelled"
            job.error = "cancelled"
            self._write(job)
            return job

    def cancel_pending_and_running(self, *, reason: str = "aborted") -> int:
        """Cancel all open jobs (Stop generation). Returns count cancelled."""
        n = 0
        with self._lock:
            for path in list(self.root.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                job = ComputerJob.from_dict(raw)
                if job.status in ("pending", "running"):
                    job.status = "cancelled"
                    job.error = reason
                    self._write(job)
                    n += 1
        return n

    def complete_a11y_push(
        self,
        job_id: str,
        elements: list[dict[str, Any]],
    ) -> ComputerJob | None:
        """Complete a snapshot job from page-injected callback (job id is the secret)."""
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            if job.action not in ("snapshot", "a11y"):
                return None
            if job.status not in ("pending", "running"):
                return None
            job.status = "done"
            job.result = {
                "ok": True,
                "target": "browser",
                "action": "snapshot",
                "message": f"{len(elements)} interactive elements",
                "elements": elements[:120],
            }
            self._write(job)
            return job

    def wait(
        self,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        poll_s: float = 0.15,
        abort_check: Any | None = None,
    ) -> ComputerJob:
        deadline = time.time() + max(1.0, timeout_s)
        while time.time() < deadline:
            if abort_check is not None:
                try:
                    if abort_check():
                        self.cancel(job_id)
                        job = self._read(job_id)
                        if job:
                            return job
                        break
                except Exception:
                    pass
            job = self._read(job_id)
            if job is None:
                break
            if job.status in ("done", "error", "cancelled"):
                return job
            time.sleep(poll_s)
        job = self._read(job_id)
        if job is None:
            return ComputerJob(
                id=job_id,
                action="?",
                status="error",
                error="job missing",
            )
        if job.status not in ("done", "error", "cancelled"):
            job.status = "error"
            job.error = f"timeout waiting for desktop host ({timeout_s:.0f}s)"
            with self._lock:
                self._write(job)
        return job

    def purge_old(self, *, max_age_s: float = 3600.0) -> int:
        cutoff = time.time() - max_age_s
        n = 0
        for path in list(self.root.glob("*.json")):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
                    n += 1
            except OSError:
                continue
        return n


_bridge: ComputerHostBridge | None = None
_bridge_lock = threading.Lock()


def get_host_bridge(home_dir: Path | str | None = None) -> ComputerHostBridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = ComputerHostBridge(home_dir=home_dir)
        return _bridge
