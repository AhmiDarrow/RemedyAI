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


# Max characters of page text / large strings kept in on-disk job JSON.
_JOB_TEXT_MAX = 4_000

# Free-text bodies that may hold page content or host messages.
_RESULT_TEXT_KEYS = (
    "text",
    "page_text",
    "content",
    "html",
    "message",
    "stdout",
    "stderr",
    "body",
    "error",
)

# Typed / secret-bearing payload keys that must not linger after the host finishes.
_PAYLOAD_SECRET_KEYS = frozenset(
    {
        "text",
        "type",
        "type_text",
        "content",
        "password",
        "value",
        "secret",
        "token",
        "api_key",
        "authorization",
    }
)


def _scrub_elements(els: Any) -> list[Any]:
    """Drop password / redacted input values from snapshot element lists."""
    if not isinstance(els, list):
        return []
    cleaned: list[Any] = []
    for el in els[:120]:
        if not isinstance(el, dict):
            cleaned.append(el)
            continue
        e = dict(el)
        tag = str(e.get("tag") or "").lower()
        itype = str(
            e.get("type") or e.get("input_type") or e.get("itype") or ""
        ).lower()
        is_password = itype == "password" or "password" in str(e.get("name") or "").lower()
        v = e.get("value")
        if is_password or e.get("value_redacted"):
            if isinstance(v, str) and v:
                e["value"] = "[filled]"
                e["value_redacted"] = True
        elif tag == "input" and isinstance(v, str) and len(v) > 40:
            e["value"] = v[:40] + "…"
        cleaned.append(e)
    return cleaned


