"""Hive daughter records and return packets.

Daughters never speak to the owner. They report a compact packet to Remedy
(the mother). See the hive plan: foragers die on report; posts (PR2) persist.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

CADENCE_FORAGER = "forager"
CADENCE_POST = "post"
CADENCES = frozenset({CADENCE_FORAGER, CADENCE_POST})

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_REPORTED = "reported"
STATUS_ASLEEP = "asleep"
STATUS_BLOCKED = "blocked"
STATUS_RETIRED = "retired"
STATUS_CANCELLED = "cancelled"

# Compact broadcast — not a transcript (Anthropic: multi-agent dumps explode).
PACKET_CHAR_CAP = 2_000
PACKET_LIST_CAP = 8
JOURNAL_NOTES_CAP = 12


def _now() -> str:
    return datetime.now(UTC).isoformat()


def hive_session_id(daughter_id: str) -> str:
    """Turn-context session id. Never a sidebar ChatSession."""
    did = str(daughter_id or "").strip()
    return f"hive_{did}" if did and not did.startswith("hive_") else did or "hive_"


def is_hive_session_id(session_id: str | None) -> bool:
    return str(session_id or "").startswith("hive_")


@dataclass
class ReturnPacket:
    """Waggle dance: what the mother is allowed to see."""

    goal: str = ""
    done: bool = False
    outcome: str = ""
    evidence: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(_cap_packet(self))

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ReturnPacket:
        d = raw if isinstance(raw, dict) else {}
        def _list(key: str) -> list[Any]:
            got = d.get(key)
            return got if isinstance(got, list) else []

        ev = _list("evidence")
        arts = _list("artifacts")
        blocks = _list("blockers")
        qs = _list("open_questions")
        try:
            conf = float(d.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        return _cap_packet(
            cls(
                goal=str(d.get("goal") or "")[:500],
                done=bool(d.get("done")),
                outcome=str(d.get("outcome") or d.get("summary") or ""),
                evidence=[str(x)[:240] for x in ev if str(x).strip()],
                artifacts=[str(x)[:400] for x in arts if str(x).strip()],
                blockers=[str(x)[:240] for x in blocks if str(x).strip()],
                open_questions=[str(x)[:240] for x in qs if str(x).strip()],
                confidence=max(0.0, min(1.0, conf)),
            )
        )

    def as_mother_text(self) -> str:
        """Plain lines for the mother's tool result (still packet-capped)."""
        lines = [
            f"done={self.done}",
            f"goal={self.goal}",
            f"outcome={self.outcome}",
        ]
        if self.evidence:
            lines.append("evidence=" + "; ".join(self.evidence))
        if self.artifacts:
            lines.append("artifacts=" + "; ".join(self.artifacts))
        if self.blockers:
            lines.append("blockers=" + "; ".join(self.blockers))
        if self.open_questions:
            lines.append("open_questions=" + "; ".join(self.open_questions))
        lines.append(f"confidence={self.confidence:.2f}")
        text = "\n".join(lines)
        if len(text) > PACKET_CHAR_CAP:
            return text[: PACKET_CHAR_CAP - 1] + "…"
        return text


def _cap_list(items: list[str]) -> list[str]:
    out = [str(x).strip()[:240] for x in items if str(x).strip()]
    return out[:PACKET_LIST_CAP]


def _cap_packet(pkt: ReturnPacket) -> ReturnPacket:
    pkt.evidence = _cap_list(pkt.evidence)
    pkt.artifacts = _cap_list(pkt.artifacts)
    pkt.blockers = _cap_list(pkt.blockers)
    pkt.open_questions = _cap_list(pkt.open_questions)
    pkt.outcome = str(pkt.outcome or "")[:800]
    raw = pkt.as_mother_text()
    if len(raw) > PACKET_CHAR_CAP:
        pkt.outcome = pkt.outcome[: max(80, 800 - (len(raw) - PACKET_CHAR_CAP))]
    return pkt


def cap_packet_dict(raw: dict[str, Any]) -> dict[str, Any]:
    return ReturnPacket.from_dict(raw).to_dict()


def append_journal(daughter: HiveDaughter, packet: ReturnPacket) -> None:
    """Stigmergy: compact notes the next pulse (and the mother) can see."""
    j = dict(daughter.journal or {})
    notes = [n for n in (j.get("notes") or []) if isinstance(n, dict)]
    notes.append(
        {
            "at": _now(),
            "outcome": str(packet.outcome or "")[:240],
            "done": bool(packet.done),
            "blockers": [str(b)[:120] for b in (packet.blockers or [])[:4]],
        }
    )
    j["notes"] = notes[-JOURNAL_NOTES_CAP:]
    try:
        count = int(j.get("pulse_count") or 0)
    except (TypeError, ValueError):
        count = 0
    j["pulse_count"] = count + 1
    j["last_pulse_at"] = _now()
    j["charter"] = daughter.goal[:800]
    daughter.journal = j


