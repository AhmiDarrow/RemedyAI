"""Job queue bridging Python tools ↔ desktop host (Tauri WebView2).

Browser-target actions that need the embed are enqueued here. The desktop
app claims jobs via HTTP and posts results. Desktop-target actions do not
use this queue (they run in-process via Win32).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SECRET_FIELD_TOKEN_RE = re.compile(
    r"(?i)(^|[^a-z])(pass|token|pin|otp)([^a-z]|$)"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _same_or_child_url(got: str, want: str) -> bool:
    """True when *got* is *want* or a same-host child path (redirect).

    Raw ``startswith`` on the full URL treats ``https://github.com`` as
    success for ``https://github.com/foo`` (and the reverse).
    """
    if not got or not want:
        return False
    if got == want:
        return True
    from urllib.parse import urlsplit

    a = urlsplit(got)
    b = urlsplit(want)
    host_a = (a.hostname or "").lower()
    host_b = (b.hostname or "").lower()
    if not host_a or host_a != host_b:
        return False
    pa = (a.path or "/").rstrip("/") or "/"
    pb = (b.path or "/").rstrip("/") or "/"
    if pa == pb:
        return True
    # Landed on a child of the requested path (common after trailing-slash redirect).
    return pa.startswith(pb.rstrip("/") + "/")


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


def _element_looks_like_secret_field(el: dict[str, Any]) -> bool:
    """True when an a11y/DOM element is likely a password/token field."""
    itype = str(
        el.get("type") or el.get("input_type") or el.get("itype") or ""
    ).lower()
    if itype == "password":
        return True
    autocomplete = str(
        el.get("autocomplete") or el.get("autoComplete") or ""
    ).lower()
    if any(
        k in autocomplete
        for k in (
            "password",
            "one-time-code",
            "otp",
            "cc-number",
            "cc-csc",
            "new-password",
            "current-password",
        )
    ):
        return True
    # name / id / aria / placeholder hints (pwd, pass, secret, token, …)
    blob = " ".join(
        str(el.get(k) or "")
        for k in (
            "name",
            "id",
            "label",
            "aria-label",
            "aria_label",
            "placeholder",
            "role",
            "title",
        )
    ).lower()
    if any(
        k in blob
        for k in (
            "password",
            "passwd",
            "passcode",
            "passphrase",
            "pwd",
            "secret",
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "auth_token",
            "bearer",
            "credential",
            "ssn",
            "cvv",
            "cvc",
        )
    ):
        return True
    # bare "pass" / "token" as whole-ish name tokens
    return bool(_SECRET_FIELD_TOKEN_RE.search(blob))


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
        str(
            e.get("type") or e.get("input_type") or e.get("itype") or ""
        ).lower()
        is_password = _element_looks_like_secret_field(e) or bool(
            e.get("value_redacted")
        )
        v = e.get("value")
        if is_password:
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
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir().resolve()
    except Exception:
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
    # Owning chat session (multi-tab abort must not cancel sibling tabs' jobs).
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ComputerJob:
        pl = dict(raw.get("payload") or {})
        sid = str(raw.get("session_id") or pl.get("session_id") or "").strip()
        return cls(
            id=str(raw.get("id") or ""),
            action=str(raw.get("action") or ""),
            payload=pl,
            status=str(raw.get("status") or "pending"),
            result=raw.get("result") if isinstance(raw.get("result"), dict) else None,
            error=raw.get("error"),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
            session_id=sid,
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
        # Last successful drive target (browser | desktop). Sticky so a game
        # launched with computer_app is not clicked in the Browser rail.
        self._last_drive_target: str = ""
        # Per-session copies — concurrent tabs must not steal each other's refs.
        self._last_elements_by_session: dict[str, list[dict[str, Any]]] = {}
        self._last_elements_target_by_session: dict[str, str] = {}
        self._last_drive_by_session: dict[str, str] = {}
        self._last_shot: dict[str, Any] = {}
        self._last_shot_by_session: dict[str, dict[str, Any]] = {}
        self._last_navigate_at: float = 0.0
        self._last_navigate_url: str = ""
        # True when last navigate returned before host confirmed page load.
        self._last_navigate_optimistic: bool = False
        self._last_navigate_at_by_session: dict[str, float] = {}
        self._last_navigate_url_by_session: dict[str, str] = {}
        self._last_navigate_optimistic_by_session: dict[str, bool] = {}
        # Desktop UI command (open Browser rail like Settings) — memory + disk
        self._ui_command: dict[str, Any] | None = None
        self._focused_session_id: str = ""
        # Same resolved home as jobs/ so ui_command and job JSON never diverge
        # when callers pass a relative or non-canonical home_dir.
        home = canonical_home(home_dir)
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

    def _session_key(self) -> str:
        try:
            from remedy.core.turn_context import current_session_id

            return str(current_session_id() or "").strip()
        except Exception:
            return ""

    def set_last_elements(
        self,
        elements: list[dict[str, Any]],
        *,
        target: str = "browser",
    ) -> None:
        els = list(elements or [])[:120]
        self._last_elements = els
        self._last_elements_target = target
        # Snapshot/find update refs only — do not steal last_drive_target
        sid = self._session_key()
        if sid:
            self._last_elements_by_session[sid] = els
            self._last_elements_target_by_session[sid] = target
            self._trim_session_maps()

    def _trim_session_maps(self) -> None:
        cap = 24
        if len(self._last_drive_by_session) <= cap:
            return
        extra = len(self._last_drive_by_session) - cap
        for k in list(self._last_drive_by_session.keys())[:extra]:
            self._last_drive_by_session.pop(k, None)
            self._last_elements_by_session.pop(k, None)
            self._last_elements_target_by_session.pop(k, None)
            self._last_navigate_at_by_session.pop(k, None)
            self._last_navigate_url_by_session.pop(k, None)
            self._last_navigate_optimistic_by_session.pop(k, None)

    def set_last_drive_target(self, target: str) -> None:
        t = (target or "").strip().lower()
        if t in ("browser", "desktop"):
            self._last_drive_target = t
            sid = self._session_key()
            if sid:
                self._last_drive_by_session[sid] = t

    def set_last_shot(
        self,
        *,
        origin: dict[str, Any] | None = None,
        width: int | None = None,
        height: int | None = None,
        path: str = "",
    ) -> None:
        info = {
            "origin": dict(origin or {}),
            "width": int(width or 0),
            "height": int(height or 0),
            "path": path or "",
        }
        self._last_shot = info
        sid = self._session_key()
        if sid:
            self._last_shot_by_session[sid] = info

    def last_shot(self) -> dict[str, Any]:
        sid = self._session_key()
        if sid and sid in self._last_shot_by_session:
            return dict(self._last_shot_by_session[sid])
        return dict(self._last_shot or {})

    def last_drive_target(self) -> str:
        sid = self._session_key()
        if sid:
            # Missing key = this tab has not driven yet (do not leak sibling).
            return str(self._last_drive_by_session.get(sid) or "").strip().lower()
        return str(self._last_drive_target or "").strip().lower()

    def mark_navigated(self, url: str = "", *, optimistic: bool = False) -> None:
        now = time.time()
        self._last_navigate_at = now
        if url:
            self._last_navigate_url = str(url)
        self._last_navigate_optimistic = bool(optimistic)
        sid = self._session_key()
        if sid:
            self._last_navigate_at_by_session[sid] = now
            if url:
                self._last_navigate_url_by_session[sid] = str(url)
            self._last_navigate_optimistic_by_session[sid] = bool(optimistic)
            self._trim_session_maps()

    def last_navigate_url(self) -> str:
        """Last rail URL this session navigated to (vault domain binding)."""
        sid = self._session_key()
        if sid:
            return str(self._last_navigate_url_by_session.get(sid) or "")
        return str(self._last_navigate_url or "")

    def clear_navigate_optimistic(self) -> None:
        self._last_navigate_optimistic = False
        sid = self._session_key()
        if sid:
            self._last_navigate_optimistic_by_session[sid] = False

    def _nav_at(self) -> float:
        sid = self._session_key()
        if sid:
            # Missing key = this tab has not navigated (do not leak sibling).
            return float(self._last_navigate_at_by_session.get(sid) or 0.0)
        return float(self._last_navigate_at or 0.0)

    def _nav_optimistic(self) -> bool:
        sid = self._session_key()
        if sid:
            return bool(self._last_navigate_optimistic_by_session.get(sid))
        return bool(self._last_navigate_optimistic)

    def navigate_needs_settle(self, *, max_age_s: float = 8.0) -> bool:
        """True if last open was optimistic and still recent (type/click should wait)."""
        if not self._nav_optimistic():
            return False
        at = self._nav_at()
        if at <= 0:
            return False
        return (time.time() - at) < float(max_age_s)

    def settle_after_navigate(self, *, min_s: float = 0.35, max_s: float = 1.2) -> float:
        """Sleep remaining settle time if a navigate just happened. Returns slept seconds."""
        at = self._nav_at()
        if at <= 0:
            return 0.0
        elapsed = time.time() - at
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
        sid = self._session_key()
        if sid:
            if sid not in self._last_elements_by_session:
                return None
            els = self._last_elements_by_session[sid]
        else:
            els = self._last_elements
        for el in els:
            if str(el.get("ref") or "").strip().lower() == r:
                return el
        return None

    def last_elements_info(self) -> dict[str, Any]:
        sid = self._session_key()
        if sid:
            els = self._last_elements_by_session.get(sid) or []
            tgt = self._last_elements_target_by_session.get(sid, "")
        else:
            els = self._last_elements
            tgt = self._last_elements_target
        return {
            "target": tgt,
            "count": len(els),
            "elements": list(els),
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
        return bool(self._last_claim_at > 0 and now - self._last_claim_at <= float(max_age_s))

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
        last: OSError | None = None
        for i in range(16):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as e:
                # Windows: dest can be briefly locked by wait() read_text / AV.
                last = e
                time.sleep(0.015 * (i + 1))
            except OSError as e:
                last = e
                if getattr(e, "winerror", None) not in (5, 32):
                    raise
                time.sleep(0.015 * (i + 1))
        if last is not None:
            raise last

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
            with contextlib.suppress(OSError):
                self._ui_path.write_text(
                    json.dumps(cmd, indent=2), encoding="utf-8"
                )

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

    def take_ui_command(self, session_id: str | None = None) -> dict[str, Any] | None:
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
            want = str(session_id or self._focused_session_id or "").strip()
            cmd_sid = str((cmd or {}).get("session_id") or "").strip()
            if want and cmd_sid and cmd_sid != want:
                return None
            self._ui_command = None
            try:
                if self._ui_path.is_file():
                    self._ui_path.unlink(missing_ok=True)
            except OSError:
                pass
            return cmd

    def enqueue(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> ComputerJob:
        pl = dict(payload or {})
        sid = str(session_id or pl.get("session_id") or "").strip()
        if sid:
            pl.setdefault("session_id", sid)
        job = ComputerJob(
            # Full uuid hex (128-bit) — do not truncate (a11y/job spoof surface).
            id=uuid.uuid4().hex,
            action=action,
            payload=pl,
            status="pending",
            session_id=sid,
        )
        with self._lock:
            self._write(job)
        # Always request rail open for browser actions (Desktop pops panel like Settings)
        raw_ui = pl.get("ui")
        ui: dict[str, Any] = raw_ui if isinstance(raw_ui, dict) else {}
        if action in (
            "navigate",
            "snapshot",
            "a11y",
            "page_text",
            "ready",
            "click",
            "type",
            "screenshot",
        ) or ui.get("open_browser"):
            cmd = {
                "action": "open_browser",
                "url": pl.get("url") or "",
                "job_id": job.id,
                "job_action": action,
            }
            if sid:
                cmd["session_id"] = sid
            self.set_ui_command(cmd)
        return job

    def set_focused_session(self, session_id: str | None) -> None:
        self._focused_session_id = str(session_id or "").strip()

    def claim_next(
        self,
        *,
        exclude_actions: set[str] | frozenset[str] | None = None,
        only_actions: set[str] | frozenset[str] | None = None,
        session_id: str | None = None,
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
        want = str(session_id or self._focused_session_id or "").strip()
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
                job_sid = str(
                    job.session_id or (job.payload or {}).get("session_id") or ""
                ).strip()
                if want and job_sid and job_sid != want:
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
            if job.status == "cancelled":
                return job
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
                with contextlib.suppress(Exception):
                    self.purge_old(max_age_s=900.0)
            return job

    def find_recent_success(
        self,
        *,
        action: str,
        url: str,
        max_age_s: float = 20.0,
        session_id: str | None = None,
        job_id: str | None = None,
    ) -> ComputerJob | None:
        """Find a recent successful job for the same action+URL (reconcile races)."""
        want = (url or "").strip().rstrip("/")
        if not want:
            return None
        want_sid = str(session_id or self._session_key() or "").strip()
        want_jid = str(job_id or "").strip()
        if want_jid:
            mine = self._read(want_jid)
            if (
                mine is not None
                and mine.action == action
                and mine.status == "done"
                and (mine.result or {}).get("ok", True)
            ):
                return mine
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
            if want_sid:
                js = str(
                    job.session_id
                    or (job.payload or {}).get("session_id")
                    or ""
                ).strip()
                if js != want_sid:
                    continue
            res = job.result or {}
            if not res.get("ok", True):
                continue
            got = str(
                res.get("url") or (job.payload or {}).get("url") or ""
            ).strip().rstrip("/")
            if _same_or_child_url(got, want):
                if best is None or (job.updated_at or "") > (best.updated_at or ""):
                    best = job
        return best

    def cancel(self, job_id: str) -> ComputerJob | None:
        """Cancel an open job. Never clobbers terminal status (done/error/cancelled).

        Host complete can race with wait() abort — success (and prior errors)
        must stick so we do not rewrite a finished click/navigate as cancelled.
        """
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            if job.status in ("done", "error", "cancelled"):
                return job
            job.status = "cancelled"
            job.error = "cancelled"
            job.payload = _scrub_retained_payload(job.payload)
            self._write(job)
            return job

    def cancel_pending_and_running(
        self,
        *,
        reason: str = "aborted",
        session_id: str | None = None,
    ) -> int:
        """Cancel open jobs (Stop generation). Returns count cancelled.

        When *session_id* is set, only jobs stamped with that session are
        cancelled so multi-tab concurrent streams do not clobber each other.
        Untagged legacy jobs (empty session_id) are cancelled only when
        *session_id* is omitted (global abort).
        """
        want = str(session_id or "").strip()
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
                if job.status not in ("pending", "running"):
                    continue
                if want:
                    job_sid = str(
                        job.session_id or (job.payload or {}).get("session_id") or ""
                    ).strip()
                    if job_sid != want:
                        continue
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
        cmd: dict[str, Any] = {
            "action": "open_browser",
            "url": url,
            "job_id": job.id,
            "job_action": job.action,
            "renudge": True,
        }
        sid = str(job.session_id or (job.payload or {}).get("session_id") or "").strip()
        if sid:
            cmd["session_id"] = sid
        self.set_ui_command(cmd)

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
                # Do NOT mark host dead for navigate / DOM jobs — host is often
                # still alive and mid-load (eval timeout ≠ offline). mark_host_dead
                # caused false "Desktop host not connected" cascades on page_text.
                act = (job.action or "").lower()
                if act not in (
                    "navigate",
                    "snapshot",
                    "a11y",
                    "page_text",
                    "ready",
                    "click",
                    "type",
                    "key",
                    "scroll",
                    "find",
                ):
                    self.mark_host_dead()
        return job

    def purge_old(
        self,
        *,
        max_age_s: float = 900.0,
        stale_open_ttl_s: float = 1800.0,
    ) -> int:
        """Delete finished job files older than *max_age_s* (default 15 minutes).

        Open work (pending/running) is never deleted — only terminal jobs and
        unreadable/corrupt JSON files past the age cutoff. Also purges aged
        desktop/browser screenshots under ``computer/shots/`` (S-COMP-02).

        **Stale-open scrub (S-COMP-03):** a pending/running job the host never
        claimed can carry a plaintext typed payload (passwords need plaintext
        while open). If such a job is older than *stale_open_ttl_s* (default 30
        minutes — a dead poller, not a slow one), it is expired: status →
        ``cancelled``, payload secrets scrubbed. Plaintext secrets must never
        sit on disk indefinitely.
        """
        cutoff = time.time() - max_age_s
        stale_cutoff = time.time() - max(float(stale_open_ttl_s), float(max_age_s))
        n = 0
        for path in list(self.root.glob("*.json")):
            try:
                mtime = path.stat().st_mtime
                if mtime >= cutoff:
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Corrupt leftover — safe to drop when aged out
                    path.unlink(missing_ok=True)
                    n += 1
                    continue
                if isinstance(raw, dict) and str(raw.get("status") or "") in (
                    "pending",
                    "running",
                ):
                    if mtime < stale_cutoff:
                        # Host is dead for this job — expire it and scrub the
                        # typed payload so no plaintext secret outlives the TTL.
                        job = ComputerJob.from_dict(raw)
                        job.status = "cancelled"
                        job.error = (
                            "expired: host never claimed/finished this job; "
                            "typed payload scrubbed after stale TTL"
                        )
                        job.payload = _scrub_retained_payload(job.payload)
                        # Write back to THIS file, not _path(job.id): a file
                        # whose JSON id ≠ filename must still get scrubbed here
                        # (otherwise the plaintext lingers forever).
                        try:
                            path.write_text(
                                json.dumps(job.to_dict()), encoding="utf-8"
                            )
                        except OSError:
                            pass
                        n += 1
                    continue
                path.unlink(missing_ok=True)
                n += 1
            except OSError:
                continue
        n += self.purge_old_shots(max_age_s=max_age_s)
        return n

    def purge_old_shots(self, *, max_age_s: float = 900.0) -> int:
        """Delete PNG/JPEG screenshots under computer/shots older than *max_age_s*.

        Screenshots are high-sensitivity (full desktop/page). Jobs already TTL
        via purge_old; shots used to accumulate indefinitely.
        """
        home = canonical_home(self.home_dir)
        shots = home / "computer" / "shots"
        # Only this process's home. A custom REMEDY_HOME / test tmp must
        # never delete the owner's default ~/.remedy/computer/shots.
        roots = [shots]
        cutoff = time.time() - float(max_age_s)
        n = 0
        for root in roots:
            if not root.is_dir():
                continue
            for path in list(root.iterdir()):
                try:
                    if not path.is_file():
                        continue
                    if path.suffix.lower() not in (
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".bmp",
                    ):
                        continue
                    if path.stat().st_mtime >= cutoff:
                        continue
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