def _scrub_job_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Cap large text fields and redact secrets before writing to disk."""
    if result is None:
        return None
    out = dict(result)
    for key in _RESULT_TEXT_KEYS:
        val = out.get(key)
        if isinstance(val, str) and len(val) > _JOB_TEXT_MAX:
            out[key] = val[: _JOB_TEXT_MAX - 1] + "…"
            out[f"{key}_truncated"] = True
    # Snapshot elements: never retain password field values at rest.
    if "elements" in out:
        out["elements"] = _scrub_elements(out.get("elements"))
    # Secret-shaped substrings (API keys, bearer tokens) never land on disk.
    try:
        from remedy.core.metabolism.redact import redact_obj

        red = redact_obj(out)
        if isinstance(red, dict):
            out = red
    except Exception:
        # Fail closed for free-text bodies; keep structural ok/action only
        for key in _RESULT_TEXT_KEYS:
            if key in out and isinstance(out.get(key), str):
                out[key] = "[redacted]"
    return out


def _scrub_job_error(error: str | None) -> str | None:
    """Redact secret-shaped host error strings before persistence."""
    if error is None:
        return None
    text = str(error)
    if not text:
        return ""
    try:
        from remedy.core.metabolism.redact import redact_text

        return redact_text(text)[:2_000]
    except Exception:
        return "[redacted]"


def _scrub_retained_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """After a job finishes, strip typed secrets from the on-disk payload.

    Host needs plaintext while pending/running; once terminal, only lengths remain.
    """
    if not payload or not isinstance(payload, dict):
        return {}
    out = dict(payload)
    for key in list(out.keys()):
        kl = str(key).lower().replace("-", "_")
        val = out.get(key)
        if kl in _PAYLOAD_SECRET_KEYS or kl.endswith("_password") or kl.endswith("_secret"):
            if isinstance(val, str) and val:
                out[key] = f"[redacted chars={len(val)}]"
            elif val is not None and not isinstance(val, (int, float, bool)):
                out[key] = "[redacted]"
    # Nested ui dict may hold free text — keep structure, scrub strings that look secret
    try:
        from remedy.core.metabolism.redact import redact_obj

        red = redact_obj(out)
        if isinstance(red, dict):
            return red
    except Exception:
        pass
    return out


def canonical_home(home_dir: Path | str | None = None) -> Path:
    """Single resolved home so tool wait + API complete always share one jobs dir."""
    if home_dir is not None and str(home_dir).strip():
        return Path(home_dir).expanduser().resolve()
    return (Path.home() / ".remedy").resolve()


def _root(home_dir: Path | str | None = None) -> Path:
    home = canonical_home(home_dir)
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
        # Last jobs/next or ui/command poll (real Desktop poller — not a one-shot hello).
        self._last_poll_at: float = 0.0
        self._last_claim_at: float = 0.0
        self._browser_bounds: dict[str, float] | None = None
        self._browser_scale: float = 1.0
        # Last a11y/desktop snapshot for click-by-ref resolution
        self._last_elements: list[dict[str, Any]] = []
        self._last_elements_target: str = ""
        self._last_navigate_at: float = 0.0
        self._last_navigate_url: str = ""
        # True when last navigate returned before host confirmed page load.
        self._last_navigate_optimistic: bool = False
        # Desktop UI command (open Browser rail like Settings) — memory + disk
        self._ui_command: dict[str, Any] | None = None
        home = Path(home_dir).expanduser() if home_dir else Path.home() / ".remedy"
        self._ui_path = home / "computer" / "ui_command.json"
        self._ui_path.parent.mkdir(parents=True, exist_ok=True)

    def mark_host_alive(self, *, poller: bool = False) -> None:
        """Note that something on loopback touched the host API.

        *poller*=True only for jobs/next or ui/command polls (real Desktop).
        A one-shot ``/host/hello`` alone must not claim the rail is driveable.
        """
        now = time.time()
        self._host_seen_at = now
        if poller:
            self._last_poll_at = now

    def mark_host_dead(self) -> None:
        """Forget host liveness after unclaimed jobs / failed drive."""
        self._host_seen_at = 0.0
        self._last_poll_at = 0.0

    def pending_count(self) -> int:
        n = 0
        for path in self.root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict) and raw.get("status") == "pending":
                n += 1
        return n

    def set_last_elements(
        self,
        elements: list[dict[str, Any]],
        *,
        target: str = "browser",
    ) -> None:
        self._last_elements = list(elements or [])[:120]
        self._last_elements_target = target

    def mark_navigated(self, url: str = "", *, optimistic: bool = False) -> None:
        self._last_navigate_at = time.time()
        if url:
            self._last_navigate_url = str(url)
        self._last_navigate_optimistic = bool(optimistic)

    def clear_navigate_optimistic(self) -> None:
        self._last_navigate_optimistic = False

    def navigate_needs_settle(self, *, max_age_s: float = 8.0) -> bool:
        """True if last open was optimistic and still recent (type/click should wait)."""
        if not self._last_navigate_optimistic:
            return False
        if self._last_navigate_at <= 0:
            return False
        return (time.time() - self._last_navigate_at) < float(max_age_s)

    def settle_after_navigate(self, *, min_s: float = 0.35, max_s: float = 1.2) -> float:
        """Sleep remaining settle time if a navigate just happened. Returns slept seconds."""
        if self._last_navigate_at <= 0:
            return 0.0
        elapsed = time.time() - self._last_navigate_at
        need = max(0.0, float(min_s) - elapsed)
        need = min(need, float(max_s))
        if need > 0.02:
            time.sleep(need)
            return need
        return 0.0

    def get_element_by_ref(self, ref: str) -> dict[str, Any] | None:
        r = (ref or "").strip().lower()
        if not r:
            return None
        for el in self._last_elements:
            if str(el.get("ref") or "").strip().lower() == r:
                return el
        return None

    def last_elements_info(self) -> dict[str, Any]:
        return {
            "target": self._last_elements_target,
            "count": len(self._last_elements),
            "elements": list(self._last_elements),
        }

    def host_connected(self, *, max_age_s: float = 15.0) -> bool:
        """True when Desktop is actively polling (jobs/ui), not just hello.

        Optimistic navigate and rail tools require a real poller; stress scripts
        that only POST /host/hello must not look \"connected\".
        """
        now = time.time()
        if self._last_poll_at > 0 and (now - self._last_poll_at) <= float(max_age_s):
            return True
        # A claim is also proof the host is working the queue.
        if self._last_claim_at > 0 and (now - self._last_claim_at) <= float(max_age_s):
            return True
        return False

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
        data = json.dumps(job.to_dict(), indent=2)
        # Atomic-ish write so wait() never reads a half-written file
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        tmp.replace(path)

    def _read(self, job_id: str) -> ComputerJob | None:
        path = self._path(job_id)
        if not path.is_file():
            return None
        try:
            # Fresh open every time (no OS cache tricks)
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return ComputerJob.from_dict(raw)

    def set_ui_command(self, command: dict[str, Any]) -> None:
        """Ask Desktop to open the Browser rail / run UI (persisted for pollers)."""
        cmd = dict(command or {})
        cmd.setdefault("ts", _now())
        with self._lock:
            self._ui_command = cmd
            try:
                self._ui_path.write_text(
                    json.dumps(cmd, indent=2), encoding="utf-8"
                )
            except OSError:
                pass

    def peek_ui_command(self) -> dict[str, Any] | None:
        with self._lock:
            if self._ui_command:
                return dict(self._ui_command)
            try:
                if self._ui_path.is_file():
                    raw = json.loads(self._ui_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and raw.get("action"):
                        self._ui_command = raw
                        return dict(raw)
            except (OSError, json.JSONDecodeError):
                pass
        return None

    def clear_ui_command(self, *, job_id: str | None = None) -> None:
        with self._lock:
            if job_id and self._ui_command:
                if str(self._ui_command.get("job_id") or "") != str(job_id):
                    return
            self._ui_command = None
            try:
                if self._ui_path.is_file():
                    self._ui_path.unlink(missing_ok=True)
            except OSError:
                pass

    def take_ui_command(self) -> dict[str, Any] | None:
        """Atomically read + clear UI command (prevents re-navigate loops)."""
        with self._lock:
            cmd = None
            if self._ui_command:
                cmd = dict(self._ui_command)
            else:
                try:
                    if self._ui_path.is_file():
                        raw = json.loads(self._ui_path.read_text(encoding="utf-8"))
                        if isinstance(raw, dict) and raw.get("action"):
                            cmd = raw
                except (OSError, json.JSONDecodeError):
                    cmd = None
            self._ui_command = None
            try:
                if self._ui_path.is_file():
                    self._ui_path.unlink(missing_ok=True)
            except OSError:
                pass
            return cmd

    def enqueue(self, action: str, payload: dict[str, Any] | None = None) -> ComputerJob:
        job = ComputerJob(
            # Full uuid hex (128-bit) — do not truncate (a11y/job spoof surface).
            id=uuid.uuid4().hex,
            action=action,
            payload=dict(payload or {}),
            status="pending",
        )
        with self._lock:
            self._write(job)
        # Always request rail open for browser actions (Desktop pops panel like Settings)
        pl = dict(payload or {})
        ui = pl.get("ui") if isinstance(pl.get("ui"), dict) else {}
        if action in ("navigate", "snapshot", "click", "type", "screenshot") or ui.get(
            "open_browser"
        ):
            self.set_ui_command(
                {
                    "action": "open_browser",
                    "url": pl.get("url") or "",
                    "job_id": job.id,
                    "job_action": action,
                }
            )
        return job

    def claim_next(
        self,
        *,
        exclude_actions: set[str] | frozenset[str] | None = None,
        only_actions: set[str] | frozenset[str] | None = None,
    ) -> ComputerJob | None:
        """Desktop host: claim oldest pending job.

        *exclude_actions*: skip these actions (leave pending). SPA uses
        exclude=navigate so Rust owns rail navigates via ui_command and
        the two pollers cannot deadlock the WebView main thread.

        *only_actions*: if set, only claim jobs whose action is in this set
        (Rust backup path: only=navigate).
        """
        skip = {str(a).lower() for a in (exclude_actions or ())}
        only = {str(a).lower() for a in (only_actions or ())} if only_actions else None
        with self._lock:
            self.mark_host_alive(poller=True)
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
                act = job.action.lower()
                if act in skip:
                    continue
                if only is not None and act not in only:
                    continue
                job.status = "running"
                self._write(job)
                self._last_claim_at = time.time()
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
            # Success always wins over a prior wait-timeout error so a late
            # host complete is recorded (agent may re-read after grace).
            safe_result = _scrub_job_result(result) if result is not None else None
            safe_error = _scrub_job_error(error) if error is not None else None
            if ok:
                job.status = "done"
                job.result = safe_result
                job.error = None
            elif job.status == "done":
                # Never downgrade SUCCESS → error
                return job
            else:
                job.status = "error"
                job.result = safe_result
                job.error = safe_error
            # Typed passwords / tokens must not linger in payload after terminal.
            if job.status in ("done", "error", "cancelled"):
                job.payload = _scrub_retained_payload(job.payload)
            self._write(job)
            # Opportunistic cleanup so page_text / snapshots do not linger on disk.
            if job.status in ("done", "error", "cancelled"):
                try:
                    self.purge_old(max_age_s=900.0)
                except Exception:
                    pass
            return job

    def find_recent_success(
        self,
        *,
        action: str,
        url: str,
        max_age_s: float = 20.0,
    ) -> ComputerJob | None:
        """Find a recent successful job for the same action+URL (reconcile races)."""
        want = (url or "").strip().rstrip("/")
        if not want:
            return None
        cutoff = time.time() - max_age_s
        best: ComputerJob | None = None
        for path in self.root.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            job = ComputerJob.from_dict(raw)
            if job.action != action or job.status != "done":
                continue
            res = job.result or {}
            if not res.get("ok", True):
                continue
            got = str(
                res.get("url") or (job.payload or {}).get("url") or ""
            ).strip().rstrip("/")
            if got == want or got.startswith(want) or want.startswith(got):
                if best is None or (job.updated_at or "") > (best.updated_at or ""):
                    best = job
        return best

    def cancel(self, job_id: str) -> ComputerJob | None:
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            job.status = "cancelled"
            job.error = "cancelled"
            job.payload = _scrub_retained_payload(job.payload)
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
                    job.error = _scrub_job_error(reason) or "aborted"
                    job.payload = _scrub_retained_payload(job.payload)
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
            trimmed = elements[:120]
            # Same scrub path as host complete — password values never hit disk.
            safe_result = _scrub_job_result(
                {
                    "ok": True,
                    "target": "browser",
                    "action": "snapshot",
                    "message": f"{len(trimmed)} interactive elements",
                    "elements": trimmed,
                }
            )
            job.status = "done"
            job.result = safe_result
            job.payload = _scrub_retained_payload(job.payload)
            # In-memory ref map still needs coordinates; drop password values only.
            self.set_last_elements(_scrub_elements(trimmed), target="browser")
            self._write(job)
            return job

    def renudge_ui_for_job(self, job: ComputerJob) -> None:
        """Re-publish open_browser ui_command if host may have lost the first take."""
        if job.action != "navigate":
            return
        if job.status not in ("pending", "running"):
            return
        pl = dict(job.payload or {})
        url = str(pl.get("url") or "")
        if not url:
            return
        self.set_ui_command(
            {
                "action": "open_browser",
                "url": url,
                "job_id": job.id,
                "job_action": job.action,
                "renudge": True,
            }
        )

    def wait(
        self,
        job_id: str,
        *,
        timeout_s: float = 30.0,
        poll_s: float = 0.05,
        abort_check: Any | None = None,
        unclaimed_timeout_s: float | None = 3.0,
        grace_s: float | None = None,
    ) -> ComputerJob:
        """Wait for job completion.

        *unclaimed_timeout_s*: if still ``pending`` this long with no UI command
        for this job, fail fast. Navigate jobs leave unclaimed_timeout None and
        wait for Desktop complete (or overall timeout).

        *grace_s*: extra time after the deadline to pick up a late host complete.
        Defaults to a small fraction of *timeout_s* (capped) so fast navigate
        paths stay sub-second.

        Navigate: re-nudge ui_command at ~0.6s and ~2.5s if still open so a
        lost take / busy poller still gets a second chance before timeout.

        Never overwrite a job that was already completed by the host (race fix).
        """
        started = time.time()
        # Allow sub-second waits for lightning navigate (do not force min 1s)
        deadline = started + max(0.05, float(timeout_s))
        nudges_done = 0
        while time.time() < deadline:
            if abort_check is not None:
                try:
                    if abort_check():
                        # Do not cancel if already done
                        job = self._read(job_id)
                        if job and job.status in ("done", "error", "cancelled"):
                            return job
                        self.cancel(job_id)
                        job = self._read(job_id)
                        if job:
                            return job
                        break
                except Exception:
                    pass
            job = self._read(job_id)
            if job is None:
                time.sleep(poll_s)
                continue
            if job.status in ("done", "error", "cancelled"):
                return job
            # Re-issue ui_command for navigate if host has not completed yet.
            # Covers: take without complete, busy main thread, dropped poll.
            elapsed = time.time() - started
            if job.action == "navigate" and job.status in ("pending", "running"):
                if nudges_done == 0 and elapsed >= 0.6:
                    cmd = self.peek_ui_command()
                    ui_for_job = (
                        isinstance(cmd, dict)
                        and str(cmd.get("job_id") or "") == str(job_id)
                    )
                    if not ui_for_job:
                        self.renudge_ui_for_job(job)
                    nudges_done = 1
                elif nudges_done == 1 and elapsed >= 2.5:
                    self.renudge_ui_for_job(job)
                    nudges_done = 2
            # Fail fast only when no UI command is outstanding for this job
            if (
                job.status == "pending"
                and unclaimed_timeout_s is not None
                and elapsed >= max(0.5, unclaimed_timeout_s)
            ):
                cmd = self.peek_ui_command()
                ui_for_job = (
                    isinstance(cmd, dict)
                    and str(cmd.get("job_id") or "") == str(job_id)
                )
                if not ui_for_job:
                    # Final re-read — host may have just completed
                    job2 = self._read(job_id)
                    if job2 and job2.status in ("done", "error", "cancelled"):
                        return job2
                    job.status = "error"
                    job.error = (
                        f"host did not claim job within {unclaimed_timeout_s:.0f}s "
                        "(Desktop poller offline or not authenticated)"
                    )
                    job.payload = _scrub_retained_payload(job.payload)
                    with self._lock:
                        # Only write error if still open
                        cur = self._read(job_id)
                        if cur and cur.status in ("pending", "running"):
                            self._write(job)
                            self.mark_host_dead()
                            return job
                        if cur:
                            return cur
                    return job
            time.sleep(poll_s)

        # Timeout path — never clobber a successful host completion.
        job = self._read(job_id)
        if job is None:
            return ComputerJob(
                id=job_id,
                action="?",
                status="error",
                error="job missing",
            )
        if job.status in ("done", "error", "cancelled"):
            return job
        # Grace scales with timeout so lightning navigate (0.45s) stays fast.
        if grace_s is None:
            if job.action == "navigate":
                grace_s = min(2.5, max(0.05, float(timeout_s) * 0.25))
            else:
                grace_s = min(0.5, max(0.05, float(timeout_s) * 0.1))
        grace_deadline = time.time() + max(0.0, float(grace_s))
        while time.time() < grace_deadline:
            time.sleep(0.02)
            job = self._read(job_id)
            if job and job.status in ("done", "error", "cancelled"):
                return job
        job = self._read(job_id)
        if job is None:
            return ComputerJob(
                id=job_id,
                action="?",
                status="error",
                error="job missing",
            )
        if job.status in ("done", "error", "cancelled"):
            return job
        job.status = "error"
        job.error = f"timeout waiting for desktop host ({timeout_s:.1f}s)"
        job.payload = _scrub_retained_payload(job.payload)
        with self._lock:
            cur = self._read(job_id)
            if cur and cur.status in ("done", "error", "cancelled"):
                return cur
            if cur and cur.status in ("pending", "running"):
                # Re-apply scrub from current (may hold typed secrets)
                job.payload = _scrub_retained_payload(cur.payload)
                self._write(job)
                # Do NOT mark host dead for navigate — host often still alive
                # and will complete a moment later; mark_host_dead caused
                # false "offline" cascades.
                if job.action != "navigate":
                    self.mark_host_dead()
        return job

    def purge_old(self, *, max_age_s: float = 900.0) -> int:
        """Delete finished job files older than *max_age_s* (default 15 minutes)."""
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
        home = canonical_home(home_dir)
        if _bridge is None:
            _bridge = ComputerHostBridge(home_dir=home)
        # If a later caller uses a different home, prefer existing singleton
        # (must match the process that serves /api/computer/*).
        return _bridge
