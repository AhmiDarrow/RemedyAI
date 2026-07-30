"""Evidence Ledger — append-only facts with stable IDs; delta context for the model.

Compression becomes ledger GC. Fail-closed: re-hydrate from disk rather than invent.
Secrets never enter the ledger body (redact at admit time).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# In-memory unit list bound (was 400 — tighter for long sessions).
MAX_EVIDENCE_UNITS = 240
# Fingerprint set can outlive unit drops; hard-bound to avoid unbounded RAM.
MAX_SEEN_FPS = MAX_EVIDENCE_UNITS * 2
# Path extractions per tool result (cheap parse, still O(n) on dump size).
MAX_PATHS_PER_ADMIT = 16
# L0/L1 lean: keep far fewer units when tools barely run.
MAX_EVIDENCE_UNITS_LEAN = 64
MAX_PATHS_PER_ADMIT_LEAN = 6

# Paths / decisions / test signals — high-value evidence tokens
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|~/|\./|\.\./|/)"
    r"[^\s\"'<>|]{2,200}"
    r"|(?:src|desktop|tests?|docs)/[^\s\"'<>|]{2,120}",
    re.I,
)
_DECISION_RE = re.compile(
    r"(?i)\b(decided|chose|using|will use|switch(?:ed)? to|approved|cancelled)\b"
)
_TEST_RE = re.compile(
    r"(?i)\b(\d+\s+passed|\d+\s+failed|ERROR|FAILED|ok\b|exit code\s*[:=]?\s*\d+)\b"
)
def _redact(text: str) -> str:
    from remedy.core.metabolism.redact import redact_text

    return redact_text(text or "")


def _eu_fingerprint(kind: str, body: str) -> str:
    h = hashlib.sha256(f"{kind}\0{body}".encode("utf-8", errors="replace")).hexdigest()
    return h[:16]


@dataclass
class EvidenceUnit:
    """One non-duplicate fact admitted to the ledger."""

    id: str
    kind: str  # path | decision | test | tool | map | note
    summary: str
    tool_name: str = ""
    tool_call_id: str = ""
    offload_path: str | None = None
    result_hash: str = ""
    tokens_est: int = 0
    ts: float = field(default_factory=time.time)
    session_id: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "summary": self.summary[:400],
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "offload_path": self.offload_path,
            "result_hash": self.result_hash,
            "tokens_est": self.tokens_est,
            "ts": self.ts,
        }


@dataclass
class EvidenceLedger:
    """Per-session append-only evidence log (in-memory + optional disk index)."""

    session_id: str = ""
    units: list[EvidenceUnit] = field(default_factory=list)
    seen_fps: set[str] = field(default_factory=set)
    evidence_units: int = 0  # count of EU admitted
    waste_tokens: int = 0  # tokens with 0 new EU
    last_model_eu_index: int = 0  # for delta since last model call
    _persist_cursor: int = 0  # units already flushed to disk (avoid thrash)
    _tool_batch_n: int = 0  # per-session throttle counter for metabolism writes
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def admit_tool_result(
        self,
        *,
        tool_name: str,
        content: str,
        tool_call_id: str = "",
        offload_path: str | None = None,
        success: bool = True,
        lean: bool = False,
    ) -> list[EvidenceUnit]:
        """Parse tool output into evidence units; skip duplicates and secrets.

        ``lean=True`` (L0/L1) keeps fewer path EUs and a tighter unit list so
        pure-chat sessions do not accumulate agency-sized ledgers.
        """
        raw = _redact(content or "")
        if not raw.strip():
            return []
        # Cap parse cost on huge tool dumps (paths/tests live in head)
        parse_src = raw if len(raw) <= 16_000 else (raw[:12_000] + "\n" + raw[-4_000:])
        result_hash = hashlib.sha256(
            raw.encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        tokens_est = max(1, len(raw) // 4)
        admitted: list[EvidenceUnit] = []
        kinds_bodies: list[tuple[str, str]] = []

        path_cap = MAX_PATHS_PER_ADMIT_LEAN if lean else MAX_PATHS_PER_ADMIT
        unit_cap = MAX_EVIDENCE_UNITS_LEAN if lean else MAX_EVIDENCE_UNITS
        paths = list(dict.fromkeys(_PATH_RE.findall(parse_src)))[:path_cap]
        for p in paths:
            kinds_bodies.append(("path", p[:240]))
        if _DECISION_RE.search(parse_src):
            line = parse_src.strip().split("\n", 1)[0][:200]
            kinds_bodies.append(("decision", line))
        if _TEST_RE.search(parse_src):
            m = _TEST_RE.search(parse_src)
            kinds_bodies.append(("test", (m.group(0) if m else "test")[:120]))
        # Always one tool summary EU
        first = parse_src.strip().split("\n", 1)[0][:160]
        kinds_bodies.append(
            (
                "tool",
                f"{tool_name}:{'ok' if success else 'fail'}:{first}",
            )
        )

        with self._lock:
            new_eu = 0
            for kind, body in kinds_bodies:
                fp = _eu_fingerprint(kind, body)
                if fp in self.seen_fps:
                    continue
                self.seen_fps.add(fp)
                eu = EvidenceUnit(
                    id=f"eu_{fp}",
                    kind=kind,
                    summary=body,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    offload_path=offload_path,
                    result_hash=result_hash,
                    tokens_est=tokens_est if kind == "tool" else max(1, len(body) // 4),
                    session_id=self.session_id,
                )
                self.units.append(eu)
                self.evidence_units += 1
                new_eu += 1
                admitted.append(eu)
            # Cap memory (unit list + fingerprint set)
            if len(self.units) > unit_cap:
                drop = len(self.units) - unit_cap
                self.units = self.units[drop:]
                self.last_model_eu_index = max(0, self.last_model_eu_index - drop)
            if len(self.seen_fps) > MAX_SEEN_FPS:
                # Rebuild from retained units so dedupe stays coherent
                self.seen_fps = {
                    _eu_fingerprint(u.kind, u.summary) for u in self.units
                }
            if new_eu == 0:
                self.waste_tokens += tokens_est
            return admitted

    def mark_model_call(self) -> None:
        with self._lock:
            self.last_model_eu_index = len(self.units)

    def delta_since_model(self, *, limit: int = 32) -> list[EvidenceUnit]:
        with self._lock:
            chunk = self.units[self.last_model_eu_index :]
            return list(chunk[-limit:])

    def pointer_block(self, *, limit: int = 16) -> str:
        """Compact system note: new evidence + offload pointers (no full dumps)."""
        delta = self.delta_since_model(limit=limit)
        if not delta:
            return ""
        lines = ["[Evidence delta — new facts since last model call]"]
        for eu in delta:
            ptr = f" offload={eu.offload_path}" if eu.offload_path else ""
            lines.append(f"- {eu.id} ({eu.kind}) {eu.summary[:180]}{ptr}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "session_id": self.session_id,
                "evidence_units": self.evidence_units,
                "unit_count": len(self.units),
                "waste_tokens": self.waste_tokens,
                "unique_fps": len(self.seen_fps),
                "last_model_eu_index": self.last_model_eu_index,
                "recent": [u.to_public() for u in self.units[-12:]],
            }

    def persist_index(self, home: Path | str | None = None) -> Path | None:
        """Write redacted index only (not full tool bodies). Fail soft.

        Appends only units not yet flushed (cursor-based) to avoid thrashing the
        same JSONL rows on every end-turn when the ledger is quiet.
        """
        try:
            root = Path(home).expanduser() if home else Path.home() / ".remedy"
            d = root / "evidence"
            d.mkdir(parents=True, exist_ok=True)
            sid = "".join(
                c for c in (self.session_id or "default") if c.isalnum() or c in "-_"
            )[:48]
            path = d / f"{sid or 'default'}.jsonl"
            with self._lock:
                start = max(0, int(self._persist_cursor or 0))
                # If memory was capped, cursor may be ahead of list — resync
                if start > len(self.units):
                    start = 0
                chunk = self.units[start:]
                if not chunk:
                    return path if path.is_file() else None
                # Cap single flush (safety)
                if len(chunk) > 40:
                    chunk = chunk[-40:]
                    start = len(self.units) - len(chunk)
                with path.open("a", encoding="utf-8") as f:
                    for u in chunk:
                        f.write(json.dumps(u.to_public(), ensure_ascii=False) + "\n")
                self._persist_cursor = len(self.units)
            return path
        except Exception:
            return None


_ledgers: dict[str, EvidenceLedger] = {}
_ledgers_lock = threading.Lock()


def get_evidence_ledger(session_id: str | None = None) -> EvidenceLedger:
    from remedy.core.metabolism.session_registry import registry_get

    key = (session_id or "").strip() or "_default"
    with _ledgers_lock:
        return registry_get(
            _ledgers, key, lambda: EvidenceLedger(session_id=key)
        )


def reset_evidence_ledger(session_id: str | None = None) -> None:
    key = (session_id or "").strip() or "_default"
    with _ledgers_lock:
        _ledgers.pop(key, None)
