"""What actually went wrong for Remedy — the trigger for self-improvement.

Self-improvement used to go *looking* for work: it read pytest's stale
lastfailed cache and picked whatever was in it. Its very first round targeted a
**network flake** — a test no code edit can ever fix — so it burned the round,
rolled back, and would have kept doing that forever.

This flips it: Remedy only tries to improve herself when she has actually hit
something. A fault here means a real fault during real work, with the context
to fix it.

Two rules keep it honest:

- **Environmental faults are not code bugs.** A provider 401, a dead network, a
  missing compiler — those are the world being the world. They are recorded (so
  she can explain herself) but marked ``environmental`` and never become
  self-improvement targets.
- **Faults that resist fixing stop being retried.** Every attempt is counted; a
  fault that has been tried ``MAX_FIX_ATTEMPTS`` times is parked.

On disk: ``~/.remedy/error_journal.json`` — atomic write under a lock.
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

_lock = threading.RLock()

STATUS_OPEN = "open"
STATUS_FIXED = "fixed"
STATUS_PARKED = "parked"  # tried enough times; stop burning rounds
STATUS_ENVIRONMENTAL = "environmental"  # real, but not ours to fix in code
STATUS_MODEL = "model"  # the LLM misbehaved — not a defect in Remedy's code

MAX_FIX_ATTEMPTS = 3
MAX_FAULTS = 200

# The world being the world — never a self-improvement target.
_ENVIRONMENTAL = re.compile(
    r"(?i)("
    r"connection\s*(refused|reset|aborted|error)|timed?\s*out|timeout|"
    r"temporary failure in name resolution|getaddrinfo|\bdns\b|"
    r"\bssl\b|certificate|unreachable|network is|\bsocket\b|"
    # HTTP status codes need context. Bare "401", "403", "429" matched any
    # traceback with a line number that happened to be one of them — so real
    # code bugs in files longer than 400 lines were filed as "the world being
    # the world" and never became self-improvement targets. That is the exact
    # failure this module exists to prevent, inverted.
    r"(?:http[/ ]?[\d.]*\s*)?(?:status|code|error)\s*[:=]?\s*(?:401|403|429)\b|"
    r"\bhttps?(?:[/ ][\d.]+)?\s+(?:401|403|429)\b|"
    r"\b(?:401|403|429)\s+(?:client\s+error|unauthorized|forbidden|too\s+many)|"
    r"rate.?limit|quota|insufficient_quota|"
    r"unauthorized|forbidden|invalid api key|authentication|"
    r"5\d\d\s+(server|bad gateway|service unavailable)|"
    r"is not recognized as an internal or external command|"
    r"no such file or directory:\s*'?(gcc|clang|node|npm|git)|"
    r"disk full|no space left|permission denied"
    r")"
)


def _home(home: str | Path | None = None) -> Path:
    base = home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    return Path(base).expanduser()


def _store_path(home: str | Path | None = None) -> Path:
    d = _home(home)
    with suppress(Exception):
        d.mkdir(parents=True, exist_ok=True)
    return d / "error_journal.json"


# The MODEL misbehaving is not a defect in Remedy's code. Editing this repo
# cannot make a provider stop emitting a malformed tool call or an empty answer,
# so these are recorded (she can explain herself) but never self-fix targets.
_MODEL_FAULT = re.compile(
    r"(?i)("
    r"no content|empty (answer|response|completion)|returned nothing|"
    r"finish_reason\s*[:=]\s*.?(length|content_filter)|max_tokens|"
    r"context (window|length) exceeded|too many tokens|token limit|"
    r"malformed (tool|function) call|invalid tool arguments|"
    r"could not parse (the )?(model|tool|json)|json decode error from (the )?model|"
    r"pseudo.?tool|tool_calls? (was|were) empty|hallucinat|"
    r"refus(ed|al)|i can'?t help with that|as an ai language model|"
    r"model (did not|didn'?t) (call|use) (a |the )?tool"
    r")"
)


def is_environmental(text: str) -> bool:
    """True when the failure is the environment, not Remedy's code."""
    return bool(_ENVIRONMENTAL.search(str(text or "")))


def is_model_fault(text: str) -> bool:
    """True when the LLM misbehaved rather than Remedy's code being wrong."""
    return bool(_MODEL_FAULT.search(str(text or "")))


def classify(text: str) -> str:
    """Which bucket a failure belongs in — only STATUS_OPEN is self-fixable."""
    blob = str(text or "")
    if is_environmental(blob):
        return STATUS_ENVIRONMENTAL
    if is_model_fault(blob):
        return STATUS_MODEL
    return STATUS_OPEN


def _signature(kind: str, exc_type: str, where: str, message: str) -> str:
    """Stable dedupe key: the same bug in the same place is one fault."""
    # Strip volatile bits (ids, paths, numbers) so repeats collapse.
    msg = re.sub(r"0x[0-9a-fA-F]+|\b\d+\b", "N", str(message or ""))[:160]
    raw = f"{kind}|{exc_type}|{where}|{msg}"
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]