def assign_charter(daughter: HiveDaughter, goal: str) -> None:
    """Replace a standing post's job. Next pulse uses the new charter."""
    g = str(goal or "").strip()[:800]
    if not g:
        return
    daughter.goal = g
    j = dict(daughter.journal or {})
    j["charter"] = g
    j["assigned_at"] = _now()
    notes = [n for n in (j.get("notes") or []) if isinstance(n, dict)]
    notes.append({"at": _now(), "outcome": f"reassigned: {g[:200]}", "done": False, "blockers": []})
    j["notes"] = notes[-JOURNAL_NOTES_CAP:]
    daughter.journal = j
    daughter.updated_at = _now()


def packet_from_outcome(
    goal: str,
    text: str,
    *,
    aborted: bool = False,
) -> ReturnPacket:
    """Build a packet from daughter prose (or a JSON object if she emitted one)."""
    blob = (text or "").strip()
    if aborted:
        return ReturnPacket(
            goal=goal[:500],
            done=False,
            outcome="cancelled",
            blockers=["cancelled: mother or owner stopped this forage"],
            confidence=0.0,
        )
    if blob.startswith("{") and "}" in blob:
        import json

        try:
            parsed = json.loads(blob[blob.find("{") : blob.rfind("}") + 1])
            if isinstance(parsed, dict) and (
                "done" in parsed or "outcome" in parsed or "goal" in parsed
            ):
                parsed.setdefault("goal", goal)
                return ReturnPacket.from_dict(parsed)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    done = False
    low = blob.lower()
    if low.startswith("done") or "\ndone=true" in low or low.startswith("done=true"):
        done = True
    return ReturnPacket(
        goal=goal[:500],
        done=done,
        outcome=blob[:800] or "no outcome",
        confidence=0.6 if blob else 0.0,
    )


@dataclass
class HiveDaughter:
    """One daughter Remedy hired. Forager or standing post."""

    id: str
    cadence: str = CADENCE_FORAGER
    status: str = STATUS_PENDING
    goal: str = ""
    session_id: str = ""
    parent_session_id: str = ""
    budget_steps: int = 8
    packet: dict[str, Any] | None = None
    project_path: str = ""
    approval_mode: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Standing post (PR2)
    pulse_s: int = 0
    next_pulse_at: str = ""
    journal: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> HiveDaughter:
        cadence = str(raw.get("cadence") or CADENCE_FORAGER).strip().lower()
        if cadence not in CADENCES:
            cadence = CADENCE_FORAGER
        did = str(raw.get("id") or "").strip()
        sid = str(raw.get("session_id") or "").strip() or hive_session_id(did)
        try:
            budget = int(raw.get("budget_steps") or 8)
        except (TypeError, ValueError):
            budget = 8
        try:
            pulse = int(raw.get("pulse_s") or 0)
        except (TypeError, ValueError):
            pulse = 0
        pkt = raw.get("packet")
        journal = raw.get("journal")
        return cls(
            id=did,
            cadence=cadence,
            status=str(raw.get("status") or STATUS_PENDING),
            goal=str(raw.get("goal") or "")[:800],
            session_id=sid,
            parent_session_id=str(raw.get("parent_session_id") or ""),
            budget_steps=max(1, min(16, budget)),
            packet=pkt if isinstance(pkt, dict) else None,
            project_path=str(raw.get("project_path") or ""),
            approval_mode=str(raw.get("approval_mode") or ""),
            created_at=str(raw.get("created_at") or _now()),
            updated_at=str(raw.get("updated_at") or _now()),
            pulse_s=max(0, pulse),
            next_pulse_at=str(raw.get("next_pulse_at") or ""),
            journal=journal if isinstance(journal, dict) else {},
        )

    def roster_line(self) -> dict[str, Any]:
        """Advanced inspector row — no transcripts."""
        pkt = ReturnPacket.from_dict(self.packet) if self.packet else None
        return {
            "id": self.id,
            "cadence": self.cadence,
            "status": self.status,
            "goal": self.goal[:240],
            "done": bool(pkt.done) if pkt else False,
            "outcome": (pkt.outcome[:200] if pkt else ""),
            "blockers": list(pkt.blockers) if pkt else [],
            "updated_at": self.updated_at,
            "pulse_s": self.pulse_s,
            "pulse_count": int((self.journal or {}).get("pulse_count") or 0),
            "next_pulse_at": self.next_pulse_at,
        }