@dataclass
class Fault:
    id: str
    kind: str  # turn_crash | tool_error | post_turn | build | other
    exc_type: str = ""
    message: str = ""
    where: str = ""  # module:line, or the tool name
    traceback: str = ""
    context: str = ""  # what she was doing
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    count: int = 1
    status: str = STATUS_OPEN
    fix_attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Fault:
        raw = raw or {}
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or "other"),
            exc_type=str(raw.get("exc_type") or ""),
            message=str(raw.get("message") or "")[:400],
            where=str(raw.get("where") or "")[:200],
            traceback=str(raw.get("traceback") or "")[:4000],
            context=str(raw.get("context") or "")[:400],
            first_seen=float(raw.get("first_seen") or time.time()),
            last_seen=float(raw.get("last_seen") or time.time()),
            count=int(raw.get("count") or 1),
            status=str(raw.get("status") or STATUS_OPEN),
            fix_attempts=int(raw.get("fix_attempts") or 0),
        )

    def is_targetable(self) -> bool:
        """Worth spending a self-improvement round on."""
        return self.status == STATUS_OPEN and self.fix_attempts < MAX_FIX_ATTEMPTS


def _read(home: str | Path | None) -> list[Fault]:
    p = _store_path(home)
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        return [Fault.from_dict(f) for f in (raw.get("faults") or [])]
    return []


def _write(home: str | Path | None, faults: list[Fault]) -> None:
    p = _store_path(home)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    keep = sorted(faults, key=lambda f: f.last_seen, reverse=True)[:MAX_FAULTS]
    with suppress(Exception):
        tmp.write_text(
            json.dumps({"faults": [f.to_dict() for f in keep]}, indent=2),
            encoding="utf-8",
        )
        os.replace(str(tmp), str(p))
        return
    with suppress(Exception):
        tmp.unlink()


def record_fault(
    kind: str,
    message: str,
    *,
    exc_type: str = "",
    where: str = "",
    traceback: str = "",
    context: str = "",
    home: str | Path | None = None,
    now: float | None = None,
) -> Fault | None:
    """Record something that actually went wrong. Repeats collapse and count up."""
    msg = str(message or "").strip()
    if not msg:
        return None
    n = time.time() if now is None else now
    blob = f"{msg}\n{traceback}"
    status = classify(blob)
    fid = _signature(kind, exc_type, where, msg)
    with _lock:
        faults = _read(home)
        for f in faults:
            if f.id == fid:
                f.count += 1
                f.last_seen = n
                # A fault marked fixed that reappears is open again.
                if f.status == STATUS_FIXED:
                    f.status = status
                    f.fix_attempts = 0
                _write(home, faults)
                return f
        fault = Fault(
            id=fid,
            kind=str(kind or "other"),
            exc_type=str(exc_type or "")[:120],
            message=msg[:400],
            where=str(where or "")[:200],
            traceback=str(traceback or "")[:4000],
            context=str(context or "")[:400],
            first_seen=n,
            last_seen=n,
            status=status,
        )
        faults.append(fault)
        _write(home, faults)
    return fault


def list_faults(
    *, status: str = "", limit: int = 50, home: str | Path | None = None
) -> list[Fault]:
    faults = _read(home)
    if status:
        faults = [f for f in faults if f.status == status]
    # Most-hit first: a fault the owner meets often matters most.
    faults.sort(key=lambda f: (f.count, f.last_seen), reverse=True)
    return faults[: max(1, int(limit))]


def open_faults(*, home: str | Path | None = None) -> list[Fault]:
    """Faults worth a self-improvement round, most frequent first."""
    return [f for f in list_faults(home=home) if f.is_targetable()]


def next_target_fault(*, home: str | Path | None = None) -> Fault | None:
    faults = open_faults(home=home)
    return faults[0] if faults else None


def note_fix_attempt(fault_id: str, *, home: str | Path | None = None) -> Fault | None:
    """Count an attempt; park the fault once it has resisted enough tries."""
    with _lock:
        faults = _read(home)
        for f in faults:
            if f.id == fault_id:
                f.fix_attempts += 1
                if f.fix_attempts >= MAX_FIX_ATTEMPTS:
                    f.status = STATUS_PARKED
                _write(home, faults)
                return f
    return None


def mark_fixed(fault_id: str, *, home: str | Path | None = None) -> Fault | None:
    with _lock:
        faults = _read(home)
        for f in faults:
            if f.id == fault_id:
                f.status = STATUS_FIXED
                _write(home, faults)
                return f
    return None


def clear_faults(*, home: str | Path | None = None) -> int:
    with _lock:
        n = len(_read(home))
        _write(home, [])
    return n


def record_exception(
    exc: BaseException,
    *,
    kind: str = "other",
    context: str = "",
    home: str | Path | None = None,
) -> Fault | None:
    """Convenience: record a live exception with its traceback and location."""
    import traceback as _tb

    tb = ""
    with suppress(Exception):
        tb = "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))[-4000:]
    where = ""
    with suppress(Exception):
        frames = _tb.extract_tb(exc.__traceback__)
        if frames:
            last = frames[-1]
            where = f"{Path(last.filename).name}:{last.lineno}"
    return record_fault(
        kind,
        str(exc) or type(exc).__name__,
        exc_type=type(exc).__name__,
        where=where,
        traceback=tb,
        context=context,
        home=home,
    )
